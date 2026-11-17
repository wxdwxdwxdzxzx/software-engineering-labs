"""SQLite storage: short connections, real transactions and durable constraints."""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .domain import BusinessError

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 phone TEXT UNIQUE, contact TEXT NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin'))
);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL REFERENCES users(id),
 title TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
 price_cents INTEGER NOT NULL CHECK(price_cents>0 AND price_cents<=99999999),
 state TEXT NOT NULL CHECK(state IN
 ('草稿','待审核','审核驳回','已发布','已下架','已预约','待交易','已售出','违规下架')),
 version INTEGER NOT NULL DEFAULT 1, submitted INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id),
 buyer_id INTEGER NOT NULL REFERENCES users(id),
 state TEXT NOT NULL CHECK(state IN ('待确认','待交易','已拒绝','已取消','超时取消','已完成')),
 created_at INTEGER NOT NULL, deadline INTEGER NOT NULL, finished_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_reservation ON reservations(product_id)
 WHERE state IN ('待确认','待交易');
CREATE TABLE IF NOT EXISTS reservation_requests (
 buyer_id INTEGER NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
 product_id INTEGER NOT NULL REFERENCES products(id),
 reservation_id INTEGER NOT NULL REFERENCES reservations(id),
 PRIMARY KEY(buyer_id,request_key)
);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id),
 reporter_id INTEGER NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT '待处理' CHECK(state IN ('待处理','已驳回','已核实')),
 created_at INTEGER NOT NULL, handled_at INTEGER
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
 report_id INTEGER NOT NULL UNIQUE REFERENCES reports(id), content TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
 product_id INTEGER, target TEXT NOT NULL, before_state TEXT NOT NULL,
 after_state TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS search_products ON products(state,category,price_cents,id);
CREATE INDEX IF NOT EXISTS seller_products ON products(seller_id,state,id);
CREATE INDEX IF NOT EXISTS reservation_owner ON reservations(buyer_id,id);
CREATE INDEX IF NOT EXISTS reservation_due ON reservations(state,deadline);
CREATE INDEX IF NOT EXISTS log_filter ON audit_logs(product_id,created_at);
CREATE INDEX IF NOT EXISTS log_action ON audit_logs(action,created_at);
CREATE TRIGGER IF NOT EXISTS protect_product_delete BEFORE DELETE ON products
 WHEN OLD.state!='草稿' OR OLD.submitted!=0
 BEGIN SELECT RAISE(ABORT,'submitted product cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS protect_reservation_delete BEFORE DELETE ON reservations
 BEGIN SELECT RAISE(ABORT,'history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS protect_report_delete BEFORE DELETE ON reports
 BEGIN SELECT RAISE(ABORT,'history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS protect_log_delete BEFORE DELETE ON audit_logs
 BEGIN SELECT RAISE(ABORT,'audit cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS protect_log_update BEFORE UPDATE ON audit_logs
 BEGIN SELECT RAISE(ABORT,'audit cannot be changed'); END;
PRAGMA user_version=1;
"""


class Store:
    """Each operation owns its connection; no cross-thread connection sharing."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5):
                raise BusinessError("数据库版本不兼容，请先备份并使用匹配版本的软件")
            if version == 0:
                conn.executescript(SCHEMA)
            self.migration_backup = None
            if version < 2:
                self._upgrade_v2(conn, existing=version == 1)
            if version < 3:
                self._upgrade_v3(conn, existing=version == 2)
            if version < 4:
                self._upgrade_v4(conn, existing=version == 3)
            if version < 5:
                from .migration5 import upgrade

                if version == 4:
                    self._backup(conn, 4)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if conn.execute("PRAGMA user_version").fetchone()[0] < 5:
                        upgrade(conn)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def _upgrade_v4(self, conn, *, existing):
        from .migration4 import upgrade

        if existing:
            self._backup(conn, 3)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] >= 4:
                conn.rollback()
                return
            upgrade(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _backup(self, conn, version):
        path = self.path.with_name(
            self.path.name + f".v{version}-backup-{uuid.uuid4().hex[:12]}.sqlite"
        )
        with sqlite3.connect(path) as backup:
            conn.backup(backup)
        self.migration_backup = path

    def _upgrade_v3(self, conn, *, existing):
        if existing:
            self._backup(conn, 2)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] >= 3:
                conn.rollback()
                return
            for table in ("products", "reservations"):
                for column in ("meeting_region", "meeting_point"):
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            conn.execute("ALTER TABLE products ADD COLUMN published_at INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "UPDATE products SET published_at=COALESCE("
                "(SELECT MAX(a.created_at) FROM audit_logs a WHERE a.product_id=products.id "
                "AND a.action='审核通过'),created_at)"
            )
            conn.execute(
                "CREATE TABLE product_photos (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,"
                "position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 2),"
                "content BLOB NOT NULL,thumbnail BLOB NOT NULL,UNIQUE(product_id,position))"
            )
            conn.execute(
                "CREATE TABLE meeting_places (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "owner_id INTEGER NOT NULL REFERENCES users(id),"
                "region TEXT NOT NULL,point TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX places_owner ON meeting_places(owner_id,id)")
            conn.execute(
                "CREATE TRIGGER protect_meeting_snapshot BEFORE UPDATE OF meeting_region,"
                "meeting_point ON reservations "
                "BEGIN SELECT RAISE(ABORT,'meeting snapshot is immutable'); END"
            )
            conn.execute("PRAGMA user_version=3")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _upgrade_v2(self, conn, *, existing):
        """Add metadata without deleting history; DDL/backfill commit together."""
        if existing:
            self._backup(conn, 1)
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Another process may have completed the same upgrade while we waited.
            if conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
                conn.rollback()
                return
            for table, column in [
                ("products", "review_reason TEXT NOT NULL DEFAULT ''"),
                ("products", "reviewer_id INTEGER REFERENCES users(id)"),
                ("products", "reviewed_at INTEGER"),
                ("reports", "handled_by INTEGER REFERENCES users(id)"),
                ("reports", "decision_reason TEXT NOT NULL DEFAULT ''"),
                ("reservations", "snapshot_title TEXT NOT NULL DEFAULT ''"),
                ("reservations", "snapshot_category TEXT NOT NULL DEFAULT ''"),
                ("reservations", "snapshot_description TEXT NOT NULL DEFAULT ''"),
                ("reservations", "snapshot_price_cents INTEGER"),
                ("reservations", "snapshot_seller_name TEXT NOT NULL DEFAULT ''"),
                ("reservations", "snapshot_buyer_name TEXT NOT NULL DEFAULT ''"),
                ("reservations", "snapshot_origin TEXT NOT NULL DEFAULT 'legacy_current'"),
            ]:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            conn.execute(
                "UPDATE reservations SET "
                "snapshot_title=(SELECT title FROM products WHERE id=product_id),"
                "snapshot_category=(SELECT category FROM products WHERE id=product_id),"
                "snapshot_description=(SELECT description FROM products WHERE id=product_id),"
                "snapshot_price_cents=(SELECT price_cents FROM products WHERE id=product_id),"
                "snapshot_seller_name=(SELECT u.username FROM products p JOIN users u "
                "ON u.id=p.seller_id WHERE p.id=product_id),"
                "snapshot_buyer_name=(SELECT username FROM users WHERE id=buyer_id)"
            )
            conn.execute(
                "CREATE TABLE report_requests (reporter_id INTEGER NOT NULL REFERENCES users(id),"
                "request_key TEXT NOT NULL,product_id INTEGER NOT NULL REFERENCES products(id),"
                "report_id INTEGER NOT NULL REFERENCES reports(id),"
                "PRIMARY KEY(reporter_id,request_key))"
            )
            conn.execute("CREATE INDEX report_pending ON reports(reporter_id,product_id,state)")
            conn.execute(
                "CREATE TABLE product_reviews (id INTEGER PRIMARY KEY,"
                "product_id INTEGER NOT NULL REFERENCES products(id),"
                "reviewer_id INTEGER NOT NULL REFERENCES users(id),approved INTEGER NOT NULL,"
                "reason TEXT NOT NULL,created_at INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TRIGGER protect_snapshot BEFORE UPDATE OF snapshot_title,snapshot_category,"
                "snapshot_description,snapshot_price_cents,snapshot_seller_name,"
                "snapshot_buyer_name,snapshot_origin ON reservations "
                "BEGIN SELECT RAISE(ABORT,'reservation snapshot is immutable'); END"
            )
            for action in ("DELETE", "UPDATE"):
                conn.execute(
                    f"CREATE TRIGGER protect_reviews_{action.lower()} BEFORE {action} "
                    "ON product_reviews BEGIN "
                    "SELECT RAISE(ABORT,'review history is immutable'); END"
                )
            conn.execute("PRAGMA user_version=2")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                raise BusinessError("数据库操作失败，本次修改未保存，请稍后重试") from exc
            except BaseException:
                conn.rollback()
                raise


def audit(conn, actor, action, product_id, target, before, after, now):
    """Append a log in the caller's transaction; never silently ignore failure."""
    conn.execute(
        "INSERT INTO audit_logs(actor,action,product_id,target,before_state,after_state,"
        "created_at) VALUES(?,?,?,?,?,?,?)",
        (
            str(actor),
            action,
            product_id,
            target,
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
            now,
        ),
    )


def product(conn, product_id):
    row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if row is None:
        raise BusinessError("商品不存在或已删除")
    return dict(row)


def active_reservation(conn, product_id):
    row = conn.execute(
        "SELECT * FROM reservations WHERE product_id=? AND state IN ('待确认','待交易')",
        (product_id,),
    ).fetchone()
    return dict(row) if row else None


def assert_consistent(conn, product_id):
    """Validate cross-table invariants before committing every product command."""
    item = product(conn, product_id)
    current = active_reservation(conn, product_id)
    expected = {"已预约": "待确认", "待交易": "待交易"}.get(item["state"])
    if (expected is None and current is not None) or (
        expected is not None and (current is None or current["state"] != expected)
    ):
        raise RuntimeError("product/reservation invariant violated")
    if current:
        delivery = conn.execute(
            "SELECT mode,phase FROM delivery_orders WHERE reservation_id=?", (current["id"],)
        ).fetchone()
        if delivery:
            phases = (
                ("待卖家确认",)
                if current["state"] == "待确认"
                else (
                    ("待发货", "已发货") if delivery["mode"] == "邮寄" else ("面交中", "待面交确认")
                )
            )
            if delivery["phase"] not in phases:
                raise RuntimeError("fulfillment/reservation invariant violated")
    if item["state"] == "已售出":
        count = conn.execute(
            "SELECT COUNT(*) FROM reservations WHERE product_id=? AND state='已完成'",
            (product_id,),
        ).fetchone()[0]
        if count != 1:
            raise RuntimeError("sold product needs exactly one completed reservation")

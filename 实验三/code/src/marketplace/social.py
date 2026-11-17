"""Account-bound favorites, recent browsing and seller subscriptions."""

from .base import Service
from .db import product
from .domain import BusinessError


def publicly_visible(item):
    return item["state"] in ("已发布", "已售出") and not item.get("listing_blocked", 0)


class Social(Service):
    def favorite(self, token, product_id, enabled=True):
        from .accounts import writable

        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            if not enabled:
                conn.execute(
                    "DELETE FROM favorites WHERE user_id=? AND product_id=?",
                    (user["id"], product_id),
                )
                return False
            writable(user)
            item = product(conn, product_id)
            if not publicly_visible(item):
                raise BusinessError("该商品不再公开，暂不能收藏")
            conn.execute(
                "INSERT OR IGNORE INTO favorites VALUES(?,?,?)",
                (user["id"], product_id, self.clock.now_ms()),
            )
            return True

    def viewed(self, token, product_id):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            item = product(conn, product_id)
            if not publicly_visible(item):
                return False
            conn.execute(
                "INSERT INTO browsing VALUES(?,?,?) ON CONFLICT(user_id,product_id) "
                "DO UPDATE SET viewed_at=excluded.viewed_at",
                (user["id"], product_id, self.clock.now_ms()),
            )
            return True

    def products(self, token, *, history=False, offset=0):
        if type(offset) is not int or not 0 <= offset <= 2**31:
            raise BusinessError("分页参数无效")
        table, field = ("browsing", "viewed_at") if history else ("favorites", "created_at")
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            # Retain a minimal marker for unavailable entries, without leaking private edits.
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT p.id,p.seller_id,p.state,"
                    "CASE WHEN p.state IN ('已发布','已售出') AND p.listing_blocked=0 THEN p.title "
                    "ELSE '商品暂不公开' END AS title,"
                    "CASE WHEN p.state IN ('已发布','已售出') AND p.listing_blocked=0 THEN "
                    "p.price_cents ELSE NULL "
                    "END AS price_cents,"
                    f"x.{field} AS viewed_at FROM {table} x JOIN products p ON p.id=x.product_id "
                    f"WHERE x.user_id=? ORDER BY x.{field} DESC,p.id DESC LIMIT 100 OFFSET ?",
                    (user["id"], offset),
                )
            ]

    def clear_history(self, token):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            conn.execute("DELETE FROM browsing WHERE user_id=?", (user["id"],))
            return True

    def follow(self, token, seller_id, enabled=True):
        from .accounts import writable

        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            if seller_id == user["id"]:
                raise BusinessError("不能关注自己")
            if not conn.execute(
                "SELECT 1 FROM users WHERE id=? AND role='user'", (seller_id,)
            ).fetchone():
                raise BusinessError("卖家不存在")
            if enabled:
                writable(user)
                conn.execute(
                    "INSERT OR IGNORE INTO follows VALUES(?,?,?)",
                    (user["id"], seller_id, self.clock.now_ms()),
                )
            else:
                conn.execute(
                    "DELETE FROM follows WHERE user_id=? AND seller_id=?", (user["id"], seller_id)
                )
            return bool(enabled)

    def follows(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT u.id AS seller_id,u.username,u.nickname,u.bio,u.region,u.avatar_thumb "
                    "FROM follows f JOIN users u ON u.id=f.seller_id WHERE f.user_id=? "
                    "ORDER BY f.created_at DESC,u.id DESC",
                    (user["id"],),
                )
            ]

    def flags(self, token, product_id, seller_id):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return {
                "favorite": bool(
                    conn.execute(
                        "SELECT 1 FROM favorites WHERE user_id=? AND product_id=?",
                        (user["id"], product_id),
                    ).fetchone()
                ),
                "following": bool(
                    conn.execute(
                        "SELECT 1 FROM follows WHERE user_id=? AND seller_id=?",
                        (user["id"], seller_id),
                    ).fetchone()
                ),
            }

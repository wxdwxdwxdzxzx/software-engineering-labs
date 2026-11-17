"""Participant-only product consultations and transaction-local business notices."""

from .base import Service
from .db import product
from .domain import BusinessError, clean_text


def notify(conn, recipients, key, content, product_id, now):
    for user_id in set(recipients):
        conn.execute(
            "INSERT OR IGNORE INTO "
            "business_notices(user_id,event_key,content,product_id,created_at) "
            "VALUES(?,?,?,?,?)",
            (user_id, key, content, product_id, now),
        )


def participants(conn, conversation_id, user_id):
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None or user_id not in (row["buyer_id"], row["seller_id"]):
        raise BusinessError("无权查看或发送此咨询会话")
    return dict(row)


class Messaging(Service):
    def open_for_order(self, token, reservation_id):
        """Existing trades retain participant communication after listing restrictions."""
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            row = conn.execute(
                "SELECT r.product_id,r.buyer_id,r.snapshot_title,p.seller_id "
                "FROM reservations r JOIN products p ON p.id=r.product_id WHERE r.id=?",
                (reservation_id,),
            ).fetchone()
            if row is None or user["id"] not in (row["buyer_id"], row["seller_id"]):
                raise BusinessError("无权访问该订单的交易咨询")
            conn.execute(
                "INSERT OR IGNORE INTO conversations(product_id,buyer_id,seller_id,created_at,"
                "snapshot_title) VALUES(?,?,?,?,?)",
                (
                    row["product_id"],
                    row["buyer_id"],
                    row["seller_id"],
                    self.clock.now_ms(),
                    row["snapshot_title"],
                ),
            )
            cid = conn.execute(
                "SELECT id FROM conversations WHERE product_id=? AND buyer_id=?",
                (row["product_id"], row["buyer_id"]),
            ).fetchone()[0]
            return {"id": cid, "product_id": row["product_id"]}

    def open(self, token, product_id):
        from .accounts import writable

        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            item = product(conn, product_id)
            existing = conn.execute(
                "SELECT id FROM conversations WHERE product_id=? AND buyer_id=?",
                (product_id, user["id"]),
            ).fetchone()
            if existing:
                return existing[0]
            writable(user)
            if item["seller_id"] == user["id"]:
                raise BusinessError("不能与自己发起咨询")
            if item["state"] != "已发布":
                raise BusinessError("当前商品不接受新的咨询，请刷新商品状态")
            if (
                conn.execute(
                    "SELECT status FROM users WHERE id=?", (item["seller_id"],)
                ).fetchone()[0]
                != "正常"
            ):
                raise BusinessError("卖家账号暂不接受新咨询，已有会话仍可沟通")
            return conn.execute(
                "INSERT INTO conversations(product_id,buyer_id,seller_id,created_at,"
                "snapshot_title) "
                "VALUES(?,?,?,?,?)",
                (product_id, user["id"], item["seller_id"], self.clock.now_ms(), item["title"]),
            ).lastrowid

    def conversations(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT c.id,c.product_id,c.snapshot_title AS title,p.state AS product_state,"
                    "b.nickname AS buyer_name,s.nickname AS seller_name,"
                    "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id "
                    "AND m.sender_id!=? AND m.read_at IS NULL) AS unread,"
                    "COALESCE((SELECT MAX(id) FROM messages m WHERE "
                    "m.conversation_id=c.id),0) AS latest "
                    "FROM conversations c JOIN products p ON p.id=c.product_id "
                    "JOIN users b ON b.id=c.buyer_id JOIN users s ON s.id=c.seller_id "
                    "WHERE c.buyer_id=? OR c.seller_id=? ORDER BY latest DESC,c.id DESC",
                    (user["id"], user["id"], user["id"]),
                )
            ]

    def send(self, token, conversation_id, text, request_key):
        text = clean_text(text, "消息", 2000)
        key = clean_text(request_key, "请求标识", 100)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            participants(conn, conversation_id, user["id"])
            previous = conn.execute(
                "SELECT id,conversation_id,content FROM messages "
                "WHERE sender_id=? AND request_key=?",
                (user["id"], key),
            ).fetchone()
            if previous:
                if previous["conversation_id"] != conversation_id or previous["content"] != text:
                    raise BusinessError("请求标识已用于另一条消息")
                return previous["id"]
            return conn.execute(
                "INSERT INTO messages(conversation_id,sender_id,content,created_at,request_key) "
                "VALUES(?,?,?,?,?)",
                (conversation_id, user["id"], text, self.clock.now_ms(), key),
            ).lastrowid

    def messages(self, token, conversation_id, *, before_id=None):
        if before_id is not None and (type(before_id) is not int or not 0 < before_id <= 2**63 - 1):
            raise BusinessError("消息分页参数无效")
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            participants(conn, conversation_id, user["id"])
            rows = list(
                conn.execute(
                    "SELECT m.id,m.sender_id,u.nickname AS "
                    "sender_name,m.content,m.created_at,m.read_at "
                    "FROM messages m JOIN users u ON u.id=m.sender_id WHERE conversation_id=? "
                    "AND m.id<? ORDER BY m.id DESC LIMIT 50",
                    (conversation_id, before_id or 2**63 - 1),
                )
            )
            # Only the displayed page is marked read; unseen older messages keep their status.
            conn.executemany(
                "UPDATE messages SET read_at=? WHERE id=? AND sender_id!=? AND read_at IS NULL",
                [(self.clock.now_ms(), r["id"], user["id"]) for r in rows],
            )
            return [dict(r) for r in reversed(rows)]

    def notices(self, token, *, offset=0):
        if type(offset) is not int or not 0 <= offset <= 2**31:
            raise BusinessError("通知分页参数无效")
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id,event_key,content,product_id,created_at,read_at FROM "
                    "business_notices "
                    "WHERE user_id=? ORDER BY id DESC LIMIT 100 OFFSET ?",
                    (user["id"], offset),
                )
            ]

    def read_notice(self, token, notice_id):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            row = conn.execute(
                "SELECT id FROM business_notices WHERE id=? AND user_id=?", (notice_id, user["id"])
            ).fetchone()
            if row is None:
                raise BusinessError("通知不存在或不属于当前账号")
            conn.execute(
                "UPDATE business_notices SET read_at=COALESCE(read_at,?) WHERE id=?",
                (self.clock.now_ms(), notice_id),
            )
            return True

    def unread(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            notices = conn.execute(
                "SELECT COUNT(*) FROM business_notices WHERE user_id=? AND read_at IS NULL",
                (user["id"],),
            ).fetchone()[0]
            messages = conn.execute(
                "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE (c.buyer_id=? OR c.seller_id=?) AND m.sender_id!=? AND m.read_at IS NULL",
                (user["id"], user["id"], user["id"]),
            ).fetchone()[0]
            return {"notices": notices, "messages": messages}

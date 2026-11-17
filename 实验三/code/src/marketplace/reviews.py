"""Reviews originate in a completed trade; moderation never invents credit scores."""

from .base import Service
from .db import audit
from .domain import BusinessError, clean_text
from .messaging import notify


class Reviews(Service):
    def submit(self, token, reservation_id, score, text):
        if type(score) is not int or not 1 <= score <= 5:
            raise BusinessError("评分请选择1至5分")
        text = clean_text(text, "评价内容", 1000)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            row = conn.execute(
                "SELECT r.state,r.buyer_id,p.seller_id,p.id AS product_id "
                "FROM reservations r JOIN products p ON p.id=r.product_id WHERE r.id=?",
                (reservation_id,),
            ).fetchone()
            if row is None or row["state"] != "已完成":
                raise BusinessError("只有已完成交易的双方可以评价")
            if user["id"] not in (row["buyer_id"], row["seller_id"]):
                raise BusinessError("不能评价不属于自己的交易")
            old = conn.execute(
                "SELECT id,score,content FROM trade_reviews WHERE reservation_id=? AND author_id=?",
                (reservation_id, user["id"]),
            ).fetchone()
            if old:
                if old["score"] == score and old["content"] == text:
                    return old["id"]
                raise BusinessError("本次交易已评价，每人只能提交一次")
            target = row["seller_id"] if user["id"] == row["buyer_id"] else row["buyer_id"]
            now = self.clock.now_ms()
            rid = conn.execute(
                "INSERT INTO trade_reviews(reservation_id,author_id,target_id,score,"
                "content,created_at) VALUES(?,?,?,?,?,?)",
                (reservation_id, user["id"], target, score, text, now),
            ).lastrowid
            audit(
                conn,
                user["id"],
                "提交交易评价",
                row["product_id"],
                f"review:{rid}",
                {},
                {"score": score},
                now,
            )
            notify(
                conn,
                [target],
                f"review:{rid}",
                "收到一条真实交易评价，可在公开主页查看。",
                row["product_id"],
                now,
            )
            return rid

    def public(self, token, user_id, *, offset=0):
        if type(offset) is not int or not 0 <= offset <= 2**31:
            raise BusinessError("评价分页参数无效")
        with self.store.connect() as conn:
            self.auth.require(conn, token)
            stats = conn.execute(
                "SELECT COUNT(*) AS count,AVG(score) AS average FROM trade_reviews "
                "WHERE target_id=? AND visible=1",
                (user_id,),
            ).fetchone()
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT r.id,r.score,r.content,r.created_at,u.nickname AS author_name "
                    "FROM trade_reviews r JOIN users u ON u.id=r.author_id "
                    "WHERE r.target_id=? AND r.visible=1 ORDER BY r.id DESC LIMIT 50 OFFSET ?",
                    (user_id, offset),
                )
            ]
            return {**dict(stats), "items": rows}

    def report(self, token, review_id, reason):
        reason = clean_text(reason, "评价举报理由", 1000)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            row = conn.execute(
                "SELECT visible FROM trade_reviews WHERE id=?", (review_id,)
            ).fetchone()
            if row is None or not row[0]:
                raise BusinessError("评价不存在或已经隐藏")
            conn.execute(
                "INSERT OR IGNORE INTO review_reports(review_id,reporter_id,reason,created_at) "
                "VALUES(?,?,?,?)",
                (review_id, user["id"], reason, self.clock.now_ms()),
            )
            return conn.execute(
                "SELECT id FROM review_reports WHERE review_id=? AND reporter_id=?",
                (review_id, user["id"]),
            ).fetchone()[0]

    def reports(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token)
            clause = "" if user["role"] == "admin" else "WHERE r.reporter_id=?"
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT r.*,v.content AS review_content FROM review_reports r "
                    f"JOIN trade_reviews v ON v.id=r.review_id {clause} ORDER BY r.id DESC",
                    () if user["role"] == "admin" else (user["id"],),
                )
            ]

    def resolve(self, token, report_id, hide, reason):
        reason = clean_text(reason, "处理理由", 1000)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "admin")
            row = conn.execute("SELECT * FROM review_reports WHERE id=?", (report_id,)).fetchone()
            if row is None or row["state"] != "待处理":
                raise BusinessError("举报不存在或已处理")
            now = self.clock.now_ms()
            result = "已核实" if hide else "已驳回"
            conn.execute(
                "UPDATE review_reports SET state=?,handled_by=?,handled_at=?,"
                "decision_reason=? WHERE id=?",
                (result, user["id"], now, reason, report_id),
            )
            if hide:
                conn.execute("UPDATE trade_reviews SET visible=0 WHERE id=?", (row["review_id"],))
            notify(
                conn,
                [row["reporter_id"]],
                f"review-report:{report_id}",
                f"评价举报{result}：{reason}",
                None,
                now,
            )
            audit(
                conn,
                user["id"],
                "处理评价举报",
                None,
                f"review-report:{report_id}",
                {"state": "待处理"},
                {"state": result, "reason": reason},
                now,
            )
            return result

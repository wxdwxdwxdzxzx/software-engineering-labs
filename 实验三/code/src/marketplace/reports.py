"""Moderation and notifications share the product transaction boundary."""

from .base import Service
from .db import active_reservation, audit
from .domain import BusinessError, clean_text
from .domain import ProductState as P
from .domain import ReservationState as R

# A valid existing report was filed while the item was public or actively reserved.
VIOLATION_CHANGES_PRODUCT = (P.PUBLISHED, P.RESERVED, P.TRADING, P.REVIEW, P.REJECTED)
VIOLATION_ALLOWED = (*VIOLATION_CHANGES_PRODUCT, P.OFFLINE, P.VIOLATION, P.SOLD)


class Reports(Service):
    """Report history is retained even when a completed product cannot be changed."""

    def report_product(self, token, product_id, version, reason, request_key=None, *, again=False):
        reason = clean_text(reason, "举报原因", 1000)
        if request_key is not None:
            request_key = clean_text(request_key, "请求标识", 100)

        def operation(conn, user, item, now):
            if item["seller_id"] == user["id"]:
                raise BusinessError("不能举报自己发布的商品")
            if request_key:
                previous = conn.execute(
                    "SELECT product_id,report_id FROM report_requests "
                    "WHERE reporter_id=? AND request_key=?",
                    (user["id"], request_key),
                ).fetchone()
                if previous:
                    if previous["product_id"] != product_id:
                        raise BusinessError("请求标识已用于其他商品")
                    return previous["report_id"]
            pending = conn.execute(
                "SELECT id FROM reports WHERE reporter_id=? AND product_id=? "
                "AND state='待处理' ORDER BY id LIMIT 1",
                (user["id"], product_id),
            ).fetchone()
            if pending:
                report_id = pending["id"]
            else:
                current = active_reservation(conn, product_id)
                participant = current and current["buyer_id"] == user["id"]
                completed = conn.execute(
                    "SELECT 1 FROM reservations WHERE product_id=? AND buyer_id=? "
                    "AND state='已完成'",
                    (product_id, user["id"]),
                ).fetchone()
                if item["state"] != P.PUBLISHED and not participant and not completed:
                    raise BusinessError("仅可举报已发布商品或本人当前预约的商品，请刷新")
                self.check_version(item, version)
                last = conn.execute(
                    "SELECT reason FROM reports WHERE reporter_id=? AND product_id=? "
                    "ORDER BY id DESC LIMIT 1",
                    (user["id"], product_id),
                ).fetchone()
                if last and (not again or reason == last["reason"]):
                    raise BusinessError(
                        "前次举报已处理；如有新的事实，请选择再次举报并填写新的理由"
                    )
                report_id = self._insert_report(conn, user, product_id, reason, now)
            if request_key:
                conn.execute(
                    "INSERT INTO report_requests VALUES(?,?,?,?)",
                    (user["id"], request_key, product_id, report_id),
                )
            return report_id

        # Idempotent retries are identified before rejecting a stale page version.
        return self.execute(token, product_id, None, operation)

    @staticmethod
    def _insert_report(conn, user, product_id, reason, now):
        cur = conn.execute(
            "INSERT INTO reports(product_id,reporter_id,reason,created_at,snapshot_title) "
            "VALUES(?,?,?,?,(SELECT title FROM products WHERE id=?))",
            (product_id, user["id"], reason, now, product_id),
        )
        audit(
            conn,
            user["id"],
            "提交举报",
            product_id,
            f"report:{cur.lastrowid}",
            {},
            {"report": "待处理"},
            now,
        )
        return cur.lastrowid

    def resolve_report(self, token, report_id, version, violation, reason=None):
        reason = clean_text(
            reason if reason is not None else ("核实属实" if violation else "未发现违规"),
            "处理理由",
            1000,
        )
        with self.store.connect() as conn:
            row = conn.execute("SELECT product_id FROM reports WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise BusinessError("举报记录不存在")
            product_id = row[0]

        def operation(conn, user, item, now):
            report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
            if report["state"] != "待处理":
                raise BusinessError("举报已经处理，请勿重复提交")
            if violation and item["state"] not in VIOLATION_ALLOWED:
                raise BusinessError("当前终态不能被覆盖，举报保留待处理，请人工核对")
            result = "已核实" if violation else "已驳回"
            current = active_reservation(conn, product_id)
            from .delivery import IN_TRANSIT, order_row

            order = order_row(conn, current["id"]) if current else None
            in_transit = order and order["phase"] in IN_TRANSIT
            if violation and in_transit:
                conn.execute("UPDATE products SET listing_blocked=1 WHERE id=?", (product_id,))
                self.change(
                    conn,
                    user["id"],
                    "举报核实停售，保留在途履约",
                    item,
                    item["state"],
                    now,
                    current,
                    current["state"],
                )
            elif violation and item["state"] in VIOLATION_CHANGES_PRODUCT:
                current = active_reservation(conn, product_id)
                self.change(
                    conn,
                    user["id"],
                    "违规下架",
                    item,
                    P.VIOLATION,
                    now,
                    current,
                    R.CANCELLED if current else None,
                )
            conn.execute(
                "UPDATE reports SET state=?,handled_at=?,handled_by=?,decision_reason=? WHERE id=?",
                (result, now, user["id"], reason, report_id),
            )
            content = f"商品 #{product_id} 的举报处理结果：{result}。处理理由：{reason}。"
            if violation and in_transit:
                content += "商品已停止公开销售；在途订单、运单与必要沟通保留，请在履约详情协商。"
            elif violation and item["state"] == P.SOLD:
                content += "已售出争议已核实并结案，保留交易终态；平台不裁定退款或改写交易记录。"
            elif violation:
                content += (
                    "商品已停售，本次核实不恢复销售。"
                    if item["state"] in (P.OFFLINE, P.VIOLATION)
                    else "商品已违规下架，相关有效预约已终止。"
                )
            conn.execute(
                "INSERT INTO notifications(user_id,report_id,content,created_at) VALUES(?,?,?,?)",
                (report["reporter_id"], report_id, content, now),
            )
            from .messaging import notify

            notify(conn, [report["reporter_id"]], f"report:{report_id}", content, product_id, now)
            audit(
                conn,
                user["id"],
                "处理举报",
                product_id,
                f"report:{report_id}",
                {"report": "待处理"},
                {"report": result, "reason": reason, "handled_by": user["id"]},
                now,
            )
            return result

        return self.execute(token, product_id, version, operation, role="admin")

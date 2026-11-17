"""Reservation idempotency, deadline checks and paired state transitions."""

import hashlib
import json

from .base import Service
from .db import active_reservation, audit
from .domain import DAY_MS, BusinessError, clean_text
from .domain import ProductState as P
from .domain import ReservationState as R


def reservation(conn, reservation_id):
    row = conn.execute(
        "SELECT id,product_id,buyer_id,state,created_at,deadline,"
        "finished_at,proposed_time,agreed_at,decision_reaso"
        "n,snapshot_title,snapshot_category,snapshot_description,snapshot_price_cents,"
        "snapshot_seller_name,snapshot_buyer_name,snapshot_origin FROM reservations WHERE id=?",
        (reservation_id,),
    ).fetchone()
    if row is None:
        raise BusinessError("预约记录不存在")
    result = dict(row)
    if result["state"] not in (R.WAITING, R.TRADING):
        result["proposed_time"] = ""
    return result


class Bookings(Service):
    """No read from a previous page can bypass an atomic current-state check."""

    def reserve(
        self,
        token,
        product_id,
        version,
        request_key,
        *,
        proposed_time="",
        delivery_mode=None,
        address_id=None,
    ):
        request_key = clean_text(request_key, "请求标识", 100)
        try:
            fingerprint = hashlib.sha256(
                json.dumps([delivery_mode, address_id, proposed_time], sort_keys=True).encode()
            ).hexdigest()
        except (TypeError, ValueError) as exc:
            raise BusinessError("交付参数格式无效") from exc

        def operation(conn, user, item, now):
            if item["seller_id"] == user["id"]:
                raise BusinessError("不能预约自己发布的商品")
            previous = conn.execute(
                "SELECT * FROM reservation_requests WHERE buyer_id=? AND request_key=?",
                (user["id"], request_key),
            ).fetchone()
            if previous:
                if previous["product_id"] != product_id:
                    raise BusinessError("请求标识已用于其他商品，请重新操作")
                if previous["fingerprint"] and previous["fingerprint"] != fingerprint:
                    raise BusinessError("重复预约内容不同，请在已有订单中申请更正")
                return {**reservation(conn, previous["reservation_id"]), "duplicate": True}
            current = active_reservation(conn, product_id)
            if current:
                if current["buyer_id"] != user["id"]:
                    raise BusinessError("手慢了，商品已被他人预约")
                conn.execute(
                    "INSERT INTO reservation_requests(buyer_id,request_"
                    "key,product_id,reservation_id,fingerprint) VALUES(?,?,?,?,?)",
                    (user["id"], request_key, product_id, current["id"], fingerprint),
                )
                return {**reservation(conn, current["id"]), "duplicate": True}
            if item["state"] != P.PUBLISHED:
                raise BusinessError("商品状态已变更，当前无法预约，请刷新页面")
            if (
                conn.execute(
                    "SELECT status FROM users WHERE id=?", (item["seller_id"],)
                ).fetchone()[0]
                != "正常"
            ):
                raise BusinessError("卖家账号暂不接受新预约，请选择其他商品")
            self.check_version(item, version)
            from .delivery import prepare_order
            from .handoff import meeting_time

            mode, freight, receiver = prepare_order(conn, user, item, delivery_mode, address_id)
            if item["listing_blocked"]:
                raise BusinessError("商品已停止接受新订单")
            suggested = meeting_time(proposed_time, now) if mode == "面交" else ""
            cur = conn.execute(
                "INSERT INTO reservations(product_id,buyer_id,state,created_at,deadline,"
                "snapshot_title,snapshot_category,snapshot_description,snapshot_price_cents,"
                "snapshot_seller_name,snapshot_buyer_name,snapshot_origin,"
                "meeting_region,meeting_point) "
                "VALUES(?,?,'待确认',?,?,?,?,?,?,?,?,'booking',?,?)",
                (
                    product_id,
                    user["id"],
                    now,
                    now + DAY_MS,
                    item["title"],
                    item["category"],
                    item["description"],
                    item["price_cents"],
                    conn.execute(
                        "SELECT username FROM users WHERE id=?", (item["seller_id"],)
                    ).fetchone()[0],
                    user["username"],
                    item["meeting_region"],
                    item["meeting_point"],
                ),
            )
            rid = cur.lastrowid
            conn.execute(
                "INSERT INTO delivery_orders(reservation_id,mode,ph"
                "ase,goods_cents,freight_cents,receiver_json) VALUES(?,?,'待卖家确认',?,?,?)",
                (rid, mode, item["price_cents"], freight, receiver),
            )
            conn.execute(
                "UPDATE reservations SET proposed_time=?,meeting_note=? WHERE id=?",
                (suggested, item["meeting_note"], rid),
            )
            conn.execute(
                "INSERT INTO reservation_photos SELECT ?,position,content,thumbnail "
                "FROM product_photos WHERE product_id=?",
                (rid, product_id),
            )
            from .messaging import notify

            notify(
                conn,
                [user["id"], item["seller_id"]],
                f"reservation:{rid}:created",
                f"商品 #{product_id} 已创建预约 #{rid}，等待卖家在24小时内处理。",
                product_id,
                now,
            )
            conn.execute(
                "INSERT INTO reservation_requests(buyer_id,request_"
                "key,product_id,reservation_id,fingerprint) VALUES(?,?,?,?,?)",
                (user["id"], request_key, product_id, rid, fingerprint),
            )
            conn.execute(
                "UPDATE products SET state='已预约',version=version+1 WHERE id=?", (product_id,)
            )
            audit(
                conn,
                user["id"],
                "创建预约",
                product_id,
                f"reservation:{rid}",
                {"product": P.PUBLISHED, "reservation": None},
                {"product": P.RESERVED, "reservation": R.WAITING, "reservation_id": rid},
                now,
            )
            return {**reservation(conn, rid), "duplicate": False}

        return self.execute(token, product_id, None, operation)

    def act_reservation(
        self, token, reservation_id, version, action, *, reason="", agreed_time=None
    ):
        from .delivery import Deliveries, order_row

        with self.store.connect() as conn:
            delivery = order_row(conn, reservation_id)
        if delivery and not delivery["legacy"]:
            return Deliveries(self.store, self.auth, self.clock).booking_action(
                token, reservation_id, version, action, reason=reason, agreed_time=agreed_time
            )
        reason = clean_text(reason, "处理原因", 500, 0)
        mapping = {
            "agree": (R.WAITING, P.TRADING, R.TRADING, "卖家同意预约"),
            "reject": (R.WAITING, P.PUBLISHED, R.REJECTED, "卖家拒绝预约"),
            "cancel_waiting": (R.WAITING, P.PUBLISHED, R.CANCELLED, "买家取消预约"),
            "cancel_trade": (R.TRADING, P.PUBLISHED, R.CANCELLED, "取消交易"),
            "complete": (R.TRADING, P.SOLD, R.COMPLETE, "确认交易完成"),
        }
        if action not in mapping:
            raise BusinessError("不支持的预约操作")
        with self.store.connect() as conn:
            product_id = reservation(conn, reservation_id)["product_id"]

        def authorize(conn, user, item):
            current = reservation(conn, reservation_id)
            seller_only = action in ("agree", "reject", "complete")
            allowed = (
                item["seller_id"] == user["id"]
                if seller_only
                else (
                    user["id"] == current["buyer_id"]
                    or (action == "cancel_trade" and user["id"] == item["seller_id"])
                )
            )
            if not allowed:
                raise BusinessError("权限不足，不能处理该预约")

        def operation(conn, user, item, now):
            current = reservation(conn, reservation_id)
            old_r, new_p, new_r, label = mapping[action]
            if current["state"] != old_r:
                raise BusinessError("预约状态已变更，请刷新后选择对应操作")
            active = active_reservation(conn, product_id)
            if active is None or active["id"] != reservation_id:
                raise BusinessError("该预约已结束，不能影响新的预约")
            if action == "agree":
                from .handoff import meeting_time

                selected = current["proposed_time"] if agreed_time is None else agreed_time
                selected = meeting_time(selected, now)
                conn.execute(
                    "UPDATE reservations SET agreed_time=?,agreed_at=? WHERE id=?",
                    (selected, now, reservation_id),
                )
            if action in ("reject", "cancel_waiting", "cancel_trade"):
                conn.execute(
                    "UPDATE reservations SET decision_reason=? WHERE id=?",
                    (reason or label, reservation_id),
                )
            self.change(conn, user["id"], label, item, new_p, now, current, new_r)
            return reservation(conn, reservation_id)

        return self.execute(
            token, product_id, version, operation, authorize=authorize, allow_restricted=True
        )

    def contacts(self, token, reservation_id):
        """Keep the original contact-only API for existing callers."""
        return self.handoff(token, reservation_id)["contact"]

    def handoff(self, token, reservation_id):
        """Read private data only for the two current trading participants."""
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            user = self.auth.require(conn, token, "user")
            row = conn.execute(
                "SELECT r.buyer_id,p.seller_id,b.contact AS buyer_contact,"
                "s.contact AS seller_contact,r.meeting_region,"
                "r.meeting_point,r.meeting_note,r.agreed_time "
                "FROM reservations r JOIN products p ON p.id=r.product_id "
                "JOIN users b ON b.id=r.buyer_id JOIN users s ON s.id=p.seller_id "
                "WHERE r.id=? AND r.state='待交易' AND p.state='待交易' "
                "AND (r.buyer_id=? OR p.seller_id=?)",
                (reservation_id, user["id"], user["id"]),
            ).fetchone()
            if row is None:
                raise BusinessError("仅当前待交易双方可查看联系方式")
            from .delivery import order_row

            if order_row(conn, reservation_id)["mode"] != "面交":
                raise BusinessError("邮寄订单请在履约详情查看收件和运单信息")
            latest = conn.execute(
                "SELECT region,point,note,meeting_time FROM handoff_changes "
                "WHERE reservation_id=? AND state='已同意' ORDER BY id DESC LIMIT 1",
                (reservation_id,),
            ).fetchone()
            return {
                "time": latest["meeting_time"] if latest else row["agreed_time"],
                "note": latest["note"] if latest else row["meeting_note"],
                "contact": (
                    row["seller_contact"] if row["buyer_id"] == user["id"] else row["buyer_contact"]
                ),
                "region": latest["region"] if latest else row["meeting_region"],
                "point": latest["point"] if latest else row["meeting_point"],
            }

    def snapshot_photos(self, token, reservation_id):
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            user = self.auth.require(conn, token, "user")
            row = conn.execute(
                "SELECT r.buyer_id,p.seller_id FROM reservations r JOIN products p "
                "ON p.id=r.product_id WHERE r.id=?",
                (reservation_id,),
            ).fetchone()
            if row is None or user["id"] not in (row["buyer_id"], row["seller_id"]):
                raise BusinessError("无权查看预约图片快照")
            return [
                r[0]
                for r in conn.execute(
                    "SELECT content FROM reservation_photos "
                    "WHERE reservation_id=? ORDER BY position",
                    (reservation_id,),
                )
            ]

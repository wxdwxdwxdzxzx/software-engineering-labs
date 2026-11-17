"""Two-party confirmation for time/location changes without mutating booking snapshots."""

from datetime import datetime

from .base import Service
from .db import audit
from .domain import BUSINESS_TZ, BusinessError, clean_text
from .messaging import notify


def meeting_time(value, now):
    value = clean_text(value, "交接时间", 16, 0)
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=BUSINESS_TZ)
        if parsed.strftime("%Y-%m-%d %H:%M") != value or parsed.timestamp() * 1000 <= now:
            raise ValueError
    except (ValueError, OSError, OverflowError) as exc:
        raise BusinessError(
            "交接时间须为未来的 YYYY-MM-DD HH:MM（北京时间），也可留空协商"
        ) from exc
    return value


def trading(conn, user_id, reservation_id, *, readonly=False):
    row = conn.execute(
        "SELECT r.id,r.state,r.product_id,r.buyer_id,p.seller_id,p.state AS pstate "
        "FROM reservations r JOIN products p ON p.id=r.product_id WHERE r.id=?",
        (reservation_id,),
    ).fetchone()
    if row is None or user_id not in (row["buyer_id"], row["seller_id"]):
        raise BusinessError("无权处理该交接约定")
    from .delivery import order_row

    order = order_row(conn, reservation_id)
    phases = ("面交中", "待面交确认") if readonly else ("面交中",)
    if order and (order["mode"] != "面交" or order["phase"] not in phases):
        raise BusinessError("仅面交中可以调整交接；邮寄订单请使用履约详情")
    if row["state"] != "待交易" or row["pstate"] != "待交易":
        raise BusinessError("预约已结束或尚未同意，交接信息已隐藏")
    return dict(row)


class Handoff(Service):
    def propose(self, token, reservation_id, region, point, time, note=""):
        region = clean_text(region, "区域", 40)
        point = clean_text(point, "公共交接点", 120)
        note = clean_text(note, "交接说明", 300, 0)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            row = trading(conn, user["id"], reservation_id)
            now = self.clock.now_ms()
            time = meeting_time(time, now)
            old = conn.execute(
                "SELECT * FROM handoff_changes WHERE reservation_id=? AND state='待确认'",
                (reservation_id,),
            ).fetchone()
            if old:
                if (
                    old["proposer_id"],
                    old["region"],
                    old["point"],
                    old["meeting_time"],
                    old["note"],
                ) == (user["id"], region, point, time, note):
                    return old["id"]
                raise BusinessError("已有待确认的变更，请先由对方同意或拒绝")
            change_id = conn.execute(
                "INSERT INTO handoff_changes(reservation_id,"
                "proposer_id,region,point,note,meeting_time,"
                "created_at) VALUES(?,?,?,?,?,?,?)",
                (reservation_id, user["id"], region, point, note, time, now),
            ).lastrowid
            other = row["seller_id"] if row["buyer_id"] == user["id"] else row["buyer_id"]
            notify(
                conn,
                [other],
                f"handoff:{change_id}:propose",
                "交易对方提出交接变更，请核对并确认。",
                row["product_id"],
                now,
            )
            audit(
                conn,
                user["id"],
                "提议交接变更",
                row["product_id"],
                f"handoff:{change_id}",
                {},
                {"reservation_id": reservation_id, "private_fields_changed": True},
                now,
            )
            return change_id

    def changes(self, token, reservation_id):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            trading(conn, user["id"], reservation_id, readonly=True)
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM handoff_changes WHERE reservation_id=? ORDER BY id DESC",
                    (reservation_id,),
                )
            ]

    def decide(self, token, change_id, approve):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            change = conn.execute(
                "SELECT * FROM handoff_changes WHERE id=?", (change_id,)
            ).fetchone()
            if change is None:
                raise BusinessError("交接变更不存在")
            row = trading(conn, user["id"], change["reservation_id"])
            if change["proposer_id"] == user["id"]:
                raise BusinessError("变更需要由交易对方确认")
            if change["state"] != "待确认":
                raise BusinessError("变更已处理，请刷新查看最新约定")
            now = self.clock.now_ms()
            if approve:
                meeting_time(change["meeting_time"], now)
            result = "已同意" if approve else "已拒绝"
            conn.execute(
                "UPDATE handoff_changes SET state=?,handled_at=? WHERE id=?",
                (result, now, change_id),
            )
            notify(
                conn,
                [change["proposer_id"]],
                f"handoff:{change_id}:decide",
                f"交接变更{result}。请在待交易详情查看有效约定。",
                row["product_id"],
                now,
            )
            audit(
                conn,
                user["id"],
                "确认交接变更",
                row["product_id"],
                f"handoff:{change_id}",
                {"state": "待确认"},
                {"state": result},
                now,
            )
            return result

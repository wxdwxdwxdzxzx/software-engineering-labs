"""Atomic fulfillment, fixed freight and two-party delivery decisions."""

import hashlib
import json
import re
import uuid

from .addresses import snapshot_address
from .base import Service
from .bookings import reservation
from .domain import DAY_MS, BusinessError, clean_text
from .domain import ProductState as P
from .domain import ReservationState as R

METHODS = ("仅邮寄", "仅面交", "邮寄或面交")
IN_TRANSIT = ("已发货", "待面交确认")
ISSUE_TYPES = ("发货延迟", "运单有误", "未收到商品", "收货异常", "退回", "丢失", "面交异常")


def supported(method):
    return (
        ("邮寄",)
        if method == "仅邮寄"
        else (("邮寄", "面交") if method == "邮寄或面交" else ("面交",))
    )


def order_row(conn, reservation_id):
    row = conn.execute(
        "SELECT * FROM delivery_orders WHERE reservation_id=?", (reservation_id,)
    ).fetchone()
    return dict(row) if row else None


def prepare_order(conn, user, item, mode, address_id):
    options = supported(item["handoff_method"])
    if not mode and len(options) == 1:
        mode = options[0]
    if mode not in options:
        raise BusinessError("请选择卖家支持的邮寄或面交方式")
    if mode == "面交" and address_id is not None:
        raise BusinessError("面交无需提交收货地址，请清空地址选择")
    receiver = snapshot_address(conn, user["id"], address_id) if mode == "邮寄" else "{}"
    freight = item["freight_cents"] if mode == "邮寄" and not item["free_shipping"] else 0
    return mode, freight, receiver


def sync_terminal(conn, reservation_id, state):
    if state not in (R.WAITING, R.TRADING):
        conn.execute(
            "UPDATE delivery_orders SET phase=? WHERE reservation_id=? "
            "AND phase NOT IN ('退回结案','丢失结案')",
            (str(state), reservation_id),
        )
        conn.execute(
            "UPDATE address_changes SET state='已失效' WHERE reservation_id=? AND state='待确认'",
            (reservation_id,),
        )
        conn.execute(
            "UPDATE delivery_issues SET state='随订单终止' "
            "WHERE reservation_id=? AND state='协商中'",
            (reservation_id,),
        )


def effective_receiver(conn, order):
    row = conn.execute(
        "SELECT address_json FROM address_changes WHERE reservation_id=? "
        "AND state='已同意' ORDER BY id DESC LIMIT 1",
        (order["reservation_id"],),
    ).fetchone()
    return json.loads(row[0] if row else order["receiver_json"])


class Deliveries(Service):
    def _command(self, token, rid, version, action, payload, operation, request_key=None):
        key = clean_text(request_key or uuid.uuid4().hex, "请求标识", 100)
        fingerprint = hashlib.sha256(
            json.dumps([action, payload], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self.store.connect() as conn:
            pid = reservation(conn, rid)["product_id"]

        def authorize(conn, user, item):
            if user["id"] not in (reservation(conn, rid)["buyer_id"], item["seller_id"]):
                raise BusinessError("权限不足，无权处理他人的订单")

        def run(conn, user, item, now):
            prior = conn.execute(
                "SELECT fingerprint,result_json FROM delivery_requests "
                "WHERE reservation_id=? AND actor_id=? AND request_key=?",
                (rid, user["id"], key),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise BusinessError("重复请求的内容不同，请刷新后重试")
                return json.loads(prior[1])
            self.check_version(item, version)
            current = reservation(conn, rid)
            order = order_row(conn, rid)
            if order is None:
                raise BusinessError("订单履约信息不存在")
            result = operation(conn, user, item, current, order, now)
            conn.execute(
                "INSERT INTO delivery_requests VALUES(?,?,?,?,?)",
                (rid, user["id"], key, fingerprint, json.dumps(result)),
            )
            return result

        return self.execute(token, pid, None, run, authorize=authorize, allow_restricted=True)

    @staticmethod
    def _seller(user, item):
        if user["id"] != item["seller_id"]:
            raise BusinessError("权限不足，仅卖家可执行该操作")

    @staticmethod
    def _buyer(user, current):
        if user["id"] != current["buyer_id"]:
            raise BusinessError("权限不足，仅买家可执行该操作")

    @staticmethod
    def _phase(order, *allowed):
        if order["phase"] not in allowed:
            raise BusinessError(f"当前履约状态为{order['phase']}，请刷新后选择相应操作")

    def _event(
        self,
        conn,
        user,
        item,
        current,
        label,
        now,
        *,
        phase=None,
        terminal=None,
        field_changes=None,
    ):
        if phase:
            conn.execute(
                "UPDATE delivery_orders SET phase=? WHERE reservation_id=?", (phase, current["id"])
            )
        if terminal:
            new_p, new_r = terminal
        else:
            new_p, new_r = item["state"], current["state"]
        self.change(
            conn,
            user["id"],
            label,
            item,
            new_p,
            now,
            current,
            new_r,
            field_changes=field_changes,
        )
        return {
            **reservation(conn, current["id"]),
            "phase": order_row(conn, current["id"])["phase"],
        }

    def booking_action(
        self, token, rid, version, action, *, reason="", agreed_time=None, request_key=None
    ):
        reason = clean_text(reason, "处理原因", 500, 0)

        def operation(conn, user, item, current, order, now):
            if action in ("agree", "reject"):
                self._seller(user, item)
                self._phase(order, "待卖家确认")
                if current["state"] != R.WAITING:
                    raise BusinessError("预约已结束，请刷新")
            if action == "agree":
                if order["mode"] == "面交":
                    from .handoff import meeting_time

                    selected = current["proposed_time"] if agreed_time is None else agreed_time
                    selected = meeting_time(selected, now)
                else:
                    selected = ""
                conn.execute(
                    "UPDATE reservations SET agreed_time=?,agreed_at=? WHERE id=?",
                    (selected, now, rid),
                )
                phase = "待发货" if order["mode"] == "邮寄" else "面交中"
                return self._event(
                    conn,
                    user,
                    item,
                    current,
                    "卖家同意预约",
                    now,
                    phase=phase,
                    terminal=(P.TRADING, R.TRADING),
                )
            if action == "complete":
                self._seller(user, item)
                raise BusinessError(
                    "不能直接完成订单：邮寄须买家确认收货，面交须卖家发起后由买家确认"
                )
            if action == "reject":
                state, label = R.REJECTED, "卖家拒绝预约"
            elif action == "cancel_waiting":
                self._buyer(user, current)
                self._phase(order, "待卖家确认")
                state, label = R.CANCELLED, "买家取消预约"
            elif action == "cancel_trade":
                self._phase(order, "待发货", "面交中")
                state, label = R.CANCELLED, "发货或交接前取消"
            else:
                raise BusinessError("不支持的订单操作")
            conn.execute(
                "UPDATE reservations SET decision_reason=? WHERE id=?", (reason or label, rid)
            )
            target = P.VIOLATION if item["listing_blocked"] else P.PUBLISHED
            return self._event(conn, user, item, current, label, now, terminal=(target, state))

        return self._command(
            token, rid, version, action, [reason, agreed_time], operation, request_key
        )

    def ship(
        self, token, rid, version, carrier, tracking_number, sender_address_id, *, request_key=None
    ):
        carrier = clean_text(carrier, "快递公司", 60)
        tracking_number = clean_text(tracking_number, "运单号", 60)
        if not re.fullmatch(r"[A-Za-z0-9-]{5,60}", tracking_number):
            raise BusinessError("运单号应为5至60位字母、数字或短横线")

        def operation(conn, user, item, current, order, now):
            self._seller(user, item)
            self._phase(order, "待发货")
            if order["mode"] != "邮寄" or item["listing_blocked"]:
                raise BusinessError("当前订单不能发货")
            if conn.execute(
                "SELECT 1 FROM address_changes WHERE reservation_id=? AND state='待确认'", (rid,)
            ).fetchone():
                raise BusinessError("请先同意或拒绝收货地址更正，再发货")
            sender = snapshot_address(conn, user["id"], sender_address_id, "发货")
            conn.execute(
                "INSERT INTO shipment_events(reservation_id,actor_i"
                "d,kind,carrier,tracking_number,reason,sender_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rid, user["id"], "发货", carrier, tracking_number, "首次录入", sender, now),
            )
            conn.execute(
                "UPDATE delivery_orders SET shipped_at=? WHERE reservation_id=?", (now, rid)
            )
            return self._event(conn, user, item, current, "卖家录入运单并发货", now, phase="已发货")

        return self._command(
            token,
            rid,
            version,
            "ship",
            [carrier, tracking_number, sender_address_id],
            operation,
            request_key,
        )

    def correct_tracking(
        self, token, rid, version, carrier, tracking_number, reason, *, request_key=None
    ):
        carrier, tracking_number, reason = (
            clean_text(v, k, n)
            for v, k, n in (
                (carrier, "快递公司", 60),
                (tracking_number, "运单号", 60),
                (reason, "更正原因", 500),
            )
        )
        if not re.fullmatch(r"[A-Za-z0-9-]{5,60}", tracking_number):
            raise BusinessError("运单号应为5至60位字母、数字或短横线")

        def operation(conn, user, item, current, order, now):
            self._seller(user, item)
            self._phase(order, "已发货")
            old = conn.execute(
                "SELECT sender_json FROM shipment_events WHERE reservation_id=? "
                "ORDER BY id DESC LIMIT 1",
                (rid,),
            ).fetchone()
            conn.execute(
                "INSERT INTO shipment_events(reservation_id,actor_i"
                "d,kind,carrier,tracking_number,reason,sender_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rid, user["id"], "更正运单", carrier, tracking_number, reason, old[0], now),
            )
            return self._event(conn, user, item, current, "更正运单（原记录保留）", now)

        return self._command(
            token,
            rid,
            version,
            "correct_tracking",
            [carrier, tracking_number, reason],
            operation,
            request_key,
        )

    def confirm(self, token, rid, version, action, *, request_key=None):
        def operation(conn, user, item, current, order, now):
            if order["legacy"]:
                raise BusinessError("旧面交记录请按原交易界面处理")
            if conn.execute(
                "SELECT 1 FROM delivery_issues WHERE reservation_id=? AND state='协商中'", (rid,)
            ).fetchone():
                raise BusinessError("存在履约异常协商，请先确认处理结果或撤回异常")
            if action == "request_handoff":
                self._seller(user, item)
                self._phase(order, "面交中")
                conn.execute(
                    "UPDATE delivery_orders SET handoff_requested_at=? WHERE reservation_id=?",
                    (now, rid),
                )
                return self._event(
                    conn, user, item, current, "卖家发起面交完成确认", now, phase="待面交确认"
                )
            if action not in ("confirm_receipt", "confirm_handoff"):
                raise BusinessError("无效的完成操作")
            self._buyer(user, current)
            self._phase(order, "已发货" if action == "confirm_receipt" else "待面交确认")
            conn.execute(
                "UPDATE delivery_orders SET received_at=? WHERE reservation_id=?", (now, rid)
            )
            return self._event(
                conn,
                user,
                item,
                current,
                "买家确认收货" if action == "confirm_receipt" else "买家确认面交完成",
                now,
                terminal=(P.SOLD, R.COMPLETE),
            )

        return self._command(token, rid, version, action, [], operation, request_key)

    def propose_address(self, token, rid, version, address_id, *, request_key=None):
        def operation(conn, user, item, current, order, now):
            self._buyer(user, current)
            self._phase(order, "待卖家确认", "待发货")
            if order["mode"] != "邮寄":
                raise BusinessError("面交不使用收货地址")
            value = snapshot_address(conn, user["id"], address_id)
            if conn.execute(
                "SELECT 1 FROM address_changes WHERE reservation_id=? AND state='待确认'", (rid,)
            ).fetchone():
                raise BusinessError("已有地址更正待卖家确认")
            conn.execute(
                "INSERT INTO address_changes(reservation_id,prop"
                "oser_id,address_json,created_at) VALUES(?,?,?,?)",
                (rid, user["id"], value, now),
            )
            return self._event(conn, user, item, current, "买家提出收货地址更正", now)

        return self._command(token, rid, version, "address", [address_id], operation, request_key)

    def decide_address(self, token, rid, version, change_id, approve, *, request_key=None):
        def operation(conn, user, item, current, order, now):
            self._seller(user, item)
            self._phase(order, "待卖家确认", "待发货")
            row = conn.execute(
                "SELECT state FROM address_changes WHERE id=? AND reservation_id=?",
                (change_id, rid),
            ).fetchone()
            if row is None or row[0] != "待确认":
                raise BusinessError("地址更正已处理或不属于本订单")
            conn.execute(
                "UPDATE address_changes SET state=?,handled_at=?,handled_by=? WHERE id=?",
                ("已同意" if approve else "已拒绝", now, user["id"], change_id),
            )
            return self._event(
                conn,
                user,
                item,
                current,
                "同意收货地址更正" if approve else "拒绝收货地址更正",
                now,
            )

        return self._command(
            token,
            rid,
            version,
            "decide_address",
            [change_id, bool(approve)],
            operation,
            request_key,
        )

    def open_issue(self, token, rid, version, category, description, *, request_key=None):
        description = clean_text(description, "异常说明", 1000)
        if category not in ISSUE_TYPES:
            raise BusinessError("请选择有效的履约异常类别")

        def operation(conn, user, item, current, order, now):
            self._phase(order, "待发货", "面交中", "已发货", "待面交确认")
            old = conn.execute(
                "SELECT id FROM delivery_issues WHERE reservation_id=? AND state='协商中'", (rid,)
            ).fetchone()
            if old:
                return {"issue_id": old[0], "duplicate": True}
            issue_id = conn.execute(
                "INSERT INTO delivery_issues(reservation_id,opened_"
                "by,category,description,created_at) VALUES(?,?,?,?,?)",
                (rid, user["id"], category, description, now),
            ).lastrowid
            self._event(conn, user, item, current, "提出履约异常，请双方协商", now)
            return {"issue_id": issue_id}

        return self._command(
            token, rid, version, "issue", [category, description], operation, request_key
        )

    def resolve_issue(
        self,
        token,
        rid,
        version,
        issue_id,
        outcome,
        *,
        seller_received=False,
        approve=False,
        request_key=None,
    ):
        if outcome not in ("继续履约", "退回结案", "丢失结案", "撤回异常", "拒绝方案", "撤回方案"):
            raise BusinessError("请选择实际支持的处理结果")

        def operation(conn, user, item, current, order, now):
            row = conn.execute(
                "SELECT * FROM delivery_issues WHERE id=? AND reservation_id=?", (issue_id, rid)
            ).fetchone()
            if row is None or row["state"] != "协商中":
                raise BusinessError("异常记录已处理或不存在")
            self._phase(order, "待发货", "已发货", "面交中", "待面交确认")
            fields = ("id", "state", "proposal", "proposed_by", "seller_received")
            before = {key: row[key] for key in fields}
            before["issue_id"] = issue_id

            def record(label, **changes):
                updated = conn.execute(
                    "SELECT * FROM delivery_issues WHERE id=?", (issue_id,)
                ).fetchone()
                after = {key: updated[key] for key in fields}
                after["issue_id"] = issue_id
                return self._event(
                    conn,
                    user,
                    item,
                    current,
                    label,
                    now,
                    field_changes=(before, after),
                    **changes,
                )

            if outcome in ("撤回异常", "拒绝方案", "撤回方案") and approve:
                raise BusinessError("此操作无需确认对方方案，请重新选择处理方式")
            if outcome == "撤回异常":
                if row["opened_by"] != user["id"]:
                    raise BusinessError("仅异常提出人可以撤回")
                conn.execute(
                    "UPDATE delivery_issues SET state='已撤回',handled_at=?,handled_by=? "
                    "WHERE id=?",
                    (now, user["id"], issue_id),
                )
                return record("撤回履约异常")
            if outcome in ("拒绝方案", "撤回方案"):
                if not row["proposal"] or row["proposed_by"] is None:
                    raise BusinessError("当前没有待处理方案，请刷新后继续协商")
                own_proposal = row["proposed_by"] == user["id"]
                if (outcome == "拒绝方案" and own_proposal) or (
                    outcome == "撤回方案" and not own_proposal
                ):
                    raise BusinessError("仅方案接收方可拒绝，方案提出方可撤回自己的方案")
                conn.execute(
                    "UPDATE delivery_issues SET proposal='',proposed_by=NULL,seller_received=0 "
                    "WHERE id=?",
                    (issue_id,),
                )
                return record(
                    "拒绝履约异常处理方案" if outcome == "拒绝方案" else "撤回履约异常处理方案"
                )
            seller = user["id"] == item["seller_id"]
            if not approve:
                if row["proposed_by"] is not None:
                    raise BusinessError("已有处理方案，请先由接收方拒绝或提出方撤回方案后重提")
                conn.execute(
                    "UPDATE delivery_issues SET "
                    "proposal=?,proposed_by=?,seller_received=? WHERE id=?",
                    (outcome, user["id"], int(seller and seller_received), issue_id),
                )
                return record("提出履约异常处理结果")
            if row["proposed_by"] in (None, user["id"]) or row["proposal"] != outcome:
                raise BusinessError("处理结果须由另一方核对后确认")
            if outcome == "退回结案" and not (
                row["seller_received"] or (seller and seller_received)
            ):
                raise BusinessError("退回结案须卖家确认已收回实物，不能自动恢复销售")
            conn.execute(
                "UPDATE delivery_issues SET "
                "state='已协商结案',handled_at=?,handled_by=?,seller_received=? WHERE id=?",
                (
                    now,
                    user["id"],
                    int(bool(row["seller_received"] or (seller and seller_received))),
                    issue_id,
                ),
            )
            if outcome == "继续履约":
                return record("双方确认继续履约（未执行退款）")
            self._phase(order, "待发货", "已发货", "面交中", "待面交确认")
            blocked = outcome == "丢失结案" or item["listing_blocked"]
            if blocked:
                conn.execute("UPDATE products SET listing_blocked=1 WHERE id=?", (item["id"],))
            return record(
                f"双方确认{outcome}，未执行退款",
                phase=outcome,
                terminal=(P.VIOLATION if blocked else P.OFFLINE, R.CANCELLED),
            )

        return self._command(
            token,
            rid,
            version,
            "resolve_issue",
            [issue_id, outcome, bool(seller_received), bool(approve)],
            operation,
            request_key,
        )

    def detail(self, token, rid):
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            user = self.auth.require(conn, token, "user")
            current = reservation(conn, rid)
            item = conn.execute(
                "SELECT seller_id,version,state,listing_blocked FROM products WHERE id=?",
                (current["product_id"],),
            ).fetchone()
            if user["id"] not in (current["buyer_id"], item["seller_id"]):
                raise BusinessError("无权查看他人的订单或物流记录")
            order = order_row(conn, rid)
            result = {
                **order,
                "product_version": item["version"],
                "listing_blocked": item["listing_blocked"],
            }
            result.pop("receiver_json")
            result["reservation"] = current
            result["seller_id"] = item["seller_id"]
            result["total_cents"] = (
                None
                if order["goods_cents"] is None
                else order["goods_cents"] + order["freight_cents"]
            )
            buyer = user["id"] == current["buyer_id"]
            active = current["state"] == R.TRADING
            visible = buyer or active
            result["receiver"] = (
                effective_receiver(conn, order) if visible and order["mode"] == "邮寄" else None
            )
            result["address_changes"] = []
            for row in conn.execute(
                "SELECT * FROM address_changes WHERE reservation_id=? ORDER BY id DESC", (rid,)
            ):
                data = dict(row)
                value = json.loads(data.pop("address_json"))
                data["address"] = value if visible else None
                # Seller cannot inspect recipient data before agreeing.
                if buyer or active:
                    result["address_changes"].append(data)
            events = []
            for row in conn.execute(
                "SELECT * FROM shipment_events WHERE reservation_id=? ORDER BY id", (rid,)
            ):
                event = dict(row)
                sender = json.loads(event.pop("sender_json"))
                event["sender_region"] = f"{sender['city']} {sender['district']}"
                if user["id"] == item["seller_id"]:
                    event["sender"] = sender
                events.append(event)
            result["shipments"] = events
            result["issues"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM delivery_issues WHERE reservation_id=? ORDER BY id DESC", (rid,)
                )
            ]
            result["issue_events"] = []
            for log in conn.execute(
                "SELECT actor,action,before_state,after_state,created_at FROM audit_logs "
                "WHERE product_id=? ORDER BY id",
                (current["product_id"],),
            ):
                try:
                    after = json.loads(log["after_state"])
                    before = json.loads(log["before_state"])
                except (ValueError, TypeError):
                    continue
                if not isinstance(after, dict) or not isinstance(before, dict):
                    continue
                fields = after.get("fields", {})
                if (
                    after.get("reservation_id") != rid
                    or not isinstance(fields, dict)
                    or not fields.get("issue_id")
                ):
                    continue
                before = before.get("fields", {})
                result["issue_events"].append(
                    {
                        "action": log["action"],
                        "created_at": log["created_at"],
                        "actor": "买家" if log["actor"] == str(current["buyer_id"]) else "卖家",
                        "issue_id": fields["issue_id"],
                        "proposal_before": before.get("proposal"),
                        "proposal_after": fields.get("proposal"),
                    }
                )
            result["shipping_delayed"] = (
                order["phase"] == "待发货"
                and current["agreed_at"] is not None
                and self.clock.now_ms() >= current["agreed_at"] + 7 * DAY_MS
            )
            result["logistics_source"] = (
                "卖家手动录入；未接入物流轨迹查询。买家确认收货不等于快递签收。"
            )
            return result

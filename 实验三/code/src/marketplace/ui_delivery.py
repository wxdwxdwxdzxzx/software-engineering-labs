"""Address books and state-aware shipping/meetup actions for desktop users."""

import uuid
from tkinter import ttk

from .delivery import ISSUE_TYPES, supported
from .domain import BusinessError, display_money, display_time
from .ui_widgets import FormDialog, TextDialog


def format_address(value):
    if not value:
        return "按当前交易权限隐藏；地址簿变更不影响订单快照。"
    return (
        f"{value['recipient']} · {value['telephone']}\n"
        f"{value['province']} {value['city']} {value['district']} {value['detail']}"
    )


class DeliveryViews:
    def address_book(self, kind="收货"):
        from .ui import TablePanel

        win = TextDialog(self.root, kind + "地址簿")
        frame = ttk.Frame(win)
        frame.grid(row=0, column=0, sticky="nsew")
        rows = self.read(lambda: self.market.addresses.list(self.token, kind))
        TablePanel(
            frame,
            [
                ("recipient", "联系人", 110),
                ("city", "城市", 110),
                ("district", "区县", 110),
                ("detail", "详细地址", 220),
                ("is_default", "默认(1=是)", 85),
            ],
            rows,
            [
                ("查看/编辑", lambda r: self.address_editor(kind, r, win), lambda _r: True),
                (
                    "设为默认",
                    lambda r: self.address_book_command(
                        kind, win, lambda: self.market.addresses.default(self.token, r["id"], kind)
                    ),
                    lambda _r: True,
                ),
                (
                    "删除地址",
                    lambda r: self.confirm_command(
                        "删除地址簿记录？已保存订单快照不受影响。",
                        lambda: self.market.addresses.delete(self.token, r["id"], kind),
                    ),
                    lambda _r: True,
                ),
            ],
        )
        win.actions.add("新增地址", lambda: self.address_editor(kind, None, win))
        win.actions.add("刷新地址簿", lambda: self.reopen_addresses(kind, win))
        return win

    def reopen_addresses(self, kind, previous):
        previous.destroy()
        return self.address_book(kind)

    def address_book_command(self, kind, win, operation):
        if self.command(operation, "地址已更新") is not None:
            self.reopen_addresses(kind, win)

    def address_editor(self, kind, row=None, parent=None):
        specs = [
            ("recipient", "收件人" if kind == "收货" else "发件人", None),
            ("telephone", "联系电话（支持手机、固话及国际区号）", None),
            ("province", "省份", None),
            ("city", "城市", None),
            ("district", "区县", None),
            ("detail", "详细地址", None),
            ("is_default", "默认地址", ["是", "否"]),
        ]
        initial = {
            **(row or {}),
            "is_default": "是" if (row or {}).get("is_default", True) else "否",
        }

        def submit(v):
            v["is_default"] = v["is_default"] == "是"
            return self.market.addresses.save(
                self.token, kind=kind, address_id=(row or {}).get("id"), **v
            )

        return FormDialog(
            self,
            kind + "地址",
            specs,
            submit,
            initial=initial,
            on_saved=(lambda _: self.reopen_addresses(kind, parent)) if parent else None,
        )

    def delivery_reserve_dialog(self, product, parent):
        key = uuid.uuid4().hex
        modes = supported(product["handoff_method"])
        addresses = self.read(lambda: self.market.addresses.list(self.token))
        choices = {
            f"#{r['id']} {r['recipient']} · {r['city']}{r['district']} "
            f"{r['detail']} · {r['telephone']}": r["id"]
            for r in addresses
        }

        def submit(v):
            aid = choices.get(v["address"]) if v["delivery_mode"] == "邮寄" else None
            if v["delivery_mode"] == "邮寄" and aid is None:
                raise BusinessError("请先在个人中心添加收货地址，再重新打开预约窗口选择")
            return self.market.bookings.reserve(
                self.token,
                product["id"],
                product["version"],
                key,
                proposed_time=v["proposed_time"],
                delivery_mode=v["delivery_mode"],
                address_id=aid,
            )

        win = FormDialog(
            self,
            "确认交付方式与预约",
            [
                ("delivery_mode", "交付方式", modes),
                ("address", "邮寄收货地址（面交不需要）", ["不选择", *choices]),
                ("proposed_time", "面交建议时间（YYYY-MM-DD HH:MM，可留空）", None),
            ],
            submit,
            caption="确认预约",
            initial={"delivery_mode": modes[0], "address": next(iter(choices), "不选择")},
            on_saved=lambda _: self.reload_detail(parent, product["id"]),
        )
        goods = product["price_cents"]
        freight = 0 if product["free_shipping"] else product["freight_cents"]

        def totals(*_):
            postal = win.fields["delivery_mode"].get() == "邮寄"
            win.inputs["address"].configure(state="readonly" if postal else "disabled")
            win.inputs["proposed_time"].configure(state="disabled" if postal else "normal")
            fee = freight if postal else 0
            hint = "请核对收件信息。" if postal else "请核对面交约定；无需收货地址。"
            win.feedback.set(
                f"{hint}商品¥{display_money(goods)} + 运费¥{display_money(fee)} "
                f"= ¥{display_money(goods + fee)}；卖家需24小时内处理。"
            )

        win.fields["delivery_mode"].trace_add("write", totals)
        win.actions.add("管理收货地址", lambda: self.address_book("收货"))

        def reload_addresses():
            rows = self.read(lambda: self.market.addresses.list(self.token))
            choices.clear()
            choices.update(
                {
                    f"#{r['id']} {r['recipient']} · {r['city']}{r['district']} "
                    f"{r['detail']} · {r['telephone']}": r["id"]
                    for r in rows
                }
            )
            win.inputs["address"].configure(values=["不选择", *choices])
            if win.fields["address"].get() not in choices:
                win.fields["address"].set(next(iter(choices), "不选择"))

        win.actions.add("刷新地址选择", reload_addresses)
        totals()
        return win

    def order_text(self, token, rid):
        data = self.market.delivery.detail(token, rid)
        row = data["reservation"]
        text = (
            f"订单 #{rid} · 商品 #{row['product_id']} · {row['snapshot_title']}\n"
            f"交付：{data['mode']} · 履约：{data['phase']} · 预约：{row['state']}\n"
            f"商品¥{display_money(data['goods_cents'])} "
            f"+ 运费¥{display_money(data['freight_cents'])} "
            f"= 合计¥{display_money(data['total_cents'])}\n"
            f"发起：{display_time(row['created_at'])} · 同意：{display_time(row['agreed_at'])}\n"
            f"发货：{display_time(data['shipped_at'])} "
            f"· 买家确认：{display_time(data['received_at'])}\n"
            f"结束：{display_time(row['finished_at'])}\n"
            f"拒绝/取消原因：{row['decision_reason'] or '无'}\n\n"
        )
        if data["listing_blocked"]:
            text += "该商品已停止公开销售；已有履约及协商记录保留。\n"
        if data["mode"] == "邮寄":
            text += "收货信息（订单快照）\n" + format_address(data["receiver"]) + "\n\n"
            if data["shipping_delayed"]:
                text += "卖家同意后已超过7天未发货，可取消或提出发货延迟协商。\n"
            text += data["logistics_source"] + "\n"
            text += (
                "\n".join(
                    f"{r['kind']} · {r['carrier']} {r['tracking_number']}\n"
                    f"{display_time(r['created_at'])} · {r['reason']} "
                    f"· 发货地{r['sender_region']}"
                    for r in data["shipments"]
                )
                or "尚未录入运单。"
            )
            for c in data["address_changes"]:
                text += f"\n\n地址更正 #{c['id']} · {c['state']}\n{format_address(c['address'])}"
        else:
            text += "面交：卖家发起交接完成，买家核对实物后确认；新订单不能由卖家单方结束。\n"
            if data["legacy"]:
                text += "此为升级前历史面交，保留原卖家完成规则。\n"
            if row["state"] == "待交易":
                text += self.handoff_text(token, rid)
            else:
                text += "具体交接地点和联系方式已隐藏。"
        for issue in data["issues"]:
            text += (
                f"\n\n异常 #{issue['id']} · {issue['category']} · {issue['state']}\n"
                f"{issue['description']}\n处理提议：{issue['proposal'] or '尚无'}"
            )
        if data["issue_events"]:
            text += "\n\n异常处理记录："
            for event in data["issue_events"]:
                text += (
                    f"\n{display_time(event['created_at'])} · {event['actor']} · {event['action']}"
                    f" · 异常#{event['issue_id']}"
                    f"（方案：{event['proposal_before'] or '无'}"
                    f" → {event['proposal_after'] or '无'}）"
                )
        return text + "\n\n平台不执行线上支付或退款。未达成一致的履约异常保留协商记录。"

    def order_detail(self, row, previous=None):
        rid = row["id"]
        data = self.read(lambda: self.market.delivery.detail(self.token, rid))
        text = self.read(lambda: self.order_text(self.token, rid))
        win = previous or self.popup_text("订单履约与物流", "")
        win.content_text.configure(state="normal")
        win.content_text.delete("1.0", "end")
        win.content_text.insert("1.0", text)
        win.content_text.configure(state="disabled")
        if previous:
            for w in win.actions.controls:
                w.destroy()
            win.actions.controls.clear()
            win.actions.add("关闭", win.destroy)
        self.delivery_windows[win] = rid
        win.actions.add("刷新订单", lambda: self.order_detail(row, win))
        win.actions.add("联系交易对方", lambda: self.order_chat(rid))
        phase = data["phase"]
        v = data["product_version"]
        seller = self.user["id"] == row["seller_id"]
        service = self.market.delivery

        def refresh(_):
            if win.winfo_exists():
                self.order_detail(row, win)

        def action(label, fn):
            def send():
                result = self.command(fn, label + "已保存")
                if result is not None:
                    try:
                        refresh(result)
                    except Exception:
                        import logging

                        logging.exception("Order saved but follow-up refresh failed")
                        self.message("操作已保存；订单刷新失败，请关闭后重新打开", error=True)

            win.actions.add(label, send)

        if phase == "待发货" and seller:
            win.actions.add("填写运单并发货", lambda: self.ship_dialog(row, data, refresh))
        if phase == "已发货":
            if seller:
                win.actions.add("更正运单", lambda: self.tracking_dialog(row, data, refresh))
            else:
                key = uuid.uuid4().hex
                action(
                    "确认收到商品",
                    lambda: service.confirm(self.token, rid, v, "confirm_receipt", request_key=key),
                )
        if phase == "面交中" and seller and not data["legacy"]:
            action("发起面交完成", lambda: service.confirm(self.token, rid, v, "request_handoff"))
        if phase == "待面交确认" and not seller:
            key = uuid.uuid4().hex
            action(
                "确认面交完成",
                lambda: service.confirm(self.token, rid, v, "confirm_handoff", request_key=key),
            )
        if phase in ("待卖家确认", "待发货") and data["mode"] == "邮寄":
            if not seller:
                win.actions.add(
                    "申请更正收货地址", lambda: self.address_change_dialog(row, data, refresh)
                )
            for c in data["address_changes"]:
                if c["state"] == "待确认" and seller:
                    action(
                        "同意地址更正",
                        lambda c=c: service.decide_address(self.token, rid, v, c["id"], True),
                    )
                    action(
                        "拒绝地址更正",
                        lambda c=c: service.decide_address(self.token, rid, v, c["id"], False),
                    )
        if phase in ("待发货", "已发货", "面交中", "待面交确认"):
            win.actions.add("异常反馈与协商", lambda: self.issue_dialog(row, data, refresh))
        if phase == "已完成":
            win.actions.add("评价本次交易", lambda: self.review_dialog(row))
        if self.user["id"] == row["buyer_id"]:
            win.actions.add("举报商品/交易争议", lambda: self.report_dialog(row))
        return win

    def order_chat(self, rid):
        from .ui_features import ChatWindow

        result = self.command(
            lambda: self.market.messaging.open_for_order(self.token, rid), "已打开交易咨询"
        )
        if result:
            return ChatWindow(self, result["id"], result["product_id"])

    def ship_dialog(self, row, data, refresh):
        choices = {
            f"#{a['id']} {a['recipient']} · {a['city']}{a['district']} {a['detail']}": a["id"]
            for a in self.read(lambda: self.market.addresses.list(self.token, "发货"))
        }
        key = uuid.uuid4().hex

        def submit(v):
            sender = choices.get(v["sender"])
            if sender is None:
                raise BusinessError("请先添加自己的发货地址，再打开此窗口")
            return self.market.delivery.ship(
                self.token,
                row["id"],
                data["product_version"],
                v["carrier"],
                v["tracking_number"],
                sender,
                request_key=key,
            )

        win = FormDialog(
            self,
            "录入运单并确认发货",
            [
                ("carrier", "快递公司", None),
                ("tracking_number", "运单号（卖家录入，非真实轨迹查询）", None),
                ("sender", "本人发货地址", list(choices) or ["请先添加发货地址"]),
            ],
            submit,
            initial={"sender": next(iter(choices), "请先添加发货地址")},
            caption="确认已发货",
            on_saved=refresh,
        )
        win.actions.add("管理发货地址", lambda: self.address_book("发货"))
        return win

    def tracking_dialog(self, row, data, refresh):
        key = uuid.uuid4().hex
        latest = data["shipments"][-1]
        return FormDialog(
            self,
            "更正运单（原记录保留）",
            [
                ("carrier", "快递公司", None),
                ("tracking_number", "运单号", None),
                ("reason", "更正原因", None),
            ],
            lambda v: self.market.delivery.correct_tracking(
                self.token, row["id"], data["product_version"], **v, request_key=key
            ),
            initial=latest,
            on_saved=refresh,
        )

    def address_change_dialog(self, row, data, refresh):
        choices = {
            f"#{a['id']} {a['recipient']} {a['city']}{a['detail']} · {a['telephone']}": a["id"]
            for a in self.read(lambda: self.market.addresses.list(self.token))
        }

        def send(v):
            if v["address"] not in choices:
                raise BusinessError("请先添加收货地址")
            return self.market.delivery.propose_address(
                self.token, row["id"], data["product_version"], choices[v["address"]]
            )

        return FormDialog(
            self,
            "收货地址更正需卖家同意",
            [("address", "新地址（原快照不改写）", list(choices) or ["没有可用地址"])],
            send,
            initial={"address": next(iter(choices), "没有可用地址")},
            on_saved=refresh,
        )

    def issue_dialog(self, row, data, refresh):
        issue = next((x for x in data["issues"] if x["state"] == "协商中"), None)
        rid = row["id"]
        v = data["product_version"]
        service = self.market.delivery
        if not issue:
            key = uuid.uuid4().hex
            return FormDialog(
                self,
                "履约异常反馈",
                [
                    ("category", "异常类型", ISSUE_TYPES),
                    ("description", "情况说明（不填写完整地址和电话）", None),
                ],
                lambda x: service.open_issue(self.token, rid, v, **x, request_key=key),
                initial={"category": ISSUE_TYPES[0]},
                on_saved=refresh,
            )
        opener = issue["opened_by"] == self.user["id"]
        counterpart = bool(issue["proposal"] and issue["proposed_by"] != self.user["id"])
        if counterpart:
            choices = ["拒绝方案，继续协商", "接受方案"]
        elif issue["proposal"]:
            choices = ["撤回方案，继续协商"]
        else:
            choices = ["继续履约", "退回结案", "丢失结案"]
        if opener:
            choices.append("撤回异常")
        key = uuid.uuid4().hex

        def submit(values):
            choice = values["outcome"]
            approve = choice == "接受方案"
            outcome = {
                "接受方案": issue["proposal"],
                "拒绝方案，继续协商": "拒绝方案",
                "撤回方案，继续协商": "撤回方案",
            }.get(choice, choice)
            return service.resolve_issue(
                self.token,
                rid,
                v,
                issue["id"],
                outcome,
                approve=approve,
                seller_received=values["seller_received"] == "是",
                request_key=key,
            )

        return FormDialog(
            self,
            "履约异常协商",
            [
                ("outcome", "处理方式（当前方案：" + (issue["proposal"] or "尚无") + "）", choices),
                ("seller_received", "卖家是否已收回实物（仅退回结案使用）", ["否", "是"]),
            ],
            submit,
            caption="提交选择",
            initial={"outcome": choices[0], "seller_received": "否"},
            on_saved=refresh,
        )

"""Desktop pages for profiles, social lists, messaging and transaction details."""

import logging
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

from .domain import BusinessError, display_time
from .ui_delivery import DeliveryViews
from .ui_extras import PhotoGallery, PhotoPicker
from .ui_theme import BODY, TITLE
from .ui_widgets import ActionBar, FormDialog


class ChatWindow(tk.Toplevel):
    """Incremental background reads never reconstruct or clear the input field."""

    def __init__(self, app, conversation_id, product_id):
        super().__init__(app.root)
        self.app, self.cid, self.token = app, conversation_id, app.token
        self.title(f"商品 #{product_id} 的咨询")
        self.geometry("720x600")
        self.minsize(520, 440)
        self.transient(app.root)
        self.closed, self.future, self.before, self.timer = False, None, None, None
        self.rows = []
        self.feedback = tk.StringVar()
        footer = ttk.Frame(self, padding=12)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, text="发送文字咨询（最多2000字）").pack(anchor="w")
        self.input = tk.Text(footer, height=3, wrap="word", font=BODY)
        self.input.pack(fill="x", pady=6)
        ttk.Label(footer, textvariable=self.feedback, wraplength=470).pack(anchor="w")
        bar = ActionBar(footer)
        bar.pack(fill="x")
        self.send_button = bar.add("发送消息", self.send)
        bar.add("返回最新", lambda: self.page(None))
        bar.add("更早消息", self.older)
        bar.add("商品详情", lambda: app.product_detail({"id": product_id}))
        bar.add("关闭", self.destroy)
        content = ttk.Frame(self, padding=12)
        content.pack(fill="both", expand=True)
        self.history = tk.Text(
            content,
            height=5,
            wrap="word",
            state="disabled",
            font=BODY,
            padx=12,
            pady=10,
        )
        scroll = ttk.Scrollbar(content, command=self.history.yview)
        scroll.pack(side="right", fill="y")
        self.history.configure(yscrollcommand=scroll.set)
        self.history.pack(fill="both", expand=True)
        self.page(None)
        self.timer = self.after(3000, self.poll)

    def page(self, before):
        self.before = before
        try:
            self.rows = self.app.read(
                lambda: self.app.market.messaging.messages(self.token, self.cid, before_id=before)
            )
            self.render()
        except BusinessError as exc:
            self.feedback.set(str(exc))

    def older(self):
        if self.rows:
            self.page(self.rows[0]["id"])

    def render(self):
        text = "\n\n".join(
            f"{r['sender_name']}  {display_time(r['created_at'])}"
            + (" · 已读" if r["read_at"] else "")
            + f"\n{r['content']}"
            for r in self.rows
        )
        current = self.history.get("1.0", "end").strip()
        if current != (text or "暂无消息，可以先向对方询问商品情况。").strip():
            self.history.configure(state="normal")
            self.history.delete("1.0", "end")
            self.history.insert("1.0", text or "暂无消息，可以先向对方询问商品情况。")
            self.history.configure(state="disabled")
            if self.before is None:
                self.history.see("end")

    def send(self):
        text = self.input.get("1.0", "end").strip()
        if not text:
            self.feedback.set("请输入消息内容")
            return
        key = uuid.uuid4().hex
        result = self.app.command(
            lambda: self.app.market.messaging.send(self.token, self.cid, text, key), "消息已发送"
        )
        if result is not None:
            self.input.delete("1.0", "end")
            self.page(None)

    def poll(self):
        self.timer = None
        if self.closed or self.app.closed or self.app.token != self.token:
            return
        try:
            if self.future is not None and self.future.done():
                rows = self.future.result()
                self.future = None
                if self.before is None:
                    self.rows = rows
                    self.render()
            if self.future is None and self.before is None and not self.app.command_busy:
                self.future = self.app.pool.submit(
                    self.app.market.messaging.messages, self.token, self.cid
                )
        except BusinessError as exc:
            self.feedback.set(str(exc))
        except Exception:
            logging.exception("Consultation refresh failed")
            self.feedback.set("消息刷新暂未成功，已输入内容仍保留；可稍后返回最新")
        if not self.closed:
            self.timer = self.after(3000, self.poll)

    def destroy(self):
        self.closed = True
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        super().destroy()


class FeatureViews(DeliveryViews):
    def restoration_history(self):
        rows = self.read(lambda: self.market.products.restoration_requests(self.token))
        return self.popup_text(
            "恢复申请进度",
            "\n\n".join(
                f"商品 #{r['product_id']} {r['title']}\n"
                f"{r['state']} · {r['decision_reason'] or '等待处理'}"
                for r in rows
            )
            or "暂无恢复申请。",
        )

    def notice_detail(self, row):
        result = self.command(
            lambda: self.market.messaging.read_notice(self.token, row["id"]), "通知已标为已读"
        )
        if result is not None:
            return self.popup_text("业务通知", row["content"])

    def profile_page(self):
        from .ui import TablePanel

        profile = self.read(lambda: self.market.accounts.profile(self.token))
        ttk.Label(
            self.content,
            text=f"{profile['nickname'] or profile['username']} · {profile['status']}",
            font=TITLE,
        ).pack(anchor="w", pady=8)
        bio = profile["bio"] or "暂无简介"
        preview = bio[:40] + "…" if len(bio) > 40 else bio
        ttk.Label(
            self.content,
            text=f"区域：{profile['region'] or '未设置'} · 简介：{preview}",
            wraplength=800,
        ).pack(anchor="w", pady=8)
        bar = ActionBar(self.content)
        bar.pack(fill="x")
        bar.add("修改资料与头像", lambda: self.edit_profile(profile))
        bar.add("修改密码", self.password_dialog)
        menu_button = ttk.Menubutton(bar, text="我的收藏与更多")
        menu = tk.Menu(menu_button, tearoff=False)
        menu.add_command(label="我的收藏", command=lambda: self.navigate("我的收藏"))
        menu.add_command(label="浏览记录", command=lambda: self.navigate("浏览记录"))
        menu.add_command(label="关注卖家", command=lambda: self.navigate("关注卖家"))
        menu.add_command(label="收货地址簿", command=lambda: self.address_book("收货"))
        menu.add_command(label="发货地址簿", command=lambda: self.address_book("发货"))
        menu.add_command(label="常用交接地点", command=lambda: self.navigate("交接地点"))
        menu.add_command(
            label="我的公开主页", command=lambda: self.seller_home({"seller_id": self.user["id"]})
        )
        menu_button.configure(menu=menu)
        bar.add_widget(menu_button)
        ttk.Label(
            self.content,
            text="同一账号可同时买卖。交易联系方式仅对当前待交易双方开放。",
            wraplength=850,
        ).pack(anchor="w", pady=4)
        self.table = TablePanel(
            self.content,
            [("name", "账号信息", 180), ("value", "内容", 550)],
            [
                {"name": "注册手机号（仅本人可见）", "value": profile["phone"]},
                {"name": "交易联系方式（仅本人可见）", "value": profile["contact"]},
                {"name": "账号状态说明", "value": profile["status_reason"] or "正常使用"},
            ],
        )

    def edit_profile(self, profile):
        avatar = {"data": None, "remove": False}
        specs = [
            ("nickname", "昵称", None),
            ("bio", "简介（最多500字）", None),
            ("region", "公开区域", None),
            ("contact", "交易联系方式（仅待交易双方可见）", None),
        ]
        win = FormDialog(
            self,
            "个人资料",
            specs,
            lambda fields: self.market.accounts.update(
                self.token, **fields, avatar=avatar["data"], remove_avatar=avatar["remove"]
            ),
            initial=profile,
        )

        def select():
            path = filedialog.askopenfilename(
                parent=win, filetypes=[("JPEG / PNG", "*.jpg *.jpeg *.png")]
            )
            if path:
                try:
                    data, normalized = self.read(lambda: PhotoPicker.load(path))
                    avatar.update(data=data, remove=False)
                    win.feedback.set("已选择头像，保存后生效")
                    PhotoGallery(win, [normalized])
                except BusinessError as exc:
                    win.feedback.set(str(exc))

        def remove():
            avatar.update(data=None, remove=True)
            win.feedback.set("保存后移除头像")

        win.actions.add("选择头像", select)
        win.actions.add("移除头像", remove)
        return win

    def password_dialog(self):
        def submit(values):
            if values["new_password"] != values["confirm_password"]:
                raise BusinessError("两次新密码不一致")
            result = self.market.accounts.change_password(
                self.token, values["old_password"], values["new_password"]
            )
            return result

        win = FormDialog(
            self,
            "修改密码（成功后所有会话退出）",
            [
                ("old_password", "原密码", None),
                ("new_password", "新密码", None),
                ("confirm_password", "再次输入新密码", None),
            ],
            submit,
        )
        # Session invalidation is observed by the shared refresh path after saving.
        return win

    def social_page(self, history=False):
        from .ui import TablePanel

        page_key = "history_offset" if history else "favorite_offset"
        offset = self.filters.get(page_key, 0)
        rows = self.read(
            lambda: self.market.social.products(self.token, history=history, offset=offset)
        )
        bar = ActionBar(self.content)
        bar.pack(fill="x")
        if history:
            bar.add(
                "清空浏览记录",
                lambda: self.confirm_command(
                    "清空本账号的浏览记录？", lambda: self.market.social.clear_history(self.token)
                ),
            )
        previous = bar.add("上一页", lambda: self.social_turn(page_key, max(0, offset - 100)))
        following = bar.add("下一页", lambda: self.social_turn(page_key, offset + 100))
        previous.configure(state="normal" if offset else "disabled")
        following.configure(state="normal" if len(rows) == 100 else "disabled")
        actions = [
            ("查看商品", self.product_detail, lambda r: r["state"] in ("已发布", "已售出")),
            ("卖家主页", self.seller_home, lambda _r: True),
        ]
        if not history:
            actions.append(
                (
                    "取消收藏",
                    lambda r: self.command(
                        lambda: self.market.social.favorite(self.token, r["id"], False),
                        "已取消收藏",
                    ),
                    lambda _r: True,
                )
            )
        self.table = TablePanel(
            self.content,
            [("title", "商品", 320), ("state", "当前状态", 120), ("price_cents", "价格", 130)],
            rows,
            actions,
        )

    def social_turn(self, key, offset):
        self.filters[key] = offset
        self.refresh()

    def followed_page(self):
        from .ui import TablePanel

        self.table = TablePanel(
            self.content,
            [("nickname", "卖家", 180), ("region", "区域", 160), ("bio", "简介", 400)],
            self.read(lambda: self.market.social.follows(self.token)),
            [
                ("公开主页", self.seller_home, lambda _r: True),
                (
                    "取消关注",
                    lambda r: self.command(
                        lambda: self.market.social.follow(self.token, r["seller_id"], False),
                        "已取消关注",
                    ),
                    lambda _r: True,
                ),
            ],
        )

    def consultations(self, parent):
        from .ui import TablePanel

        return TablePanel(
            parent,
            [
                ("title", "商品咨询", 280),
                ("buyer_name", "买家", 120),
                ("seller_name", "卖家", 120),
                ("unread", "未读", 70),
            ],
            self.read(lambda: self.market.messaging.conversations(self.token)),
            [("打开咨询", lambda r: ChatWindow(self, r["id"], r["product_id"]), lambda _r: True)],
        )

    def open_consultation(self, product):
        cid = self.command(
            lambda: self.market.messaging.open(self.token, product["id"]), "咨询会话已打开"
        )
        if cid is not None:
            return ChatWindow(self, cid, product["id"])

    def reserve_dialog(self, product, parent):
        return self.delivery_reserve_dialog(product, parent)

    def booking_action(self, row, action):
        if action == "complete":
            return self.confirm_command(
                "请确认线下钱货已交接。完成后将关闭联系方式查看。",
                lambda: self.market.bookings.act_reservation(
                    self.token, row["id"], row["product_version"], action
                ),
            )
        if action == "agree" and row.get("delivery_mode") == "邮寄":
            return self.confirm_command(
                "同意邮寄预约？同意后进入待发货，请核对订单收货信息。",
                lambda: self.market.bookings.act_reservation(
                    self.token, row["id"], row["product_version"], "agree"
                ),
            )
        if action == "agree":
            return FormDialog(
                self,
                "同意预约并确认交接",
                [("agreed_time", "最终交接时间（YYYY-MM-DD HH:MM，留空表示继续协商）", None)],
                lambda v: self.market.bookings.act_reservation(
                    self.token,
                    row["id"],
                    row["product_version"],
                    action,
                    agreed_time=v["agreed_time"],
                ),
                initial={"agreed_time": row.get("proposed_time", "")},
            )
        return FormDialog(
            self,
            "拒绝预约" if action == "reject" else "取消预约或交易",
            [("reason", "请说明原因（双方可见）", None)],
            lambda v: self.market.bookings.act_reservation(
                self.token, row["id"], row["product_version"], action, reason=v["reason"]
            ),
        )

    def booking_detail(self, row):
        return self.order_detail(row)

    def handoff_dialog(self, row):
        data = self.read(lambda: self.market.bookings.handoff(self.token, row["id"]))
        win = self.popup_text("交接约定与变更", self.handoff_text(self.token, row["id"]))
        self.contact_windows[win] = row["id"]

        def propose():
            return FormDialog(
                self,
                "提议变更交接约定",
                [
                    ("region", "区域", None),
                    ("point", "具体公共交接点", None),
                    ("time", "时间（YYYY-MM-DD HH:MM）", None),
                    ("note", "交接说明", None),
                ],
                lambda v: self.market.handoff.propose(self.token, row["id"], **v),
                initial=data,
            )

        win.actions.add("提议地点 / 时间变更", propose)
        changes = self.read(lambda: self.market.handoff.changes(self.token, row["id"]))
        pending = next((c for c in changes if c["state"] == "待确认"), None)
        if pending and pending["proposer_id"] != self.user["id"]:
            for caption, approve in [("同意变更", True), ("拒绝变更", False)]:
                win.actions.add(
                    caption,
                    lambda a=approve: self.command(
                        lambda: self.market.handoff.decide(self.token, pending["id"], a),
                        "交接变更已处理",
                    ),
                )
        return win

    def review_dialog(self, row):
        return FormDialog(
            self,
            "评价本次交易",
            [("score", "评分", ["5", "4", "3", "2", "1"]), ("text", "评价内容", None)],
            lambda v: self.market.reviews.submit(
                self.token, row["id"], int(v["score"] or 0), v["text"]
            ),
            initial={"score": "5"},
        )

    def public_reviews(self, seller_id):
        from .ui import TablePanel

        result = self.read(lambda: self.market.reviews.public(self.token, seller_id))
        win = self.popup_text(
            "真实交易评价",
            f"评价数：{result['count']}，"
            + (f"平均 {result['average']:.1f} 分" if result["count"] else "暂无评价"),
        )
        frame = ttk.Frame(win)
        frame.grid(row=0, column=0, sticky="nsew")
        TablePanel(
            frame,
            [("author_name", "评价者", 130), ("score", "评分", 55), ("content", "评价内容", 390)],
            result["items"],
            [
                (
                    "举报不当评价",
                    lambda r: FormDialog(
                        self,
                        "举报评价",
                        [("reason", "理由", None)],
                        lambda v: self.market.reviews.report(self.token, r["id"], v["reason"]),
                    ),
                    lambda _r: True,
                )
            ],
        )
        return win

    def confirm_command(self, text, operation):
        if messagebox.askyesno("确认操作", text, parent=self.root):
            return self.command(operation)
        return None

    def admin_center(self):
        from .ui import TablePanel

        tabs = ttk.Notebook(self.content)
        tabs.pack(fill="both", expand=True)
        for title, rows, columns, actions in self.admin_tabs():
            frame = ttk.Frame(tabs)
            tabs.add(frame, text=title)
            self.table = TablePanel(frame, columns, rows, actions)
        self.bind_notebook_table(tabs)
        add = ActionBar(self.content)
        add.pack(fill="x")
        add.add("新增分类", lambda: self.category_dialog())

    def category_dialog(self, row=None):
        return FormDialog(
            self,
            "分类维护",
            [("name", "分类名称", None), ("enabled", "状态", ["启用", "停用"])],
            lambda v: self.market.accounts.save_category(
                self.token, v["name"], v["enabled"] == "启用"
            ),
            initial={
                "name": (row or {}).get("name", ""),
                "enabled": "启用" if (row or {}).get("enabled", 1) else "停用",
            },
        )

    def admin_tabs(self):
        def restore(row, approve):
            return self.reason_dialog(
                "恢复申请处理",
                lambda reason: self.command(
                    lambda: self.market.products.resolve_restore(
                        self.token, row["id"], row["version"], approve, reason
                    )
                ),
            )

        def moderate(row, hide):
            return self.reason_dialog(
                "评价举报处理",
                lambda reason: self.command(
                    lambda: self.market.reviews.resolve(self.token, row["id"], hide, reason)
                ),
            )

        def status(row):
            return self.reason_dialog(
                "账号状态调整",
                lambda reason: self.command(
                    lambda: self.market.accounts.set_status(
                        self.token, row["id"], row["status"] != "受限", reason
                    )
                ),
            )

        return [
            (
                "全部商品",
                self.read(lambda: self.market.queries.managed_products(self.token)),
                [("id", "编号", 70), ("title", "商品", 320), ("state", "状态", 120)],
                [("查看详情", self.product_detail, lambda _r: True)],
            ),
            (
                "恢复申请",
                self.read(lambda: self.market.products.restoration_requests(self.token)),
                [("title", "商品", 220), ("reason", "申请理由", 330), ("state", "状态", 90)],
                [
                    ("允许重审", lambda r: restore(r, True), lambda r: r["state"] == "待处理"),
                    ("拒绝恢复", lambda r: restore(r, False), lambda r: r["state"] == "待处理"),
                ],
            ),
            (
                "分类",
                self.read(lambda: self.market.accounts.categories(self.token, all_states=True)),
                [("name", "分类", 300), ("enabled", "启用（1/0）", 130)],
                [("维护分类", self.category_dialog, lambda _r: True)],
            ),
            (
                "用户状态",
                self.read(lambda: self.market.accounts.users(self.token)),
                [
                    ("id", "编号", 70),
                    ("nickname", "昵称", 180),
                    ("status", "状态", 90),
                    ("status_reason", "处理说明", 330),
                ],
                [("限制 / 恢复账号", status, lambda _r: True)],
            ),
            (
                "评价举报",
                self.read(lambda: self.market.reviews.reports(self.token)),
                [
                    ("review_content", "被举报评价", 300),
                    ("reason", "举报理由", 250),
                    ("state", "状态", 90),
                ],
                [
                    ("核实并隐藏", lambda r: moderate(r, True), lambda r: r["state"] == "待处理"),
                    ("驳回举报", lambda r: moderate(r, False), lambda r: r["state"] == "待处理"),
                ],
            ),
        ]

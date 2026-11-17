"""Tkinter desktop views. Every button delegates authorization and rules to services."""

import contextlib
import gc
import json
import logging
import time
import tkinter as tk
import uuid
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

from .domain import BusinessError, display_money, display_time
from .domain import ProductState as P
from .reports import VIOLATION_ALLOWED
from .ui_extras import PhotoGallery, PhotoPicker, PlaceEditor
from .ui_features import FeatureViews
from .ui_theme import BODY, HERO, TITLE, configure_ui
from .ui_widgets import ActionBar, TextDialog

INK = "#18324A"
BLUE = "#245F95"
PALE = "#F3F7FB"


class TablePanel(ttk.Frame):
    """Reusable selectable table with full record details and state-aware actions."""

    def __init__(self, parent, columns, rows, actions=(), *, height=13):
        super().__init__(parent)
        self.pack(fill="both", expand=True, padx=4, pady=4)
        self.rows = {str(index): row for index, row in enumerate(rows)}
        self.stale = False
        covers = any(column[0] in ("meeting_region", "location") for column in columns)
        self.covers = []
        if covers:
            placeholder = tk.PhotoImage(master=self, width=36, height=24)
            placeholder.put("#DFE8F0", to=(0, 0, 36, 24))
        self.tree = ttk.Treeview(
            self,
            columns=[c[0] for c in columns],
            show="tree headings" if covers else "headings",
            height=min(height, 3),
            selectmode="browse",
        )
        if covers:
            self.tree.heading("#0", text="封面")
            self.tree.column("#0", width=82, minwidth=82, stretch=False, anchor="center")
            self.tree.configure(style="Products.Treeview")
        for state, color in [
            ("已售出", "#637589"),
            ("违规下架", "#A83932"),
            ("待审核", "#916700"),
            ("已发布", "#236348"),
        ]:
            self.tree.tag_configure(state, foreground=color)
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=55, stretch=True)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        for key, row in self.rows.items():
            values = []
            for column, _, _ in columns:
                value = row.get(column, "")
                if column == "price_cents":
                    value = "¥ " + display_money(value) if value is not None else "—"
                elif column in ("created_at", "deadline", "handled_at"):
                    value = display_time(value)
                values.append(value)
            if covers:
                data = row.get("cover_thumbnail")
                try:
                    cover = tk.PhotoImage(master=self, data=data) if data else placeholder
                except tk.TclError:
                    cover = placeholder
                self.covers.append(cover)
                self.tree.insert(
                    "", "end", iid=key, values=values, image=cover, tags=(row.get("state", ""),)
                )
            else:
                self.tree.insert("", "end", iid=key, values=values, tags=(row.get("state", ""),))
        bar = ActionBar(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        self.buttons = []
        for label, callback, predicate in actions:
            button = bar.add(label, lambda cb=callback: cb(self.selected()))
            self.buttons.append((button, predicate))
        self.description = tk.Text(
            self,
            height=1,
            wrap="word",
            font=BODY,
            bg="white",
            fg=INK,
            relief="flat",
            padx=10,
            pady=8,
        )
        self.description.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.selection_changed()
        if not rows:
            self.description.configure(state="normal")
            self.description.insert("1.0", "暂无符合条件的记录。可以调整查询条件或稍后刷新。")
        self.description.configure(state="disabled")

    def selected(self):
        selected = self.tree.selection()
        return self.rows[selected[0]] if selected else None

    def selection_changed(self, _event=None):
        row = self.selected()
        for button, predicate in self.buttons:
            button.configure(
                state="normal" if row and not self.stale and predicate(row) else "disabled"
            )
        self.description.configure(state="normal")
        self.description.delete("1.0", "end")
        if row:
            fields = [
                key
                for key in (
                    "title",
                    "description",
                    "reason",
                    "content",
                    "before_state",
                    "after_state",
                )
                if key in row
            ]
            labels = {
                "title": "商品",
                "description": "说明",
                "reason": "举报原因",
                "content": "通知内容",
                "before_state": "变更前",
                "after_state": "变更后",
            }
            self.description.insert(
                "1.0", "\n".join(f"{labels[key]}：{row[key]}" for key in fields)
            )
        self.description.configure(state="disabled")


class ProductEditor(tk.Toplevel):
    """Editable draft fields, with submission validation owned by the service."""

    def __init__(self, app, item=None):
        super().__init__(app.root)
        from .products import CONDITIONS
        from .ui_widgets import ScrollArea

        self.app, self.item = app, item
        self.title("编辑商品" if item else "创建商品草稿")
        self.geometry("740x660")
        self.minsize(560, 500)
        self.transient(app.root)
        self.after_idle(lambda: TextDialog.center(self))
        self.feedback = tk.StringVar()
        footer = ttk.Frame(self, padding=(18, 8))
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, textvariable=self.feedback, foreground="#B14736", wraplength=460).pack(
            fill="x"
        )
        self.save_button = ttk.Button(
            footer, text="保存修改" if item else "保存草稿", command=self.save
        )
        self.save_button.pack(side="left", pady=4)
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="left", padx=10)
        area = ScrollArea(self)
        self.scroll_area = area
        area.pack(fill="both", expand=True)
        frame = area.inner
        self.fields = {}
        initial = item or {}
        categories = [
            r["name"] for r in app.read(lambda: app.market.accounts.categories(app.token))
        ]
        specs = [
            ("title", "商品标题", None),
            ("category", "分类", ["", *categories]),
            ("price", "价格（元，草稿可留空）", None),
            ("condition", "成色", CONDITIONS),
            ("brand", "品牌 / 型号（可选）", None),
            ("region", "面交大致区域（可选，公开）", None),
            ("handoff_method", "交付方式", ["仅邮寄", "仅面交", "邮寄或面交"]),
            ("origin_city", "商品/发货城市（公开）", None),
            ("origin_district", "商品所在区县（公开）", None),
            ("free_shipping", "邮寄运费规则", ["包邮", "固定运费"]),
            ("shipping_fee", "固定运费（元，面交不计费）", None),
        ]
        for index, (key, label, choices) in enumerate(specs):
            row, col = divmod(index, 2)
            group = ttk.Frame(frame)
            group.grid(row=row, column=col, sticky="ew", padx=(0, 12), pady=4)
            ttk.Label(group, text=label).pack(anchor="w")
            value = initial.get(key, "")
            if key == "price" and item:
                value = "" if item.get("price_missing") else display_money(item["price_cents"])
            if key == "condition":
                value = value or "成色良好"
            if key == "handoff_method":
                value = value if value in ("仅邮寄", "仅面交", "邮寄或面交") else "仅面交"
            if key == "free_shipping":
                value = "包邮" if initial.get("free_shipping", 1) else "固定运费"
            if key == "shipping_fee":
                value = display_money(initial.get("freight_cents", 0))
            if key == "region":
                value = initial.get("meeting_region", "")
            var = tk.StringVar(value=value)
            self.fields[key] = var
            control = (
                ttk.Combobox(group, textvariable=var, values=choices, state="readonly")
                if choices
                else ttk.Entry(group, textvariable=var)
            )
            control.pack(fill="x", pady=3)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="描述：请写明瑕疵、配件及使用情况").grid(
            row=6, column=0, columnspan=2, sticky="w"
        )
        self.description = tk.Text(frame, height=4, wrap="word", font=BODY)
        self.description.grid(row=7, column=0, columnspan=2, sticky="ew", pady=6)
        self.description.insert("1.0", initial.get("description", ""))
        photos = (
            app.read(lambda: app.market.queries.detail(app.token, item["id"], include_photos=True))[
                "photos"
            ]
            if item
            else []
        )
        ttk.Label(frame, text="图片（最多6张，第一张为封面）").grid(
            row=8, column=0, columnspan=2, sticky="w"
        )
        self.photo_picker = PhotoPicker(frame, self, photos)
        self.photo_picker.grid(row=9, column=0, columnspan=2, sticky="ew")
        places = app.read(lambda: app.market.places.list(app.token))
        options = ["保留已选地点" if item else "未选择交接地点", "清除交接地点"]
        options.extend(f"#{r['id']} · {r['region']} / {r['point']}" for r in places)
        self.place_choices = [None, None] + [r["id"] for r in places]
        self.place_choice = ttk.Combobox(frame, values=options, state="readonly")
        self.place_choice.grid(row=10, column=0, columnspan=2, sticky="ew", pady=8)
        self.place_choice.current(0)
        ttk.Label(
            frame,
            text="草稿允许不完整；提交审核需填完整。已发布商品修改后重新审核。",
            wraplength=500,
        ).grid(row=11, column=0, columnspan=2, sticky="w")

    def save(self):
        fields = {key: value.get() for key, value in self.fields.items()}
        fields["free_shipping"] = fields["free_shipping"] == "包邮"
        fields["description"] = self.description.get("1.0", "end").strip()
        fields["photos"] = [row[0] for row in self.photo_picker.photos]
        selected = self.place_choice.current()
        if selected > 0:
            fields["meeting_place_id"] = self.place_choices[selected]
        if selected > 1:
            fields.pop("region")
        if self.item:

            def operation():
                return self.app.market.products.edit_product(
                    self.app.token, self.item["id"], self.item["version"], **fields
                )
        else:

            def operation():
                return self.app.market.products.create_draft(self.app.token, **fields)

        result = self.app.command(operation, "商品已保存；提交审核并通过后才可发布")
        if result is not None:
            self.destroy()


class Desktop(FeatureViews):
    """Desktop composition with no duplicated business state machine."""

    def __init__(self, root, market):
        self.root = root
        self.market = market
        self.token = None
        self.user = None
        self.current_page = "发现商品"
        self.table = None
        self.filters = {}
        self.form_state = {}
        # Tcl objects must be finalized on Tk's thread, never by an I/O worker's GC.
        self.gc_was_enabled = gc.isenabled()
        gc.disable()
        self.last_collection = time.monotonic()
        self.command_busy = False
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="market-io")
        self.poll_future = None
        self.last_revision = None
        self.revision_epoch = 0
        self.pending_refresh = False
        self.contact_windows = {}
        self.delivery_windows = {}
        self.offset = 0
        self.next_poll = 0
        self.status = tk.StringVar(value="欢迎使用拾光二手交易")
        self.root.title("拾光 · 二手交易系统")
        self.root.geometry("1240x840")
        self.root.minsize(1060, 720)
        self.root.configure(bg=PALE)
        configure_ui(root)
        style = ttk.Style(root)
        style.configure("TFrame", background=PALE)
        style.configure("TLabel", background=PALE, foreground=INK, font=BODY)
        style.configure("Title.TLabel", foreground=BLUE)
        style.configure("TButton", background=BLUE, foreground="white", borderwidth=0)
        style.map(
            "TButton",
            background=[("disabled", "#D9E3EC"), ("active", "#193F64")],
            foreground=[("disabled", "#687D90")],
        )
        style.configure("Secondary.TButton", background="#DFE8F0", foreground=INK)
        style.configure("Danger.TButton", background="#A83932", foreground="white")
        for name in ("Secondary.TButton", "Danger.TButton"):
            style.map(
                name,
                background=[("disabled", "#D9E3EC"), ("active", "#637589")],
                foreground=[("disabled", "#687D90")],
            )
        self.body = ttk.Frame(root, padding=14)
        self.status_label = ttk.Label(
            root, textvariable=self.status, padding=(18, 8), wraplength=1180, foreground=BLUE
        )
        self.status_label.pack(side="bottom", fill="x")
        self.body.pack(fill="both", expand=True)
        self.show_login()
        self.timer_id = self.root.after(500, self.tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self.callback_error

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.table = None

    def message(self, text, *, error=False):
        self.status.set(text)
        self.status_label.configure(foreground="#B14736" if error else BLUE)
        for window in self.root.winfo_children():
            if isinstance(window, tk.Toplevel) and hasattr(window, "feedback"):
                window.feedback.set(text)

    def callback_error(self, kind, value, traceback):
        logging.error("UI callback failed", exc_info=(kind, value, traceback))
        self.message(
            str(value)
            if isinstance(value, BusinessError)
            else "界面操作失败，请重试；详细错误已保存到本地日志",
            error=True,
        )

    def read(self, operation):
        """Wait using Tk's event loop, keeping repaint/input responsive during disk I/O."""
        if self.command_busy:
            # Rendering after a completed write runs outside the busy interval.
            raise BusinessError("正在处理操作，请稍候")
        self.command_busy = True
        future = self.pool.submit(operation)
        done = tk.BooleanVar(master=self.root)

        def ready():
            if future.done():
                done.set(True)
            elif not self.closed:
                self.root.after(10, ready)

        self.root.after(0, ready)
        try:
            self.root.wait_variable(done)
            return future.result()
        finally:
            self.command_busy = False

    def command(self, operation, success="操作成功"):
        if self.command_busy:
            self.message("正在提交，请勿重复操作", error=True)
            return None
        try:
            result = self.read(operation)
        except BusinessError as exc:
            self.message(str(exc), error=True)
        except Exception:
            logging.exception("Unexpected application error")
            self.message("操作未完成，请重试；详细错误已保存在本地运行日志", error=True)
        else:
            self.revision_epoch += 1
            self.last_revision = None
            self.message(success)
            if self.token:
                try:
                    self.refresh()
                except Exception:
                    logging.exception("Committed operation succeeded; view refresh failed")
                    self.message(
                        success + "；操作已保存，但列表刷新失败，请点击刷新，勿重复提交", error=True
                    )
            return result
        return None

    def show_login(self):
        self.clear()
        ttk.Label(self.body, text="拾光 · 让闲置找到下一位主人", style="Title.TLabel").pack(
            pady=(12, 8)
        )
        ttk.Label(self.body, text="商品展示、预约撮合与线下交易管理").pack(pady=8)
        card = ttk.Frame(self.body, padding=14)
        card.pack(pady=8)
        self.login_identity = tk.StringVar()
        self.login_password = tk.StringVar()
        self.login_admin = tk.BooleanVar()
        ttk.Label(card, text="普通用户填手机号；管理员填独立登录名").pack(anchor="w", pady=8)
        ttk.Entry(card, textvariable=self.login_identity, width=45).pack(fill="x", pady=6)
        ttk.Label(card, text="密码").pack(anchor="w", pady=8)
        ttk.Entry(card, textvariable=self.login_password, show="•", width=45).pack(fill="x", pady=6)
        ttk.Checkbutton(card, text="管理员登录", variable=self.login_admin).pack(
            anchor="w", pady=12
        )
        self.login_button = ttk.Button(card, text="登录", command=self.login)
        self.login_button.pack(fill="x", pady=8)
        ttk.Button(card, text="注册普通用户", command=self.show_register).pack(fill="x", pady=8)
        ttk.Label(
            card,
            text="首次启动可初始化本机管理员，普通注册不授予后台权限。",
            wraplength=400,
            foreground="#617487",
        ).pack(pady=6)

    def show_register(self):
        self.clear()
        ttk.Label(self.body, text="注册普通用户", style="Title.TLabel").pack(pady=8)
        self.register_fields = {}
        card = ttk.Frame(self.body, padding=10)
        card.pack()
        for key, label in [
            ("phone", "手机号"),
            ("username", "用户名（2至24字）"),
            ("password", "密码（8至128字）"),
            ("contact", "交易联系方式（电话或微信）"),
        ]:
            ttk.Label(card, text=label).pack(anchor="w", pady=(6, 3))
            var = tk.StringVar()
            ttk.Entry(card, textvariable=var, width=48, show="•" if key == "password" else "").pack(
                fill="x"
            )
            self.register_fields[key] = var
        ttk.Button(card, text="注册", command=self.register).pack(fill="x", pady=10)
        ttk.Button(card, text="返回登录", command=self.show_login).pack(fill="x")

    def register(self):
        values = {k: v.get() for k, v in self.register_fields.items()}
        result = self.command(
            lambda: self.market.auth.register(**values),
            "注册成功，请使用手机号登录",
        )
        if result is not None:
            self.show_login()

    def login(self):
        if self.command_busy:
            return
        identity, password, admin = (
            self.login_identity.get(),
            self.login_password.get(),
            self.login_admin.get(),
        )
        try:
            self.token = self.read(lambda: self.market.auth.login(identity, password, admin=admin))
            self.user = self.market.auth.me(self.token)
            self.current_page = "商品审核" if self.user["role"] == "admin" else "发现商品"
            self.filters = {}
            self.form_state = {}
            self.offset = 0
            self.last_revision = None
            self.refresh()
            self.message(f"已登录：{self.user['username']}。请在操作前核对当前商品状态。")
        except BusinessError as exc:
            self.message(str(exc), error=True)

    def logout(self):
        if self.command_busy:
            self.message("正在处理操作，请完成后再退出", error=True)
            return
        self.market.auth.logout(self.token)
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        self.token = None
        self.user = None
        self.filters = {}
        self.form_state = {}
        self.offset = 0
        self.contact_windows.clear()
        self.delivery_windows.clear()
        self.last_revision = None
        self.pending_refresh = False
        self.show_login()
        self.message("已退出登录")

    def navigate(self, page):
        if self.command_busy:
            return
        self.current_page = page
        self.refresh()

    def refresh(self):
        if not self.token or self.command_busy:
            return
        try:
            self.user = self.read(lambda: self.market.auth.me(self.token))
        except BusinessError:
            self.logout()
            self.message("会话已失效，请使用当前密码重新登录")
            return
        self.pending_refresh = False
        self.refresh_contacts()
        self.clear()
        header = ttk.Frame(self.body)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="拾光 · 二手交易", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="退出登录", command=self.logout).pack(side="right")
        ttk.Label(
            header,
            text=f"{self.user['username']}  ·  "
            + ("管理员" if self.user["role"] == "admin" else "同一账号可买也可卖"),
        ).pack(side="right", padx=20)
        nav = ActionBar(self.body)
        nav.pack(fill="x", pady=(0, 8))
        pages = (
            ["商品审核", "举报审核", "管理中心", "统计与日志"]
            if self.user["role"] == "admin"
            else ["发现商品", "我的商品", "我的预约", "卖家预约", "消息中心", "个人中心"]
        )
        for name in pages:
            nav.add(name, lambda n=name: self.navigate(n))
        nav.add("刷新", self.refresh)
        ttk.Label(self.body, text=self.current_page, font=TITLE).pack(anchor="w", pady=8)
        self.content = ttk.Frame(self.body)
        self.content.pack(fill="both", expand=True)
        render = {
            "发现商品": self.discover,
            "个人中心": self.profile_page,
            "我的收藏": lambda: self.social_page(False),
            "浏览记录": lambda: self.social_page(True),
            "关注卖家": self.followed_page,
            "消息中心": self.notifications,
            "管理中心": self.admin_center,
            "我的商品": self.own_products,
            "交接地点": self.meeting_places,
            "我的预约": lambda: self.booking_list(False),
            "卖家预约": lambda: self.booking_list(True),
            "举报与通知": self.notifications,
            "商品审核": self.review_products,
            "举报审核": self.review_reports,
            "统计与日志": self.statistics,
        }
        render[self.current_page]()

    def field(self, parent, key, label, width=18, *, choices=None):
        if isinstance(parent, ActionBar):
            group = ttk.Frame(parent)
            parent.add_widget(group)
            parent = group
        ttk.Label(parent, text=label).pack(side="left", padx=(0, 5))
        var = tk.StringVar(value=self.form_state.get(key, self.filters.get(key, "")))
        var.trace_add("write", lambda *_: self.form_state.update({key: var.get()}))
        if choices is None:
            control = ttk.Entry(parent, textvariable=var, width=width)
        else:
            control = ttk.Combobox(
                parent, textvariable=var, values=choices, state="readonly", width=width
            )
        control.pack(side="left", padx=(0, 6))
        return var

    def discover(self):
        bar = ActionBar(self.content)
        bar.pack(fill="x", pady=6)
        categories = self.read(lambda: self.market.queries.categories(self.token))
        sort_options = {"最新发布": "latest", "价格升序": "price_asc", "价格降序": "price_desc"}
        from .products import CONDITIONS

        advanced = ActionBar(self.content)
        variables = {
            "keyword": self.field(bar, "keyword", "关键词", 14),
            "category": self.field(bar, "category", "分类", 8, choices=["", *categories]),
            "state": self.field(advanced, "state", "状态", 7, choices=["已发布", "已售出"]),
            "condition": self.field(advanced, "condition", "成色", 8, choices=["", *CONDITIONS]),
            "region": self.field(advanced, "region", "商品所在地", 7),
            "delivery_mode": self.field(
                advanced, "delivery_mode", "交付方式", 7, choices=["", "邮寄", "面交"]
            ),
            "minimum": self.field(advanced, "minimum", "最低价", 6),
            "maximum": self.field(advanced, "maximum", "最高价", 6),
            "product_id": self.field(advanced, "product_id", "商品编号", 7),
            "sort": self.field(advanced, "sort", "排序", 8, choices=list(sort_options)),
        }
        if not variables["state"].get():
            variables["state"].set("已发布")
        if not variables["sort"].get():
            variables["sort"].set("最新发布")

        def search():
            self.filters.update({key: var.get() for key, var in variables.items()})
            self.offset = 0
            self.refresh()

        def clear():
            for key in variables:
                self.filters.pop(key, None)
                self.form_state.pop(key, None)
            self.offset = 0
            self.refresh()

        bar.add("查询", search)
        bar.add("清空筛选", clear)

        def more_filters():
            from .ui_widgets import FormDialog

            context = {key: var.get() for key, var in variables.items()}
            specs = [
                ("state", "公开状态", ["已发布", "已售出"]),
                ("condition", "成色", ["", *CONDITIONS]),
                ("region", "城市或区县（例如：北京市 海淀区；忽略空格，非收货地）", None),
                ("delivery_mode", "交付方式（留空跨地区查找）", ["", "邮寄", "面交"]),
                ("minimum", "最低价（元）", None),
                ("maximum", "最高价（元）", None),
                ("product_id", "商品编号（精确）", None),
                ("sort", "排序", list(sort_options)),
            ]

            def apply(values):
                candidate = {**context, **values}
                candidate["sort"] = sort_options.get(candidate["sort"], "latest")
                self.market.queries.search(self.token, **candidate)
                self.filters.update(context)
                self.filters.update(values)
                self.form_state.update(values)
                self.offset = 0
                return True

            FormDialog(
                self,
                "更多筛选与排序",
                specs,
                apply,
                caption="应用筛选",
                initial=context,
            )

        bar.add("更多筛选 / 排序", more_filters)
        self.search_vars = variables
        options = {key: self.filters.get(key, "") for key in variables}
        options["sort"] = sort_options.get(options["sort"], "latest")
        options["state"] = options["state"] or "已发布"

        def fetch():
            return self.read(
                lambda: self.market.queries.search(self.token, **options, offset=self.offset)
            )

        result = fetch()
        if self.offset and self.offset >= result["total"]:
            self.offset = max(0, ((result["total"] - 1) // 100) * 100)
            result = fetch()
        self.search_total = result["total"]
        ttk.Label(
            self.content,
            text=(
                f"共{result['total']}条；当前从第{self.offset + 1}条显示。"
                f"公开状态：{options['state']}。"
                if result["total"]
                else "暂无符合条件的公开商品；可调整条件后查询。"
            ),
        ).pack(anchor="w", pady=5)
        labels = {
            "keyword": "关键词",
            "category": "分类",
            "state": "状态",
            "condition": "成色",
            "region": "商品所在地",
            "delivery_mode": "交付方式",
            "minimum": "最低价",
            "maximum": "最高价",
            "product_id": "编号",
            "sort": "排序",
        }
        tags = "  ·  ".join(
            f"{labels[key]}：{value}"
            for key, value in self.filters.items()
            if key in labels and value
        )
        if tags:
            ttk.Label(self.content, text="当前筛选  " + tags, wraplength=1000).pack(anchor="w")
        page = ttk.Frame(self.content)
        page.pack(side="bottom", fill="x")
        self.previous_button = ttk.Button(page, text="上一页", command=lambda: self.turn_page(-100))
        self.previous_button.pack(side="left", padx=4)
        self.next_button = ttk.Button(page, text="下一页", command=lambda: self.turn_page(100))
        self.next_button.pack(side="left", padx=4)
        self.previous_button.configure(state="normal" if self.offset else "disabled")
        self.next_button.configure(
            state="normal" if self.offset + 100 < result["total"] else "disabled"
        )
        self.table = TablePanel(
            self.content,
            self.product_columns(),
            result["items"],
            [
                ("商品详情 / 预约", self.product_detail, lambda _r: True),
                ("举报商品", self.report_dialog, lambda r: r["seller_id"] != self.user["id"]),
                ("卖家主页", self.seller_home, lambda _r: True),
            ],
        )

    def turn_page(self, delta):
        if self.command_busy:
            return
        maximum = max(0, ((self.search_total - 1) // 100) * 100)
        self.offset = min(maximum, max(0, self.offset + delta))
        self.refresh()

    @staticmethod
    def product_columns():
        return [
            ("id", "编号", 60),
            ("title", "商品", 250),
            ("category", "分类", 100),
            ("price_cents", "价格", 120),
            ("state", "商品状态", 110),
            ("location", "商品所在地", 120),
            ("handoff_method", "交付方式", 115),
            ("condition", "成色", 105),
        ]

    def own_products(self):
        bar = ttk.Frame(self.content)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="创建草稿", command=lambda: ProductEditor(self)).pack(
            side="left", padx=(0, 20)
        )
        ttk.Button(bar, text="恢复申请进度", command=self.restoration_history).pack(
            side="left", padx=6
        )
        ttk.Button(bar, text="交接地点", command=lambda: self.navigate("交接地点")).pack(
            side="left", padx=(0, 12)
        )
        ttk.Label(bar, text="状态").pack(side="left")
        var = tk.StringVar(value=self.filters.get("own_state", ""))
        combo = ttk.Combobox(
            bar, textvariable=var, values=[""] + list(P), state="readonly", width=12
        )
        combo.pack(side="left", padx=8)

        def apply(_event=None):
            self.filters["own_state"] = var.get()
            self.refresh()

        combo.bind("<<ComboboxSelected>>", apply)
        ttk.Label(
            self.content,
            text="已发布商品修改后重审；普通下架可申请重审。预约期间禁止编辑和主动下架。",
            foreground="#617487",
        ).pack(anchor="w", pady=6)
        service = self.market.products
        selected_state = var.get()
        self.table = TablePanel(
            self.content,
            self.product_columns(),
            self.read(lambda: self.market.queries.own_products(self.token, selected_state)),
            [
                ("查看", self.product_detail, lambda _r: True),
                (
                    "重新上架申请",
                    lambda r: self.product_command(service.relist, r),
                    lambda r: r["state"] == P.OFFLINE,
                ),
                (
                    "违规恢复申请",
                    lambda r: self.reason_dialog(
                        "违规商品恢复申请",
                        lambda reason: self.command(
                            lambda: service.request_restore(
                                self.token, r["id"], r["version"], reason
                            )
                        ),
                    ),
                    lambda r: r["state"] == P.VIOLATION,
                ),
                (
                    "编辑商品",
                    lambda r: ProductEditor(self, r),
                    lambda r: r["state"] in (P.DRAFT, P.REJECTED, P.PUBLISHED),
                ),
                (
                    "提交审核",
                    lambda r: self.product_command(service.submit, r),
                    lambda r: r["state"] in (P.DRAFT, P.REJECTED),
                ),
                (
                    "主动下架",
                    lambda r: self.product_command(service.take_down, r),
                    lambda r: r["state"] == P.PUBLISHED,
                ),
                (
                    "删除草稿",
                    lambda r: self.confirm_command(
                        "确定永久删除这个未提交草稿及其图片？",
                        lambda: service.delete_draft(self.token, r["id"], r["version"]),
                    ),
                    lambda r: r["state"] == P.DRAFT,
                ),
            ],
        )

    def meeting_places(self):
        ttk.Button(self.content, text="新增地点", command=lambda: PlaceEditor(self)).pack(
            anchor="w", pady=6
        )
        rows = self.read(lambda: self.market.places.list(self.token))
        self.table = TablePanel(
            self.content,
            [("region", "城市及区县", 180), ("point", "公共交接点", 350)],
            rows,
            [
                ("修改地点", lambda row: PlaceEditor(self, row), lambda _row: True),
                (
                    "删除地点",
                    lambda row: self.command(
                        lambda: self.market.places.delete(self.token, row["id"]), "地点已删除"
                    ),
                    lambda _row: True,
                ),
            ],
        )

    def product_command(self, method, row, *args):
        return self.command(lambda: method(self.token, row["id"], row["version"], *args))

    def popup_text(self, title, text):
        return TextDialog(self.root, title, text)

    def product_detail(self, row, previous=None):
        try:
            p = self.read(lambda: self.market.queries.detail(self.token, row["id"]))
        except BusinessError as exc:
            self.message(str(exc), error=True)
            return
        if self.user["role"] == "user":
            with contextlib.suppress(BusinessError):
                self.read(lambda: self.market.social.viewed(self.token, p["id"]))
        from .delivery import supported

        modes = supported(p["handoff_method"])
        freight_label = "包邮" if p["free_shipping"] else "¥" + display_money(p["freight_cents"])
        shipping = f"邮寄运费：{freight_label}\n" if "邮寄" in modes else ""
        meeting = f"面交区域：{p['meeting_region'] or '双方协商'}\n" if "面交" in modes else ""
        text = (
            f"{p['title']}\n\n¥ {display_money(p['price_cents'])}\n"
            f"分类：{p['category']} · 成色：{p['condition']} · "
            f"品牌/型号：{p['brand'] or '未填写'}\n"
            f"卖家：{p['seller_name']} · 交付：{p['handoff_method']}\n"
            f"商品所在地：{p['location'] or '未填写'}\n{shipping}"
            f"当前状态：{p['state']}\n"
            + ("卖家暂不接受新预约或咨询；已有交易仍可处理。\n" if not p["accepting_new"] else "")
            + f"{meeting}\n{p['description']}\n\n"
            "同一普通账号可以发布商品，也可以预约其他人的商品。\n"
            "邮寄订单在履约详情查看收件信息与运单；面交同意后向双方开放交接信息。\n"
            + (
                "这是你发布的商品，不能预约自己的商品。\n"
                if p["seller_id"] == self.user["id"]
                else ""
            )
            + (
                f"最近审核理由：{p['review_reason']}"
                if p.get("review_reason")
                and (p["seller_id"] == self.user["id"] or self.user["role"] == "admin")
                else ""
            )
        )
        win = previous if previous is not None else self.popup_text("商品详情", "")
        win.content_text.configure(state="normal")
        win.content_text.delete("1.0", "end")
        win.content_text.insert("1.0", text)
        if previous is not None:
            for control in win.actions.controls:
                control.destroy()
            win.actions.controls.clear()
            win.actions.add("关闭", win.destroy)
        win.content_text.tag_configure("price", font=HERO, foreground=BLUE)
        win.content_text.tag_add("price", "3.0", "3.end")
        if p.get("cover_thumbnail"):
            try:
                win.cover_image = tk.PhotoImage(master=win, data=p["cover_thumbnail"]).zoom(3)
                win.content_text.image_create("1.0", image=win.cover_image)
                win.content_text.insert("1.1", "\n")
            except tk.TclError:
                win.content_text.insert("1.0", "图片暂不可用\n")
        win.content_text.configure(state="disabled")
        win.actions.add("卖家主页", lambda: self.seller_home(p))
        win.actions.add("刷新详情", lambda: self.reload_detail(win, p["id"]))
        if (
            p["seller_id"] != self.user["id"]
            and self.user["role"] == "user"
            and p["state"] in (P.PUBLISHED, P.RESERVED, P.TRADING, P.SOLD)
        ):
            win.actions.add("举报商品", lambda: self.report_dialog(p))
        if (
            p["state"] == P.PUBLISHED
            and p["accepting_new"]
            and p["seller_id"] != self.user["id"]
            and self.user["role"] == "user"
        ):
            win.actions.add("发起预约", lambda: self.reserve_dialog(p, win))
            win.actions.add("咨询卖家", lambda: self.open_consultation(p))
        if self.user["role"] == "user" and p["state"] in (P.PUBLISHED, P.SOLD):
            flags = self.read(lambda: self.market.social.flags(self.token, p["id"], p["seller_id"]))
            win.actions.add(
                "取消收藏" if flags["favorite"] else "收藏商品",
                lambda: self.favorite_detail(win, p, not flags["favorite"]),
            )
        if self.user["role"] == "admin" or self.user["id"] == p["seller_id"]:
            win.actions.add(
                "审核历史",
                lambda: self.popup_text(
                    "审核历史",
                    "\n\n".join(
                        f"{display_time(r['created_at'])} · "
                        f"{'通过' if r['approved'] else '驳回'}\n{r['reason']}"
                        for r in self.read(
                            lambda: self.market.queries.review_history(self.token, p["id"])
                        )
                    ),
                ),
            )
        win.actions.add("查看图片", lambda: self.show_product_photos(win, p["id"]))
        return win

    def show_product_photos(self, parent, product_id):
        try:
            item = self.read(
                lambda: self.market.queries.detail(self.token, product_id, include_photos=True)
            )
        except BusinessError as exc:
            self.message(str(exc), error=True)
            return
        return PhotoGallery(parent, [row["content"] for row in item["photos"]])

    def reload_detail(self, previous, product_id):
        if previous.winfo_exists():
            return self.product_detail({"id": product_id}, previous)

    def favorite_detail(self, window, product, enabled):
        result = self.command(
            lambda: self.market.social.favorite(self.token, product["id"], enabled),
            "收藏状态已更新",
        )
        if result is not None:
            self.reload_detail(window, product["id"])

    def seller_home(self, row):
        try:
            profile = self.read(
                lambda: self.market.queries.seller_profile(self.token, row["seller_id"])
            )
        except BusinessError as exc:
            self.message(str(exc), error=True)
            return None
        rows = profile["products"]
        ratings = self.read(lambda: self.market.reviews.public(self.token, row["seller_id"]))
        win = self.popup_text(
            "卖家公开主页",
            f"{profile['nickname'] or profile['username']}\n卖家 "
            f"ID：{profile['seller_id']} · 用户名：{profile['username']}\n"
            f"所在区域：{profile['region'] or '未设置'}\n{profile['bio'] or '暂无简介'}\n"
            + (
                f"真实评价 {ratings['count']} 条 · 平均 {ratings['average']:.1f}分\n\n"
                if ratings["count"]
                else "暂无评价\n\n"
            )
            + (
                "\n\n".join(
                    f"#{r['id']}  {r['title']}  ¥{display_money(r['price_cents'])}  {r['state']}"
                    for r in rows
                )
                if rows
                else "该卖家暂无可公开展示的商品。"
            ),
        )
        if profile["avatar_thumb"]:
            win.content_text.configure(state="normal")
            win.avatar = tk.PhotoImage(master=win, data=profile["avatar_thumb"])
            win.content_text.image_create("1.0", image=win.avatar)
            win.content_text.configure(state="disabled")
        win.actions.add("查看真实评价", lambda: self.public_reviews(row["seller_id"]))
        if self.user["role"] == "user" and row["seller_id"] != self.user["id"]:
            following = row["seller_id"] in [
                r["seller_id"] for r in self.read(lambda: self.market.social.follows(self.token))
            ]
            win.actions.add(
                "取消关注" if following else "关注卖家",
                lambda: self.command(
                    lambda: self.market.social.follow(self.token, row["seller_id"], not following),
                    "关注状态已更新",
                ),
            )
        return win

    def report_dialog(self, row):
        product_id = row.get("product_id", row["id"])
        version = row.get("product_version", row.get("version"))
        win = TextDialog(self.root, f"举报商品 #{product_id}：请填写原因", editable=True)
        reason = win.content_text
        again = tk.BooleanVar()
        ttk.Checkbutton(
            win, text="前次已处理，依据新的事实再次举报（待处理的举报会合并）", variable=again
        ).grid(row=3, column=0, sticky="w", padx=14, pady=8)
        key = uuid.uuid4().hex

        def send():
            reason_text, new_report = reason.get("1.0", "end"), again.get()
            result = self.command(
                lambda: self.market.reports.report_product(
                    self.token, product_id, version, reason_text, key, again=new_report
                ),
                "举报已受理（重复请求不新增记录）；请在举报与通知中查看",
            )
            if result is not None:
                win.destroy()

        win.actions.add("提交举报", send)
        return win

    def booking_list(self, seller):
        rows = self.read(lambda: self.market.queries.reservations(self.token, seller=seller))
        columns = [
            ("id", "预约号", 65),
            ("title", "商品", 220),
            ("buyer_name", "买家", 100),
            ("seller_name", "卖家", 100),
            ("delivery_mode", "交付", 80),
            ("fulfillment_phase", "履约状态", 130),
            ("state", "预约状态", 100),
            ("product_state", "商品状态", 100),
            ("deadline", "等待确认截止", 180),
        ]

        def act(row, action):
            self.booking_action(row, action)

        actions = [
            ("订单详情 / 履约", self.booking_detail, lambda _r: True),
            (
                "交接协商",
                self.handoff_dialog,
                lambda r: r["delivery_mode"] == "面交" and r["fulfillment_phase"] == "面交中",
            ),
        ]
        if seller:
            for title, action, state in [
                ("同意预约", "agree", "待确认"),
                ("拒绝预约", "reject", "待确认"),
                ("确认交易完成", "complete", "待交易"),
            ]:
                actions.append(
                    (
                        title,
                        lambda r, a=action: act(r, a),
                        lambda r, s=state, a=action: (
                            r["state"] == s and (a != "complete" or r.get("delivery_legacy"))
                        ),
                    )
                )
        else:
            actions.append(
                (
                    "取消等待预约",
                    lambda r: act(r, "cancel_waiting"),
                    lambda r: r["state"] == "待确认",
                )
            )
        actions.append(
            (
                "取消交易",
                lambda r: act(r, "cancel_trade"),
                lambda r: r["fulfillment_phase"] in ("待发货", "面交中"),
            )
        )

        def contact(row):
            try:
                value = self.read(lambda: self.handoff_text(self.token, row["id"]))
                window = self.popup_text("当前交易的联系与交接信息", value)
                self.contact_windows[window] = row["id"]
            except BusinessError as exc:
                self.message(str(exc), error=True)

        actions.append(
            (
                "查看对方联系方式",
                contact,
                lambda r: r["delivery_mode"] == "面交" and r["state"] == "待交易",
            )
        )
        actions.append(("卖家主页", self.seller_home, lambda _r: True))
        actions.append(
            (
                "预约时商品信息",
                lambda r: self.popup_text(
                    "预约信息快照",
                    f"预约 #{r['id']} / 商品 #{r['product_id']}\n"
                    f"{r['snapshot_title']} · {r['snapshot_category']}\n"
                    f"预约时价格：¥{display_money(r['snapshot_price_cents'])}\n{r['snapshot_description']}\n"
                    f"买家：{r['buyer_name']} · 卖家：{r['seller_name']}\n"
                    f"创建：{display_time(r['created_at'])}；结束：{display_time(r['finished_at'])}\n"
                    + (
                        "旧版记录缺少原始快照，显示升级时商品信息，不能据此认定原预约价格。"
                        if r["snapshot_origin"] == "legacy_current"
                        else "信息保留自预约创建时，不随当前商品修改。"
                    ),
                ),
                lambda _r: True,
            )
        )
        if not seller:
            actions.append(
                (
                    "举报预约商品",
                    self.report_dialog,
                    lambda r: r["state"] in ("待确认", "待交易", "已完成"),
                )
            )
        ttk.Label(
            self.content, text="打开订单详情查看邮寄/面交履约；发货后不能直接取消，须通过异常协商。"
        ).pack(anchor="w", pady=6)
        self.table = TablePanel(self.content, columns, rows, actions)

    def notifications(self):
        unread = self.read(lambda: self.market.messaging.unread(self.token))
        ttk.Label(
            self.content,
            text=f"未读咨询 {unread['messages']} 条 · 未读业务通知 {unread['notices']} 条",
        ).pack(anchor="w", pady=4)
        notebook = ttk.Notebook(self.content)
        notebook.pack(fill="both", expand=True)
        chats = ttk.Frame(notebook)
        notebook.add(chats, text="商品咨询")
        self.table = self.consultations(chats)
        notices = ttk.Frame(notebook)
        notebook.add(notices, text="业务通知")
        self.table = TablePanel(
            notices,
            [("content", "业务通知", 650), ("created_at", "时间", 180)],
            self.read(lambda: self.market.messaging.notices(self.token)),
            [("查看并标为已读", lambda r: self.notice_detail(r), lambda _r: True)],
        )
        for title, rows, columns in [
            (
                "站内通知",
                self.read(lambda: self.market.queries.notifications(self.token)),
                [("id", "编号", 60), ("content", "处理结果", 740), ("created_at", "时间", 180)],
            ),
            (
                "我的举报",
                self.read(lambda: self.market.queries.reports(self.token)),
                [
                    ("id", "编号", 60),
                    ("title", "商品", 220),
                    ("reason", "举报原因", 350),
                    ("state", "处理状态", 100),
                ],
            ),
        ]:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)
            actions = []
            if title == "我的举报":
                actions = [
                    (
                        "查看处理记录",
                        self.report_record,
                        lambda _r: True,
                    ),
                    (
                        "再次举报",
                        self.report_dialog,
                        lambda r: r["state"] != "待处理" and r["product_state"] == P.PUBLISHED,
                    ),
                ]
            self.table = TablePanel(frame, columns, rows, actions)
        self.bind_notebook_table(notebook)

    def bind_notebook_table(self, notebook):
        def select(_event=None):
            selected = notebook.select()
            if selected:
                frame = notebook.nametowidget(selected)
                self.table = next(
                    (w for w in frame.winfo_children() if isinstance(w, TablePanel)), None
                )

        notebook.bind("<<NotebookTabChanged>>", select)
        select()

    def report_record(self, row):
        handler = f"管理员 #{row['handled_by']}" if row["handled_by"] else "尚未处理"
        return self.popup_text(
            "举报处理记录",
            f"举报 #{row['id']} · 商品 #{row['product_id']}：{row['title']}\n\n"
            f"举报时间：{display_time(row['created_at'])}\n举报原因：{row['reason']}\n\n"
            f"处理状态：{row['state']}\n处理人：{handler}\n"
            f"处理时间：{display_time(row['handled_at'])}\n"
            f"处理理由：{row['decision_reason'] or '暂无处理结论'}\n\n"
            "每条举报分别核实，处理结果同时保存在站内通知中。",
        )

    def reason_dialog(self, title, submit):
        window = TextDialog(self.root, title + "：请填写理由", editable=True)

        def confirm():
            reason = window.content_text.get("1.0", "end").strip()
            if not reason:
                window.feedback.set("请填写明确的处理理由")
                return
            if submit(reason) is not None:
                window.destroy()

        window.actions.add("确认处理", confirm)
        return window

    def review_products(self):
        self.table = TablePanel(
            self.content,
            self.product_columns(),
            self.read(lambda: self.market.queries.pending_products(self.token)),
            [
                ("查看详情", self.product_detail, lambda _r: True),
                (
                    "审核通过",
                    lambda r: self.product_command(self.market.products.review, r, True),
                    lambda _r: True,
                ),
                (
                    "审核驳回",
                    lambda r: self.reason_dialog(
                        "审核驳回",
                        lambda reason: self.product_command(
                            self.market.products.review, r, False, reason
                        ),
                    ),
                    lambda _r: True,
                ),
            ],
        )

    def review_reports(self):
        ttk.Label(
            self.content,
            text="每条举报分别核实并通知；已停售不恢复销售。已售出争议核实结案并保留交易终态。",
        ).pack(anchor="w", pady=6)

        def resolve(row, violation):
            return self.reason_dialog(
                "核实违规" if violation else "驳回举报",
                lambda reason: self.command(
                    lambda: self.market.reports.resolve_report(
                        self.token, row["id"], row["product_version"], violation, reason
                    ),
                    "举报处理结果已在站内通知举报人",
                ),
            )

        self.table = TablePanel(
            self.content,
            [
                ("id", "举报号", 65),
                ("title", "商品", 190),
                ("reason", "原因", 350),
                ("state", "处理状态", 100),
                ("product_state", "商品状态", 100),
            ],
            self.read(lambda: self.market.queries.reports(self.token)),
            [
                ("驳回举报", lambda r: resolve(r, False), lambda r: r["state"] == "待处理"),
                (
                    "核实违规 / 停售",
                    lambda r: resolve(r, True),
                    lambda r: r["state"] == "待处理" and r["product_state"] in VIOLATION_ALLOWED,
                ),
            ],
        )

    def statistics(self):
        bar = ActionBar(self.content)
        bar.pack(fill="x", pady=6)
        keys = [
            ("stat_day", "统计日期", 11),
            ("log_product", "商品编号", 7),
            ("log_actor", "操作人", 9),
            ("log_start", "起始日期", 11),
            ("log_end", "结束日期", 11),
        ]
        variables = {key: self.field(bar, key, label, width) for key, label, width in keys}

        def apply():
            self.filters.update({key: var.get() for key, var in variables.items()})
            self.refresh()

        bar.add("查询", apply)
        stats = self.read(
            lambda: self.market.queries.statistics(self.token, self.filters.get("stat_day") or None)
        )
        categories = (
            "  /  ".join(f"{r['category']} {r['count']}件" for r in stats["categories"])
            or "暂无在售商品"
        )
        ttk.Label(
            self.content,
            text=(
                f"{stats['day']} 新上架：{stats['daily_new']}件（按商品去重）"
                f"  |  当前在售：{categories}\n"
                f"当日预约：{stats['reservations']} · 完成：{stats['completed']} · "
                f"举报处理：{stats['reports_handled']} · 当前待处理：{stats['reports_pending']}"
            ),
            foreground=BLUE,
            wraplength=1100,
        ).pack(anchor="w", pady=8)
        rows = self.read(
            lambda: self.market.queries.logs(
                self.token,
                *(
                    self.filters.get(k, "")
                    for k in ("log_product", "log_actor", "log_start", "log_end")
                ),
            )
        )
        self.table = TablePanel(
            self.content,
            [
                ("id", "日志号", 65),
                ("actor", "操作人", 90),
                ("action", "操作", 140),
                ("product_id", "商品编号", 80),
                ("target", "目标", 140),
                ("created_at", "时间", 180),
            ],
            rows,
            [
                (
                    "完整日志",
                    lambda r: self.popup_text(
                        "完整操作日志", json.dumps(r, ensure_ascii=False, indent=2)
                    ),
                    lambda _r: True,
                )
            ],
        )
        ttk.Label(
            self.content,
            text="日期格式 YYYY-MM-DD；操作人可填用户ID或 SYSTEM；最多显示最新1000条日志。",
            foreground="#617487",
        ).pack(anchor="w", pady=5)

    def handoff_text(self, token, reservation_id):
        data = self.market.bookings.handoff(token, reservation_id)
        place = f"{data['region']} / {data['point']}" if data["point"] else "未预设，请双方协商"
        changes = self.market.handoff.changes(token, reservation_id)
        pending = next((row for row in changes if row["state"] == "待确认"), None)
        extra = (
            f"\n\n待确认变更：{pending['region']} / {pending['point']}\n"
            f"{pending['meeting_time']} · {pending['note']}"
            if pending
            else ""
        )
        return (
            f"对方联系方式：{data['contact']}\n交接地点：{place}\n"
            f"约定时间：{data['time'] or '继续协商'}\n交接说明：{data['note'] or '无'}\n\n"
            "请在线下公共地点交接，平台不收取交易款。"
        ) + extra

    def refresh_contacts(self):
        """Revoke displayed contact data after local actions or another app's changes."""
        for window, reservation_id in list(self.contact_windows.items()):
            if not window.winfo_exists():
                self.contact_windows.pop(window, None)
                continue
            try:
                value = self.read(lambda rid=reservation_id: self.handoff_text(self.token, rid))
            except BusinessError:
                value = "联系方式已隐藏，交接地点也已隐藏：预约已结束或当前账号无权访问。"
                self.contact_windows.pop(window, None)
            self.set_contact_text(window, value)

    @staticmethod
    def set_contact_text(window, value):
        if window.content_text.get("1.0", "end").strip() != value.strip():
            window.content_text.configure(state="normal")
            window.content_text.delete("1.0", "end")
            window.content_text.insert("1.0", value)
            window.content_text.configure(state="disabled")

    def tick(self):
        """Poll outside Tk; notify instead of destroying a partially filled page."""
        try:
            if time.monotonic() - self.last_collection >= 10:
                gc.collect()
                self.last_collection = time.monotonic()
            if self.poll_future and self.poll_future.done():
                future, token = self.poll_future, self.poll_token
                self.poll_future = None
                revision, contacts = future.result()
                if self.token == token and self.poll_epoch == self.revision_epoch:
                    if self.last_revision is not None and revision != self.last_revision:
                        self.pending_refresh = True
                        if self.table is not None:
                            self.table.stale = True
                            self.table.selection_changed()
                        self.message("数据已有更新；填写中的内容已保留，请点击刷新核对最新列表")
                    self.last_revision = revision
                    for window, value in contacts:
                        if window.winfo_exists():
                            self.set_contact_text(window, value)
            if (
                self.token
                and not self.command_busy
                and self.poll_future is None
                and time.monotonic() >= self.next_poll
            ):
                self.next_poll = time.monotonic() + 1.5
                token = self.token
                targets = list(self.contact_windows.items())
                delivery_targets = list(self.delivery_windows.items())

                def poll():
                    self.market.auth.me(token)
                    self.market.bookings.sweep()
                    with self.market.store.connect() as conn:
                        revision = conn.execute(
                            "SELECT COALESCE(MAX(id),0) FROM audit_logs"
                        ).fetchone()[0]
                    values = []
                    for window, rid in targets:
                        try:
                            value = self.handoff_text(token, rid)
                        except BusinessError:
                            value = (
                                "联系方式已隐藏，交接地点也已隐藏：预约已结束或当前账号无权访问。"
                            )
                        values.append((window, value))
                    for window, rid in delivery_targets:
                        try:
                            value = self.order_text(token, rid)
                        except BusinessError:
                            value = "订单隐私信息已隐藏，请重新登录或刷新订单。"
                        values.append((window, value))
                    return revision, values

                self.poll_token = token
                self.poll_epoch = self.revision_epoch
                self.poll_future = self.pool.submit(poll)
        except BusinessError as exc:
            self.poll_future = None
            if "会话" in str(exc) and self.token and not self.command_busy:
                self.logout()
                self.message("会话已失效，请重新登录")
            else:
                self.message(str(exc), error=True)
        except Exception as exc:
            self.poll_future = None
            self.callback_error(type(exc), exc, exc.__traceback__)
        if not self.closed:
            self.timer_id = self.root.after(100, self.tick)

    def close(self):
        if self.command_busy:
            self.message("正在处理操作，请完成后再关闭", error=True)
            return
        if self.poll_future and not self.poll_future.done():
            self.message("正在完成后台检查，完成后自动关闭")
            future = self.poll_future
            try:
                self.read(future.result)
            except Exception:
                logging.exception("Background check failed during shutdown")
        self.closed = True
        self.root.after_cancel(self.timer_id)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.root.destroy()
        gc.collect()
        if self.gc_was_enabled:
            gc.enable()

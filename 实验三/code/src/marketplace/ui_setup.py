"""First-install administrator setup for source and windowed Windows bundles."""

import logging
import tkinter as tk
from tkinter import ttk

from .domain import BusinessError
from .ui_theme import HERO, configure_ui
from .ui_widgets import ScrollArea


def startup_error(message, database):
    """Use a visible root: hidden owners can strand native Windows error dialogs."""
    root = tk.Tk()
    configure_ui(root)
    root.title("拾光 · 启动失败")
    root.geometry("720x330")
    root.minsize(600, 300)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Button(frame, text="关闭", command=root.destroy).pack(side="bottom", anchor="e", pady=10)
    message_label = ttk.Label(
        frame,
        text=f"{message}\n\n日志位置：{database.with_suffix('.log')}",
        wraplength=640,
    )
    message_label.pack(fill="both", expand=True)
    frame.bind("<Configure>", lambda e: message_label.configure(wraplength=max(100, e.width - 48)))
    root.mainloop()


def first_admin(root, market, on_ready, username="admin"):
    """Keep installation provisioning separate from ordinary user registration."""
    root.title("拾光 · 首次启动设置")
    configure_ui(root)
    root.geometry("720x510")
    root.minsize(620, 450)
    footer = ttk.Frame(root, padding=18)
    footer.pack(side="bottom", fill="x")
    area = ScrollArea(root)
    area.pack(fill="both", expand=True)
    frame = area.inner
    ttk.Label(frame, text="初始化本机管理员", font=HERO).pack(anchor="w", pady=(0, 12))
    explanation = ttk.Label(
        frame,
        text="仅首次使用空库时设置。普通用户之后在登录页注册，不能取得管理员权限。",
        wraplength=610,
    )
    explanation.pack(anchor="w", pady=(0, 10))
    fields = []
    for label, value, secret in [
        ("管理员登录名", username, False),
        ("密码（8至128个字符）", "", True),
        ("再次输入密码", "", True),
    ]:
        ttk.Label(frame, text=label).pack(anchor="w")
        variable = tk.StringVar(value=value)
        entry = ttk.Entry(frame, textvariable=variable, show="*" if secret else "")
        entry.pack(fill="x", pady=(3, 8))
        fields.append(variable)
    feedback = tk.StringVar()
    ttk.Label(footer, textvariable=feedback, foreground="#A83932", wraplength=570).pack(
        anchor="w", pady=4
    )

    def save():
        if fields[1].get() != fields[2].get():
            feedback.set("两次输入密码不一致，请重新输入")
            return
        try:
            market.auth.provision_admin(fields[0].get(), fields[1].get(), first_only=True)
        except BusinessError as exc:
            feedback.set(str(exc))
            return
        except Exception:
            logging.exception("First administrator setup failed")
            feedback.set("初始化未完成，请检查数据目录是否可写；详情已写入运行日志")
            return
        area.destroy()
        footer.destroy()
        on_ready()

    ttk.Button(footer, text="创建管理员并进入登录", command=save).pack(anchor="w", pady=8)
    location = ttk.Label(
        frame, text=f"数据保存位置：{market.store.path}", style="Hint.TLabel", wraplength=560
    )
    location.pack(anchor="w", pady=6)
    frame.bind(
        "<Configure>",
        lambda e: [
            label.configure(wraplength=max(160, e.width - 32)) for label in (explanation, location)
        ],
        add="+",
    )

"""Resizable content regions and reserved action areas for Tk windows."""

import tkinter as tk
from tkinter import ttk

from .ui_theme import BODY


class ScrollArea(ttk.Frame):
    """Scroll a form without moving the footer or losing the focused entry."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#F3F7FB")
        scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, padding=16)
        window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.bind("<MouseWheel>", self.wheel)

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")


class FormDialog(tk.Toplevel):
    """Small labeled forms share scrollable content and a persistent action row."""

    def __init__(self, app, title, specs, submit, *, caption="保存", initial=None, on_saved=None):
        super().__init__(app.root)
        self.title(title)
        self.geometry("620x530")
        self.minsize(480, 400)
        self.transient(app.root)
        self.app = app
        self.feedback = tk.StringVar()
        footer = ttk.Frame(self, padding=12)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, textvariable=self.feedback, foreground="#B14736", wraplength=450).pack(
            fill="x"
        )
        self.actions = ActionBar(footer)
        self.actions.pack(fill="x")
        self.actions.add("取消", self.destroy)
        area = ScrollArea(self)
        area.pack(fill="both", expand=True)
        self.form = area.inner
        self.fields = {}
        self.inputs = {}
        for key, label, choices in specs:
            ttk.Label(self.form, text=label, wraplength=420).pack(anchor="w", pady=(8, 3))
            var = tk.StringVar(value=(initial or {}).get(key, ""))
            self.fields[key] = var
            if choices:
                entry = ttk.Combobox(self.form, textvariable=var, values=choices, state="readonly")
            else:
                entry = ttk.Entry(
                    self.form, textvariable=var, show="•" if "password" in key else ""
                )
            entry.pack(fill="x", pady=(0, 4))
            self.inputs[key] = entry

        def save():
            values = {key: var.get() for key, var in self.fields.items()}
            result = app.command(lambda: submit(values), title + "已保存")
            if result is not None and self.winfo_exists():
                self.destroy()
                if on_saved is not None:
                    try:
                        on_saved(result)
                    except Exception:
                        import logging

                        logging.exception("Saved form follow-up display failed")
                        app.message("操作已保存；详情刷新失败，请关闭后重新打开", error=True)

        self.save_button = self.actions.add(caption, save)
        self.center_job = self.after_idle(lambda: TextDialog.center(self))

    def destroy(self):
        if self.center_job is not None:
            self.after_cancel(self.center_job)
            self.center_job = None
        super().destroy()


class ActionBar(ttk.Frame):
    """Wrap controls into rows instead of clipping the rightmost actions."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.controls = []
        self.layout_job = None
        self.bind("<Configure>", self.layout)

    def add(self, text, command):
        style = "TButton"
        if any(word in text for word in ("关闭", "取消", "返回", "刷新", "清空")):
            style = "Secondary.TButton"
        if any(word in text for word in ("删除", "拒绝", "违规", "限制", "取消交易")):
            style = "Danger.TButton"
        button = ttk.Button(self, text=text, command=command, style=style)
        return self.add_widget(button)

    def add_widget(self, widget):
        self.controls.append(widget)
        if self.layout_job is None:
            self.layout_job = self.after_idle(self.layout_idle)
        return widget

    def layout_idle(self):
        self.layout_job = None
        self.layout()

    def destroy(self):
        if self.layout_job is not None:
            self.after_cancel(self.layout_job)
            self.layout_job = None
        super().destroy()

    def layout(self, _event=None):
        width = max(self.winfo_width() - 28, 100)
        x, y, row_height = 0, 4, 0
        for widget in self.controls:
            requested = widget.winfo_reqwidth()
            if x and x + requested > width:
                y += row_height + 8
                x = row_height = 0
            widget.place(x=x + 4, y=y, width=min(requested, width), height=widget.winfo_reqheight())
            x += requested + 8
            row_height = max(row_height, widget.winfo_reqheight())
        height = y + row_height + 8
        if self.winfo_reqheight() != height:
            self.configure(height=height)


class TextDialog(tk.Toplevel):
    """Footer is allocated before scrollable text; all actions remain reachable."""

    def __init__(self, root, title, text="", *, editable=False):
        super().__init__(root)
        self.title(title)
        self.geometry("760x510")
        self.minsize(480, 360)
        self.transient(root)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        content = ttk.Frame(self, padding=14)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.content_text = tk.Text(
            content,
            height=6,
            width=20,
            wrap="word",
            font=BODY,
            padx=10,
            pady=10,
        )
        self.content_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(content, command=self.content_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.content_text.configure(yscrollcommand=scroll.set)
        self.content_text.insert("1.0", text)
        if not editable:
            self.content_text.configure(state="disabled")
        self.feedback = tk.StringVar()
        ttk.Label(
            self, textvariable=self.feedback, wraplength=440, foreground="#B14736", padding=(14, 2)
        ).grid(row=1, column=0, sticky="ew")
        self.actions = ActionBar(self, padding=(14, 4))
        self.actions.grid(row=2, column=0, sticky="ew")
        self.actions.add("关闭", self.destroy)
        self.center_job = self.after_idle(self.center)

    def center(self):
        self.center_job = None
        if not self.winfo_exists():
            return
        root = self.master
        x = max(0, min(root.winfo_rootx() + 50, self.winfo_screenwidth() - self.winfo_width() - 20))
        y = max(
            0, min(root.winfo_rooty() + 60, self.winfo_screenheight() - self.winfo_height() - 60)
        )
        self.geometry(f"+{x}+{y}")

    def destroy(self):
        if self.center_job is not None:
            self.after_cancel(self.center_job)
            self.center_job = None
        super().destroy()

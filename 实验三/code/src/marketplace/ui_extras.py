"""Image and handoff place editors for the Tk desktop."""

import tkinter as tk
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

from .domain import BusinessError
from .media import MAX_BYTES, MAX_PHOTOS, prepare_photo
from .ui_widgets import ActionBar, ScrollArea


class PhotoGallery(tk.Toplevel):
    def __init__(self, parent, photos, index=0):
        super().__init__(parent)
        self.title("商品图片")
        self.geometry("640x475")
        self.transient(parent)
        self.photos = photos
        self.index = index
        bar = ActionBar(self, padding=10)
        bar.pack(side="bottom", fill="x")
        self.previous = bar.add("上一张", lambda: self.move(-1))
        self.next = bar.add("下一张", lambda: self.move(1))
        bar.add("放大查看", self.zoom)
        bar.add("关闭图片", self.destroy)
        self.caption = ttk.Label(self, anchor="center")
        self.caption.pack(side="bottom", fill="x", pady=5)
        self.preview = ttk.Label(self, anchor="center")
        self.preview.pack(fill="both", expand=True)
        self.move(0)

    def move(self, step):
        if not self.photos:
            self.preview.configure(text="暂无图片")
            self.previous.state(["disabled"])
            self.next.state(["disabled"])
            return
        self.index += step
        try:
            with Image.open(BytesIO(self.photos[self.index])) as image:
                image.thumbnail((560, 350))
                self.picture = ImageTk.PhotoImage(image, master=self)
        except (OSError, ValueError):
            self.preview.configure(image="", text="图片无法显示，请重试或联系管理员")
            return
        self.preview.configure(image=self.picture)
        self.caption.configure(text=f"第 {self.index + 1} / {len(self.photos)} 张")
        self.previous.configure(state="normal" if self.index else "disabled")
        self.next.configure(state="normal" if self.index + 1 < len(self.photos) else "disabled")

    def zoom(self):
        if not self.photos:
            return
        window = tk.Toplevel(self)
        window.title("放大图片 · 拖动滚动条查看")
        window.geometry("850x650")
        canvas = tk.Canvas(window, bg="#F3F7FB", highlightthickness=0)
        horizontal = ttk.Scrollbar(window, orient="horizontal", command=canvas.xview)
        vertical = ttk.Scrollbar(window, command=canvas.yview)
        canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        horizontal.pack(side="bottom", fill="x")
        vertical.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        try:
            with Image.open(BytesIO(self.photos[self.index])) as image:
                window.picture = ImageTk.PhotoImage(image, master=window)
            canvas.create_image(0, 0, image=window.picture, anchor="nw")
            canvas.configure(scrollregion=canvas.bbox("all"))
        except (OSError, ValueError):
            canvas.create_text(250, 100, text="图片无法显示")


class PhotoPicker(ttk.Frame):
    def __init__(self, parent, editor, photos):
        super().__init__(parent)
        self.editor = editor
        self.photos = [
            (row["id"], row["content"], f"已保存图片 {index}")
            for index, row in enumerate(photos, 1)
        ]
        bar = ActionBar(self)
        bar.pack(fill="x")
        self.choice = ttk.Combobox(bar, width=15, state="readonly")
        bar.add_widget(self.choice)
        self.add_button = bar.add("添加图片", self.add_photo)
        self.preview_button = bar.add("预览", self.preview)
        self.cover_button = bar.add("设为封面", self.cover)
        self.remove_button = bar.add("移除", self.remove)
        self.left_button = bar.add("前移", lambda: self.reorder(-1))
        self.right_button = bar.add("后移", lambda: self.reorder(1))
        self.refresh()

    def refresh(self, index=0):
        self.choice.configure(
            values=[f"{'封面 · ' if i == 0 else ''}{row[2]}" for i, row in enumerate(self.photos)]
        )
        if self.photos:
            self.choice.current(min(index, len(self.photos) - 1))
        else:
            self.choice.set("暂无图片")
        self.add_button.configure(state="normal" if len(self.photos) < MAX_PHOTOS else "disabled")
        for button in (self.preview_button, self.cover_button, self.remove_button):
            button.configure(state="normal" if self.photos else "disabled")
        self.left_button.configure(state="normal" if self.photos else "disabled")
        self.right_button.configure(state="normal" if self.photos else "disabled")

    @staticmethod
    def load(path):
        try:
            with open(path, "rb") as file:
                data = file.read(MAX_BYTES + 1)
        except OSError as exc:
            raise BusinessError("无法读取图片文件，请重新选择") from exc
        normalized, _ = prepare_photo(data)
        return data, normalized

    def add_photo(self):
        if len(self.photos) >= MAX_PHOTOS:
            return
        path = filedialog.askopenfilename(
            parent=self.editor,
            title="选择商品图片",
            filetypes=[("JPEG / PNG", "*.jpg *.jpeg *.png")],
        )
        if not path:
            return
        try:
            data, normalized = self.editor.app.read(lambda: self.load(path))
        except BusinessError as exc:
            self.editor.feedback.set(str(exc))
            return
        self.photos.append((data, normalized, Path(path).name))
        self.editor.feedback.set("")
        self.refresh(len(self.photos) - 1)

    def preview(self):
        PhotoGallery(self.editor, [row[1] for row in self.photos], self.choice.current())

    def cover(self):
        self.photos.insert(0, self.photos.pop(self.choice.current()))
        self.refresh()

    def remove(self):
        index = self.choice.current()
        self.photos.pop(index)
        self.refresh(index)

    def reorder(self, step):
        index = self.choice.current()
        target = index + step
        if 0 <= target < len(self.photos):
            self.photos[index], self.photos[target] = self.photos[target], self.photos[index]
            self.refresh(target)


class PlaceEditor(tk.Toplevel):
    def __init__(self, app, row=None):
        super().__init__(app.root)
        self.title("编辑交接地点" if row else "新增交接地点")
        self.geometry("570x380")
        self.minsize(480, 350)
        self.transient(app.root)
        footer = ttk.Frame(self, padding=12)
        footer.pack(side="bottom", fill="x")
        self.feedback = tk.StringVar()
        ttk.Label(footer, textvariable=self.feedback, foreground="#B14736", wraplength=420).pack(
            fill="x"
        )
        area = ScrollArea(self)
        area.pack(fill="both", expand=True)
        frame = area.inner
        row = row or {}
        self.region = tk.StringVar(value=row.get("region", ""))
        self.point = tk.StringVar(value=row.get("point", ""))
        self.note = tk.StringVar(value=row.get("note", ""))
        self.city = tk.StringVar(value=row.get("city", ""))
        self.district = tk.StringVar(value=row.get("district", ""))
        for label, var in (
            ("城市", self.city),
            ("区县", self.district),
            ("旧记录区域/大致范围（可保留）", self.region),
            ("具体公共交接点", self.point),
            ("交接说明（仅待交易双方）", self.note),
        ):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(5, 3))
            ttk.Entry(frame, textvariable=var).pack(fill="x", pady=(0, 5))

        def save():
            region, point, note = self.region.get(), self.point.get(), self.note.get()
            city, district = self.city.get(), self.district.get()
            result = app.command(
                lambda: app.market.places.save(
                    app.token,
                    region,
                    point,
                    place_id=row.get("id"),
                    note=note,
                    city=city,
                    district=district,
                ),
                "交接地点已保存",
            )
            if result is not None:
                self.destroy()

        ttk.Button(footer, text="保存地点", command=save).pack(side="left", pady=4)
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="left", padx=8)

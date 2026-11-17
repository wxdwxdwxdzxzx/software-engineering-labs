"""Shared, system-font typography for every Tk interpreter and child window."""

from tkinter import font, ttk

BODY = "MarketBody"
STRONG = "MarketStrong"
HINT = "MarketHint"
TITLE = "MarketTitle"
HERO = "MarketHero"
MONO = "MarketMono"


def configure_ui(root):
    """Configure named fonts once; point sizes follow Tk's current DPI scaling."""
    if getattr(root, "_market_fonts", None):
        return root._market_fonts
    available = {name.casefold(): name for name in font.families(root)}
    default = font.nametofont("TkDefaultFont", root=root).actual("family")
    family = next(
        (
            available[name.casefold()]
            for name in (
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "微软雅黑",
                "Noto Sans CJK SC",
                "SimSun",
            )
            if name.casefold() in available
        ),
        default,
    )
    monospace = available.get(
        "consolas", font.nametofont("TkFixedFont", root=root).actual("family")
    )
    fonts = {}
    for name, size, weight, face in (
        (BODY, 11, "normal", family),
        (STRONG, 11, "bold", family),
        (HINT, 10, "normal", family),
        (TITLE, 16, "bold", family),
        (HERO, 20, "bold", family),
        (MONO, 11, "normal", monospace),
    ):
        fonts[name] = font.Font(root=root, name=name, family=face, size=size, weight=weight)
    root._market_fonts = fonts  # Keep Tcl named fonts alive for all child widgets.
    for name in font.names(root):
        if name.startswith("Tk"):
            font.nametofont(name, root=root).configure(
                family=monospace if name == "TkFixedFont" else family,
                size=10 if name in ("TkSmallCaptionFont", "TkTooltipFont") else 11,
                weight="bold" if name == "TkHeadingFont" else "normal",
            )
    root.option_add("*Font", BODY)
    # Let label styles select title/helper roles instead of overriding them with BODY.
    root.option_add("*TLabel.font", "")
    for control in ("Text", "Entry", "Listbox", "Menu", "Spinbox", "TEntry", "TCombobox"):
        root.option_add(f"*{control}.font", BODY)
    root.option_add("*TCombobox*Listbox.font", BODY)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=BODY)
    for name in (
        "TLabel",
        "TButton",
        "TEntry",
        "TCombobox",
        "TSpinbox",
        "TCheckbutton",
        "TRadiobutton",
        "TNotebook.Tab",
        "Treeview",
    ):
        style.configure(name, font=BODY)
    style.configure("Treeview.Heading", font=STRONG)
    style.configure("TLabelframe.Label", font=STRONG)
    style.configure("Title.TLabel", font=HERO)
    style.configure("Page.TLabel", font=TITLE)
    style.configure("Hint.TLabel", font=HINT)
    padding = root.winfo_pixels("4p")
    row_height = fonts[BODY].metrics("linespace") + padding * 2
    style.configure("Treeview", rowheight=row_height)
    style.configure("Products.Treeview", rowheight=max(root.winfo_pixels("42p"), row_height))
    style.configure("TButton", width=0, padding=(root.winfo_pixels("8p"), padding))
    style.configure("TEntry", padding=padding)
    style.configure("TCombobox", padding=padding)
    return fonts

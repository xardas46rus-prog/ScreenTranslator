"""Шрифт с кириллицей и CJK на целевой Windows; fallback для Linux-проверок."""

from tkinter import font


def text_family(root):
    families = {name.casefold(): name for name in font.families(root)}
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans CJK JP",
                 "DejaVu Sans", "Segoe UI"):
        if name.casefold() in families:
            candidate = font.Font(root, family=families[name.casefold()], size=12)
            # Некоторые минимальные X-сборки перечисляют недоступные scalable-шрифты.
            if candidate.actual("family").casefold() == name.casefold() and candidate.measure("Игрок") > 10:
                return families[name.casefold()]
    return font.nametofont("TkDefaultFont").actual("family")

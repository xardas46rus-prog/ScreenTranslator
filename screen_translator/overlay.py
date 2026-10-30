"""Управляемый оверлей: обычное перемещаемое/масштабируемое topmost-окно."""

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from .presentation import clamp_font_size
from .windowing import exclude_from_capture
from .fonts import text_family


class TranslationOverlay:
    def __init__(self, root, on_close):
        self.window = tk.Toplevel(root)
        self.font_family = text_family(root)
        self.window.title("ScreenTranslator — оверлей")
        self.window.geometry(f"520x330+{max(0, root.winfo_screenwidth()-550)}+{max(0, root.winfo_screenheight()-400)}")
        self.window.minsize(320, 200)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.9)
        self.window.protocol("WM_DELETE_WINDOW", on_close)
        self.opacity = tk.DoubleVar(value=0.9)
        self.font_size = tk.StringVar(value="18")
        bar = ttk.Frame(self.window, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text="Прозрачность").pack(side="left")
        ttk.Scale(bar, from_=0.35, to=1, variable=self.opacity, command=self.change_opacity,
                  length=110).pack(side="left", padx=4)
        ttk.Label(bar, text="Шрифт").pack(side="left", padx=(6, 2))
        ttk.Spinbox(bar, from_=10, to=36, textvariable=self.font_size, width=3,
                    command=self.change_font).pack(side="left")
        self.font_size.trace_add("write", lambda *_: self.change_font())
        ttk.Button(bar, text="В окно", command=on_close).pack(side="right")
        ttk.Label(self.window, text="Оверлей принимает клики. Перемещайте за заголовок, размер — за край.",
                  wraplength=490, padding=(6, 2)).pack(fill="x")
        self.output = ScrolledText(self.window, wrap="word", state="disabled", bg="#18202c",
                                   fg="#f3f5f8", insertbackground="white", font=(self.font_family, 18),
                                   padx=12, pady=8, relief="flat")
        self.output.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Не размещайте оверлей поверх области OCR.")
        ttk.Label(self.window, textvariable=self.status, wraplength=490, padding=6).pack(fill="x")
        self.window.update_idletasks()
        self.capture_excluded = exclude_from_capture(self.window.winfo_id())

    def change_opacity(self, value):
        self.window.attributes("-alpha", max(0.35, min(1, float(value))))

    def change_font(self):
        self.output.configure(font=(self.font_family, clamp_font_size(self.font_size.get())))

    def render(self, text):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def close(self):
        self.window.destroy()

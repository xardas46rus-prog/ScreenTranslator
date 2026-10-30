"""Русский Tkinter-интерфейс стартового прототипа."""

from queue import Empty, Queue
from threading import Event, Thread
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .providers import ProviderError, Region, TesseractOCR, make_translator
from .worker import run_session


LANGUAGES = {"Английский": ("en", "eng"), "Китайский": ("zh", "chi_sim"),
             "Русский": ("ru", "rus")}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"ScreenTranslator {__version__} — прототип")
        root.geometry("860x760")
        root.minsize(700, 640)
        self.queue = Queue(maxsize=64)
        self.stop = Event()
        self.visible = Event()
        self.visible.set()
        self.thread = None
        self.had_error = False
        self.controls = []
        self.provider = tk.StringVar(value="Без перевода")
        self.language = tk.StringVar(value="Английский")
        self.model = tk.StringVar(value="")
        self.tesseract = tk.StringVar(value="")
        self.consent = tk.BooleanVar(value=False)
        self.chat_visible = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Готово. Начните с ручного ввода или выберите область экрана.")
        self.region_vars = [tk.StringVar(value=v) for v in ("0", "0", "640", "240")]
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll)

    def _build(self):
        panel = ttk.Frame(self.root, padding=12)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="ScreenTranslator — чистый стартовый прототип",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(panel, text="Новая реализация, не оригинальная v3.3.2. Перевод на русский.",
                  wraplength=780).pack(anchor="w", pady=(2, 8))
        settings = ttk.Frame(panel)
        settings.pack(fill="x")
        for column, (label, variable, values) in enumerate((
            ("Исходный язык", self.language, tuple(LANGUAGES)),
            ("Провайдер", self.provider, ("Без перевода", "Argos (локально)", "Ollama (локально)")),
        )):
            ttk.Label(settings, text=label).grid(row=0, column=column, sticky="w")
            combo = ttk.Combobox(settings, textvariable=variable, values=values, state="readonly", width=25)
            combo.grid(row=1, column=column, padx=(0, 10), sticky="w")
            self.controls.append((combo, "readonly"))
        ttk.Label(settings, text="Установленная модель Ollama").grid(row=0, column=2, sticky="w")
        entry = ttk.Entry(settings, textvariable=self.model, width=25)
        entry.grid(row=1, column=2, sticky="ew")
        self.controls.append((entry, "normal"))
        consent = ttk.Checkbutton(panel, variable=self.consent,
                                  text="Разрешаю отправлять текст выбранной локальной модели Ollama (127.0.0.1)")
        consent.pack(anchor="w", pady=6)
        self.controls.append((consent, "normal"))
        ttk.Label(panel, text=(
            "По умолчанию перевод отключён. Argos требует отдельной установки пакета и моделей. "
            "Ollama требует запущенного локального сервера и установленной модели. "
            "Облачных провайдеров и автоматической загрузки моделей нет."
        ), wraplength=780).pack(anchor="w")

        capture = ttk.LabelFrame(panel, text="Захват экрана — только выбранная область", padding=8)
        capture.pack(fill="x", pady=8)
        coordinates = ttk.Frame(capture)
        coordinates.pack(fill="x")
        for column, (name, variable) in enumerate(zip(("X", "Y", "Ширина", "Высота"), self.region_vars)):
            ttk.Label(coordinates, text=name).grid(row=0, column=column * 2)
            entry = ttk.Entry(coordinates, textvariable=variable, width=7)
            entry.grid(row=0, column=column * 2 + 1, padx=(3, 8))
            self.controls.append((entry, "normal"))
        choose = ttk.Button(coordinates, text="Выбрать мышью…", command=self.select_region)
        choose.grid(row=0, column=8)
        self.controls.append((choose, "normal"))
        path_row = ttk.Frame(capture)
        path_row.pack(fill="x", pady=6)
        ttk.Label(path_row, text="Путь к tesseract.exe (пусто = PATH):").pack(side="left")
        entry = ttk.Entry(path_row, textvariable=self.tesseract)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.controls.append((entry, "normal"))
        ttk.Label(capture, text="Для OCR нужны requirements-ocr.txt, Tesseract и языковые данные. "
                  "Не включайте окно переводчика в область захвата.", wraplength=760).pack(anchor="w")
        buttons = ttk.Frame(capture)
        buttons.pack(fill="x", pady=(6, 0))
        start = ttk.Button(buttons, text="Начать захват", command=self.start_capture)
        start.pack(side="left")
        self.controls.append((start, "normal"))
        self.stop_button = ttk.Button(buttons, text="Остановить", command=self.stop_capture, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Checkbutton(buttons, text="Чат видим — разрешить обработку", variable=self.chat_visible,
                        command=self.change_visibility).pack(side="left")
        ttk.Label(capture, text="Снимите флажок, когда чат скрыт: захват и перевод приостанавливаются. "
                  "Автоматического определения скрытого чата пока нет.", wraplength=760).pack(anchor="w", pady=(4, 0))

        ttk.Label(panel, text="Ручная проверка OCR-текста (без захвата экрана):").pack(anchor="w")
        self.input = ScrolledText(panel, height=4, wrap="word", font=("Segoe UI", 11))
        self.input.pack(fill="x", pady=4)
        self.input.insert("1.0", "Player: Hello team!\n玩家: 你好")
        manual = ttk.Button(panel, text="Обработать введённый текст", command=self.start_manual)
        manual.pack(anchor="w")
        self.controls.extend(((manual, "normal"), (self.input, "normal")))
        ttk.Label(panel, text="Сообщения: оригинал → результат. «Без перевода» сохраняет исходный текст.").pack(anchor="w", pady=(8, 2))
        self.output = ScrolledText(panel, height=8, wrap="word", font=("Segoe UI", 11), state="disabled")
        self.output.pack(fill="both", expand=True)
        ttk.Label(panel, textvariable=self.status, wraplength=780).pack(anchor="w", pady=(6, 0))

    def change_visibility(self):
        if self.chat_visible.get():
            self.visible.set()
        else:
            self.visible.clear()

    def select_region(self):
        """Выделение на основном мониторе; другие мониторы доступны через X/Y."""
        self.root.withdraw()
        selection = tk.Toplevel(self.root)
        selection.attributes("-fullscreen", True)
        selection.attributes("-topmost", True)
        selection.attributes("-alpha", 0.35)
        canvas = tk.Canvas(selection, bg="black", cursor="crosshair", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(30, 30, anchor="nw", fill="white", font=("Segoe UI", 16),
                           text="Выделите область чата. Esc — отмена.")
        start = []
        rectangle = None

        def close(event=None):
            selection.destroy()
            self.root.deiconify()

        def press(event):
            nonlocal rectangle
            start[:] = [event.x_root, event.y_root, event.x, event.y]
            rectangle = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="yellow", width=3)

        def drag(event):
            if start:
                canvas.coords(rectangle, start[2], start[3], event.x, event.y)

        def release(event):
            if start:
                values = (min(start[0], event.x_root), min(start[1], event.y_root),
                          abs(event.x_root - start[0]), abs(event.y_root - start[1]))
                if values[2] and values[3]:
                    for variable, value in zip(self.region_vars, values):
                        variable.set(str(value))
            close()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        selection.bind("<Escape>", close)
        selection.protocol("WM_DELETE_WINDOW", close)
        selection.grab_set()
        selection.focus_force()

    def start_capture(self):
        try:
            region = Region(*(int(v.get()) for v in self.region_vars))
        except ValueError as exc:
            messagebox.showerror("Область захвата", str(exc), parent=self.root)
            return
        language = LANGUAGES[self.language.get()][1]
        self.start(ocr=TesseractOCR(language, self.tesseract.get().strip()), region=region)

    def start_manual(self):
        text = self.input.get("1.0", "end-1c")
        if not text.strip():
            self.status.set("Введите текст для проверки.")
            return
        self.start(manual=text)

    def start(self, **kwargs):
        if self.thread and self.thread.is_alive():
            return
        if not self.chat_visible.get():
            self.status.set("Обработка отключена: включите флажок «Чат видим».")
            return
        try:
            translator = make_translator(self.provider.get(), model=self.model.get(), consent=self.consent.get())
        except ProviderError as exc:
            messagebox.showerror("Перевод", str(exc), parent=self.root)
            return
        while not self.queue.empty():
            self.queue.get_nowait()
        self.stop = Event()
        self.had_error = False
        self.change_visibility()
        for control, _ in self.controls:
            control.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Обработка… Для OCR строка подтверждается двумя последовательными кадрами.")
        self.thread = Thread(target=run_session, kwargs={
            "queue": self.queue, "stop": self.stop, "visible": self.visible,
            "translator": translator, "source": LANGUAGES[self.language.get()][0], **kwargs,
        }, daemon=True, name="screen-translator")
        self.thread.start()

    def stop_capture(self):
        self.stop.set()
        self.status.set("Остановка… Ждём завершения текущего OCR/перевода; загрузка Argos может занять время.")

    def poll(self):
        try:
            while True:
                result = self.queue.get_nowait()
                if result.kind == "error":
                    self.had_error = True
                    self.status.set(result.text)
                elif result.kind == "message":
                    self.output.configure(state="normal")
                    self.output.insert("end", f"{result.original}\n→ {result.text}\n\n")
                    if int(self.output.index("end-1c").split(".")[0]) > 400:
                        self.output.delete("1.0", "101.0")
                    self.output.see("end")
                    self.output.configure(state="disabled")
        except Empty:
            pass
        if self.thread is not None and not self.thread.is_alive():
            self.thread = None
            for control, state in self.controls:
                control.configure(state=state)
            self.stop_button.configure(state="disabled")
            if not self.had_error:
                self.status.set("Обработка завершена. История хранится только в памяти.")
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        self.stop.set()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def main():
    if sys.platform == "win32":
        # До создания Tk: координаты выбора области должны соответствовать пикселям MSS.
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    App(root)
    root.mainloop()

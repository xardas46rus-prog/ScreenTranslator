"""Русский Tkinter-интерфейс стартового прототипа."""

from queue import Empty, Queue
from threading import Event, Thread
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .providers import ProviderError, Region, TesseractOCR, make_translator
from .worker import LiveOptions, LiveSession, run_session
from .overlay import TranslationOverlay
from .presentation import MessageHistory, clamp_font_size
from .windowing import exclude_from_capture, overlaps
from .fonts import text_family


LANGUAGES = {"Английский": ("en", "eng"), "Китайский": ("zh", "chi_sim"),
             "Русский": ("ru", "rus")}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.font_family = text_family(root)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=self.font_family)
        root.title(f"ScreenTranslator {__version__} — прототип")
        root.geometry("920x850")
        root.minsize(760, 620)
        self.queue = Queue(maxsize=64)
        self.stop = Event()
        self.visible = Event()
        self.visible.set()
        self.thread = None
        self.session = None
        self.overlay = None
        self.history = MessageHistory()
        self.settings_collapsed = False
        self.capture_excluded = False
        self.guard_reason = ""
        self.active_provider = "Без перевода"
        self.had_error = False
        self.controls = []
        self.provider = tk.StringVar(value="Без перевода")
        self.language = tk.StringVar(value="Английский")
        self.model = tk.StringVar(value="")
        self.tesseract = tk.StringVar(value="")
        self.consent = tk.BooleanVar(value=False)
        self.chat_visible = tk.BooleanVar(value=True)
        self.fast_chat = tk.BooleanVar(value=True)
        self.output_mode = tk.StringVar(value="Отдельное окно")
        self.show_original = tk.BooleanVar(value=True)
        self.font_size = tk.StringVar(value="14")
        self.interval = tk.StringVar(value="0.25")
        self.confirmations = tk.StringVar(value="2")
        self.cooldown = tk.StringVar(value="30")
        self.metrics = tk.StringVar(value="История: только в памяти · до 100 сообщений")
        self.status = tk.StringVar(value="Готово. Начните с ручного ввода или выберите область экрана.")
        self.region_vars = [tk.StringVar(value=v) for v in ("0", "0", "640", "240")]
        self._build()
        root.update_idletasks()
        self.capture_excluded = exclude_from_capture(root.winfo_id())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll)

    def _build(self):
        panel = ttk.Frame(self.root, padding=12)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="ScreenTranslator — живой перевод", font=(self.font_family, 16, "bold")).pack(anchor="w")
        ttk.Label(panel, text="Прототип 0.2.0 · EN / 中文 → RU · оригинал v3.3.2 не импортирован").pack(anchor="w", pady=(2, 8))
        view = ttk.Frame(panel)
        view.pack(fill="x", pady=(0, 8))
        self.settings_button = ttk.Button(view, text="Свернуть настройки", command=self.toggle_settings)
        self.settings_button.pack(side="left")
        mode = ttk.Combobox(view, textvariable=self.output_mode, values=("Отдельное окно", "Оверлей"),
                            state="readonly", width=18)
        mode.pack(side="left", padx=8)
        mode.bind("<<ComboboxSelected>>", lambda event: self.change_output_mode())
        ttk.Checkbutton(view, text="Исходный текст", variable=self.show_original,
                        command=self.render_history).pack(side="left")
        ttk.Label(view, text="Шрифт").pack(side="left", padx=(8, 2))
        ttk.Spinbox(view, from_=10, to=36, width=3, textvariable=self.font_size).pack(side="left")
        ttk.Button(view, text="Очистить", command=self.clear_history).pack(side="right")
        self.settings_panel = ttk.LabelFrame(panel, text="Настройки OCR и локального перевода", padding=8)
        self.settings_panel.pack(fill="x", pady=(0, 8))
        settings = ttk.Frame(self.settings_panel)
        settings.pack(fill="x")
        for column, (label, variable, values) in enumerate((
            ("Исходный язык", self.language, tuple(LANGUAGES)),
            ("Провайдер", self.provider, ("Без перевода", "Argos (локально)", "Ollama (локально)")),
        )):
            ttk.Label(settings, text=label).grid(row=0, column=column, sticky="w")
            combo = ttk.Combobox(settings, textvariable=variable, values=values, state="readonly", width=23)
            combo.grid(row=1, column=column, padx=(0, 8), sticky="w")
            if column == 0:
                self.controls.append((combo, "readonly"))
        ttk.Label(settings, text="Локальная модель Ollama").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.model, width=23).grid(row=1, column=2, sticky="ew")
        engine = ttk.Frame(self.settings_panel)
        engine.pack(fill="x", pady=6)
        ttk.Checkbutton(engine, variable=self.consent, text="Разрешаю текст локальной Ollama (127.0.0.1)",
                        command=self.consent_changed).pack(side="left")
        ttk.Button(engine, text="Применить движок", command=self.apply_provider).pack(side="right")
        ttk.Label(self.settings_panel, text="Argos: установленная локальная модель. Ollama: локальный сервер и модель. "
                  "Автозагрузки моделей и облачных запросов нет.", wraplength=800).pack(anchor="w")
        coordinates = ttk.Frame(self.settings_panel)
        coordinates.pack(fill="x", pady=6)
        for column, (name, variable) in enumerate(zip(("X", "Y", "Ширина", "Высота"), self.region_vars)):
            ttk.Label(coordinates, text=name).grid(row=0, column=column * 2)
            entry = ttk.Entry(coordinates, textvariable=variable, width=6)
            entry.grid(row=0, column=column * 2 + 1, padx=(3, 8))
            self.controls.append((entry, "normal"))
        choose = ttk.Button(coordinates, text="Выбрать область…", command=self.select_region)
        choose.grid(row=0, column=8)
        self.controls.append((choose, "normal"))
        path_row = ttk.Frame(self.settings_panel)
        path_row.pack(fill="x")
        ttk.Label(path_row, text="tesseract.exe (пусто = PATH):").pack(side="left")
        entry = ttk.Entry(path_row, textvariable=self.tesseract)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.controls.append((entry, "normal"))
        timing = ttk.Frame(self.settings_panel)
        timing.pack(fill="x", pady=6)
        for label, variable, first, last in (
            ("Интервал OCR, с", self.interval, 0.05, 10),
            ("Подтверждения", self.confirmations, 1, 5),
            ("Окно повторов, с", self.cooldown, 1, 300),
        ):
            ttk.Label(timing, text=label).pack(side="left")
            spin = ttk.Spinbox(timing, from_=first, to=last, increment=0.05 if first == 0.05 else 1,
                               textvariable=variable, width=5)
            spin.pack(side="left", padx=(4, 12))
            self.controls.append((spin, "normal"))
        ttk.Label(self.settings_panel, text="Держите окно и оверлей ВНЕ области OCR. При пересечении захват приостанавливается, "
                  "если ОС не поддерживает исключение окна из capture. Для OCR нужны requirements-ocr.txt и Tesseract.",
                  wraplength=800).pack(anchor="w")
        self.capture_buttons = ttk.Frame(panel)
        self.capture_buttons.pack(fill="x", pady=(0, 4))
        start = ttk.Button(self.capture_buttons, text="Начать OCR", command=self.start_capture)
        start.pack(side="left")
        self.controls.append((start, "normal"))
        self.stop_button = ttk.Button(self.capture_buttons, text="Остановить", command=self.stop_capture, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Checkbutton(self.capture_buttons, text="Быстрый чат", variable=self.fast_chat,
                        command=self.change_fast_chat).pack(side="left")
        ttk.Checkbutton(self.capture_buttons, text="Чат видим (F8 в окне)", variable=self.chat_visible,
                        command=self.change_visibility).pack(side="left", padx=8)
        ttk.Label(panel, text="Скрытый чат: снимите флажок. Автоопределения нет. «Быстрый чат» выключен → обычный OCR продолжает работать.",
                  wraplength=810).pack(anchor="w", pady=(0, 6))
        self.manual_panel = ttk.Frame(panel)
        self.manual_panel.pack(fill="x")
        ttk.Label(self.manual_panel, text="Ручная проверка (без захвата):").pack(anchor="w")
        self.input = ScrolledText(self.manual_panel, height=2, wrap="word", font=(self.font_family, 11))
        self.input.pack(fill="x", pady=3)
        self.input.insert("1.0", "Player: Hello team!\n玩家: 你好")
        manual = ttk.Button(self.manual_panel, text="Обработать текст", command=self.start_manual)
        manual.pack(anchor="w")
        self.controls.extend(((manual, "normal"), (self.input, "normal")))
        ttk.Label(panel, text="Перевод · режим «Без перевода» показывает оригинал").pack(anchor="w", pady=(8, 3))
        self.output = ScrolledText(panel, height=8, wrap="word", font=(self.font_family, 14), state="disabled",
                                   bg="#f4f6f9", fg="#142135", padx=12, pady=8, relief="flat")
        self.output.pack(fill="both", expand=True)
        ttk.Label(panel, textvariable=self.status, wraplength=810).pack(anchor="w", pady=(6, 0))
        ttk.Label(panel, textvariable=self.metrics).pack(anchor="w")
        self.font_size.trace_add("write", lambda *_: self.output.configure(font=(self.font_family, clamp_font_size(self.font_size.get()))))
        self.root.bind("<F8>", lambda event: self.toggle_visibility())

    def change_visibility(self):
        if self.chat_visible.get():
            self.visible.set()
        else:
            self.visible.clear()
        if self.session:
            self.session.set_visible(self.chat_visible.get())

    def toggle_visibility(self):
        self.chat_visible.set(not self.chat_visible.get())
        self.change_visibility()

    def change_fast_chat(self):
        if self.session:
            self.session.set_fast_chat(self.fast_chat.get())

    def toggle_settings(self):
        self.settings_collapsed = not self.settings_collapsed
        if self.settings_collapsed:
            self.settings_panel.pack_forget()
        else:
            self.settings_panel.pack(fill="x", pady=(0, 8), before=self.capture_buttons)
        self.settings_button.configure(text="Показать настройки" if self.settings_collapsed else "Свернуть настройки")

    def change_output_mode(self):
        if self.output_mode.get() == "Оверлей" and self.overlay is None:
            self.overlay = TranslationOverlay(self.root, self.return_to_window)
            self.overlay.window.bind("<F8>", lambda event: self.toggle_visibility())
            self.render_history()
        elif self.output_mode.get() != "Оверлей" and self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        self.sync_capture_guard()

    def return_to_window(self):
        self.output_mode.set("Отдельное окно")
        self.change_output_mode()
        self.root.deiconify()
        self.root.lift()

    def render_history(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", self.history.render(self.show_original.get()))
        self.output.see("end")
        self.output.configure(state="disabled")
        if self.overlay:
            self.overlay.render(self.history.render(False))

    def clear_history(self):
        self.history.clear()
        self.render_history()

    def consent_changed(self):
        # Отзыв разрешения отменяет очередь Ollama, включая ещё не показанные ответы.
        if not self.consent.get() and self.active_provider == "Ollama (локально)":
            self.provider.set("Без перевода")
            self.apply_provider()

    def apply_provider(self):
        try:
            translator = make_translator(self.provider.get(), model=self.model.get(), consent=self.consent.get())
        except ProviderError as exc:
            messagebox.showerror("Перевод", str(exc), parent=self.root)
            return
        if self.session:
            self.session.set_translator(translator)
        elif self.thread and self.thread.is_alive():
            self.stop.set()
            self.status.set("Ручная проверка остановлена; следующий запуск использует выбранный движок.")
        self.active_provider = self.provider.get()
        self.status.set(f"Выбран движок: {self.active_provider}.")

    def sync_capture_guard(self):
        self.guard_reason = ""
        if not self.session:
            return
        windows = [(self.root, self.capture_excluded)]
        if self.overlay:
            windows.append((self.overlay.window, self.overlay.capture_excluded))
        for window, excluded in windows:
            if not excluded and window.winfo_viewable() and overlaps(self.session.region, (
                window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height()
            )):
                self.guard_reason = "Захват на паузе: переместите окно/оверлей за пределы области OCR."
                break
        self.session.set_blocked(bool(self.guard_reason))

    def select_region(self):
        """Выделение на основном мониторе; другие мониторы доступны через X/Y."""
        self.root.withdraw()
        selection = tk.Toplevel(self.root)
        selection.attributes("-fullscreen", True)
        selection.attributes("-topmost", True)
        selection.attributes("-alpha", 0.35)
        canvas = tk.Canvas(selection, bg="black", cursor="crosshair", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(30, 30, anchor="nw", fill="white", font=(self.font_family, 16),
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
            options = LiveOptions(interval=float(self.interval.get()), confirmations=int(self.confirmations.get()),
                                  cooldown=float(self.cooldown.get()))
        except ValueError as exc:
            messagebox.showerror("Область захвата", str(exc), parent=self.root)
            return
        language = LANGUAGES[self.language.get()][1]
        self.start(ocr=TesseractOCR(language, self.tesseract.get().strip()), region=region, options=options)

    def start_manual(self):
        text = self.input.get("1.0", "end-1c")
        if not text.strip():
            self.status.set("Введите текст для проверки.")
            return
        self.start(manual=text)

    def start(self, **kwargs):
        if self.thread and self.thread.is_alive():
            return
        if not self.chat_visible.get() and ("manual" in kwargs or self.fast_chat.get()):
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
        self.session = None
        self.had_error = False
        self.active_provider = self.provider.get()
        self.change_visibility()
        for control, _ in self.controls:
            control.configure(state="disabled")
        self.stop_button.configure(state="normal")
        source = LANGUAGES[self.language.get()][0]
        if "manual" in kwargs:
            self.status.set("Ручная проверка…")
            self.thread = Thread(target=run_session, kwargs={
                "queue": self.queue, "stop": self.stop, "visible": self.visible,
                "translator": translator, "source": source, **kwargs,
            }, daemon=True, name="screen-translator-manual")
            self.thread.start()
        else:
            self.session = LiveSession(queue=self.queue, translator=translator, source=source,
                                       fast_chat=self.fast_chat.get(), visible=self.chat_visible.get(), **kwargs)
            self.thread = self.session
            self.stop = self.session.stop
            self.sync_capture_guard()
            self.session.start()

    def stop_capture(self):
        self.stop.set()
        if self.session:
            self.session.cancel()
        self.status.set("Остановка… Ждём завершения текущего OCR/перевода; загрузка Argos может занять время.")

    def poll(self):
        self.sync_capture_guard()
        changed = False
        try:
            while True:
                result = self.queue.get_nowait()
                if self.session and not self.session.accepts(result):
                    continue
                if result.kind == "error":
                    self.had_error = True
                    self.status.set(result.text)
                elif result.kind == "message":
                    if not self.stop.is_set():
                        self.history.append(result.original, result.text)
                        changed = True
        except Empty:
            pass
        if changed:
            self.render_history()
        if self.session:
            stats = self.session.stats
            self.metrics.set(f"Кадров OCR: {stats['frames']} · В очереди: {stats['pending']}/{self.session.options.max_pending} · "
                             f"Переведено: {stats['translated']} · Отменено/устарело: {stats['dropped']}")
            if not self.had_error and not self.stop.is_set():
                if self.guard_reason:
                    self.status.set(self.guard_reason)
                elif self.fast_chat.get() and not self.chat_visible.get():
                    self.status.set("Чат скрыт: OCR и перевод чата приостановлены.")
                else:
                    mode = "быстрый чат" if self.fast_chat.get() else "обычный OCR"
                    self.status.set(f"{'Перевод работает' if stats['translated'] else 'Распознавание запущено'}: {mode} · {self.active_provider}")
            if self.overlay:
                self.overlay.status.set(self.status.get())
        if self.thread is not None and not self.thread.is_alive():
            self.thread = None
            self.session = None
            for control, state in self.controls:
                control.configure(state=state)
            self.stop_button.configure(state="disabled")
            if not self.had_error:
                self.status.set("Обработка завершена. История хранится только в памяти.")
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        self.stop.set()
        if self.session:
            self.session.cancel()
        if self.overlay:
            self.overlay.close()
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

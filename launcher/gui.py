"""Русский GUI; операции выполняются по кнопке в одном отменяемом потоке."""

import os
import shutil
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid

from .install import Installation, atomic_json, plain
from .network import Updates
from .trust import TRUSTED_KEYS
from .update import UpdateError, parse_json


def data_root():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "ScreenTranslator" / "launcher"


class App:
    def __init__(self, root, settings_dir=None):
        self.root = root
        self.settings_dir = plain(settings_dir or data_root())
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = plain(self.settings_dir / "preferences.json")
        preferences = {}
        if self.settings_path.is_file():
            try:
                preferences = parse_json(self.settings_path.read_bytes())
                if not isinstance(preferences, dict):
                    preferences = {}
            except (UpdateError, OSError):
                preferences = {}
        self.directory = tk.StringVar(value=str(preferences.get("directory", self.settings_dir / "applications")))
        self.profile = tk.StringVar(value=preferences.get("profile", "argos") if preferences.get("profile", "argos") in ("argos", "ocr") else "argos")
        self.automatic = tk.BooleanVar(value=preferences.get("automatic") is True)
        self.status = tk.StringVar(value="Готово. Онлайн-обновления отключены: доверенный ключ пока не задан." if not TRUSTED_KEYS else "Готово. Проверка обновлений — по кнопке.")
        self.events = Queue(maxsize=4)
        self.cancel = Event()
        self.thread = None
        self.manager = None
        self.updates = Updates(TRUSTED_KEYS)
        self.pending = None
        root.title("ScreenTranslator — лаунчер (test only)")
        root.geometry("810x330")
        panel = ttk.Frame(root, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Лаунчер ScreenTranslator", font=("Segoe UI", 16)).pack(anchor="w")
        ttk.Label(panel, text="Каталог версий лаунчера (отдельно от моделей и portable-папок):").pack(anchor="w", pady=(12, 0))
        row = ttk.Frame(panel)
        row.pack(fill="x")
        self.path_entry = ttk.Entry(row, textvariable=self.directory)
        self.path_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Выбрать…", command=self.choose).pack(side="left")
        options = ttk.Frame(panel)
        options.pack(fill="x", pady=12)
        ttk.Label(options, text="Профиль:").pack(side="left")
        self.profile_box = ttk.Combobox(options, textvariable=self.profile, values=("argos", "ocr"), state="readonly", width=10)
        self.profile_box.pack(side="left", padx=8)
        ttk.Checkbutton(options, text="Проверять при старте (необязательно)", variable=self.automatic, command=self.save).pack(side="left")
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        self.buttons = []
        for title, command in (("Запустить переводчик", self.launch), ("Проверить обновления", self.check),
                               ("Установить", self.install), ("Откат", self.rollback)):
            button = ttk.Button(buttons, text=title, command=command)
            button.pack(side="left", padx=(0, 6))
            self.buttons.append(button)
        ttk.Button(buttons, text="Отмена", command=self.cancel.set).pack(side="left")
        extra = ttk.Frame(panel)
        extra.pack(fill="x", pady=8)
        ttk.Button(extra, text="Запустить существующий Portable…", command=self.launch_legacy).pack(side="left")
        ttk.Button(extra, text="Остановить переводчик…", command=self.stop).pack(side="left", padx=8)
        ttk.Label(panel, textvariable=self.status, wraplength=760).pack(anchor="w", pady=8)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll)
        if self.automatic.get() and TRUSTED_KEYS:
            root.after_idle(self.check)

    def save(self):
        atomic_json(self.settings_path, {"directory": self.directory.get(), "profile": self.profile.get(),
                                         "automatic": self.automatic.get()})

    def choose(self):
        if self.thread is not None:
            return
        folder = filedialog.askdirectory(title="Отдельный каталог управляемых версий")
        if folder:
            self.directory.set(folder)
            self.pending = None
            self.save()

    def get_manager(self):
        root = plain(self.directory.get())
        if self.manager is not None and (self.manager.root != root or self.manager.profile != self.profile.get()):
            if self.manager.busy():
                raise UpdateError("Сначала завершите переводчик перед сменой каталога/профиля")
            self.manager = None
        if self.manager is None:
            self.manager = Installation(root, TRUSTED_KEYS, self.profile.get())
        self.save()
        return self.manager

    def work(self, operation):
        if self.thread is not None:
            return
        try:
            manager = self.get_manager()
        except (UpdateError, OSError) as exc:
            self.status.set(str(exc))
            return
        self.cancel = Event()
        for button in self.buttons:
            button.configure(state="disabled")
        self.path_entry.configure(state="disabled")
        self.profile_box.configure(state="disabled")
        self.status.set("Выполняется… «Отмена» прервёт загрузку/подготовку до переключения версии.")
        def run():
            try:
                message = operation(manager, self.cancel)
            except Exception as exc:
                message = f"Операция не выполнена: {exc}"
            self.events.put(message)
        self.thread = Thread(target=run, daemon=True)
        self.thread.start()

    def check(self):
        def run(manager, cancel):
            manifest = self.updates.check(manager.profile, cancel)
            manager.candidate(manifest.envelope)
            self.pending = manifest
            return f"Доступна {manifest.version} ({manifest.profile}), {manifest.size // (1024 * 1024)} МБ. Нажмите «Установить»."
        self.pending = None
        self.work(run)

    def install(self):
        pending = self.pending
        if pending is None:
            self.status.set("Сначала проверьте обновления: неподписанные ZIP не устанавливаются.")
            return
        if not messagebox.askyesno("Установка", f"Установить {pending.version} ({pending.profile})? Модели не изменяются."):
            return
        def run(manager, cancel):
            manager.candidate(pending.envelope)
            if shutil.disk_usage(manager.root).free < pending.size * 2 + 64 * 1024 * 1024:
                raise UpdateError("Недостаточно места для загрузки и staging")
            folder = manager.root / "staging" / uuid.uuid4().hex
            folder.mkdir()
            archive = folder / "download.zip"
            with archive.open("xb") as stream:
                self.updates.transport.read(pending.url, pending.size, cancel, stream, exact=True)
            result = manager.install(pending.envelope, archive, cancel)
            self.pending = None
            return f"Установлена версия {result}. Для запуска нажмите «Запустить переводчик»."
        self.work(run)

    def launch(self):
        self.work(lambda manager, cancel: (manager.launch(), "Переводчик запущен.")[1])

    def launch_legacy(self):
        if self.thread is not None:
            return
        folder = filedialog.askdirectory(title="Каталог, содержащий ScreenTranslator/ScreenTranslator.exe")
        if folder and messagebox.askyesno("Запуск Portable", "Запустить выбранный существующий Portable? Его подпись лаунчер не подтверждает."):
            self.work(lambda manager, cancel: (manager.launch(folder), "Существующий Portable запущен; его каталог не изменён.")[1])

    def rollback(self):
        if messagebox.askyesno("Явный откат", "Вернуться к предыдущей проверенной установленной версии? Защита от сетевого downgrade сохранится."):
            self.work(lambda manager, cancel: (manager.rollback(), "Выполнен откат. Модели и прежние каталоги сохранены.")[1])

    def stop(self):
        if self.manager is not None and messagebox.askyesno("Завершение", "Завершить только процесс переводчика, запущенный этим лаунчером? Сначала завершите работу в его окне."):
            self.work(lambda manager, cancel: (manager.stop(), "Переводчик остановлен.")[1])

    def poll(self):
        try:
            message = self.events.get_nowait()
        except Empty:
            pass
        else:
            self.status.set(message)
            if self.thread is not None:
                self.thread.join()
            self.thread = None
            for button in self.buttons:
                button.configure(state="normal")
            self.path_entry.configure(state="normal")
            self.profile_box.configure(state="readonly")
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        if self.thread is not None:
            self.cancel.set()
            self.status.set("Отмена запрошена. Дождитесь завершения операции и закройте окно снова.")
            return
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

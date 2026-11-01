"""Русский GUI; операции выполняются по кнопке в одном отменяемом потоке."""

import os
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .install import Installation, atomic_json, plain
from .network import Updates
from .flow import UpdateFlow
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
        self.external = preferences.get("external", "") if isinstance(preferences.get("external", ""), str) else ""
        self.installed_info = tk.StringVar(value="Управляемая версия: не установлена")
        self.available_info = tk.StringVar(value="Доступная версия: не проверялась · канал stable")
        self.external_info = tk.StringVar()
        self.status = tk.StringVar(value="Готово. Онлайн-обновления отключены: доверенный ключ пока не задан." if not TRUSTED_KEYS else "Готово. Проверка обновлений — по кнопке.")
        self.events = Queue(maxsize=4)
        self.cancel = Event()
        self.thread = None
        self.manager = None
        self.updates = Updates(TRUSTED_KEYS)
        self.flow = UpdateFlow(self.updates)
        self.pending = None
        self.last_selection = (self.directory.get(), self.profile.get())
        root.title("ScreenTranslator — лаунчер (test only)")
        root.geometry("860x490")
        root.minsize(810, 450)
        panel = ttk.Frame(root, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Лаунчер ScreenTranslator", font=("Segoe UI", 16)).pack(anchor="w")
        ttk.Label(panel, text="Каталог версий лаунчера (отдельно от моделей и portable-папок):").pack(anchor="w", pady=(12, 0))
        row = ttk.Frame(panel)
        row.pack(fill="x")
        self.path_entry = ttk.Entry(row, textvariable=self.directory)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.path_entry.bind("<FocusOut>", lambda event: self.show_unchecked())
        ttk.Button(row, text="Выбрать…", command=self.choose).pack(side="left")
        options = ttk.Frame(panel)
        options.pack(fill="x", pady=12)
        ttk.Label(options, text="Профиль:").pack(side="left")
        self.profile_box = ttk.Combobox(options, textvariable=self.profile, values=("argos", "ocr"), state="readonly", width=10)
        self.profile_box.pack(side="left", padx=8)
        self.profile_box.bind("<<ComboboxSelected>>", lambda event: self.show_unchecked())
        ttk.Checkbutton(options, text="Проверять при старте (необязательно)", variable=self.automatic, command=self.save).pack(side="left")
        for variable in (self.installed_info, self.available_info, self.external_info):
            ttk.Label(panel, textvariable=variable, wraplength=810).pack(anchor="w", pady=3)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        self.buttons = []
        for title, command in (("Запустить переводчик", self.launch), ("Проверить обновления", self.check),
                               ("Установить", self.install), ("Откат", self.rollback)):
            button = ttk.Button(buttons, text=title, command=command)
            button.pack(side="left", padx=(0, 6))
            self.buttons.append(button)
        self.cancel_button = ttk.Button(buttons, text="Отмена", command=lambda: self.cancel.set())
        self.cancel_button.pack(side="left")
        extra = ttk.Frame(panel)
        extra.pack(fill="x", pady=8)
        ttk.Button(extra, text="Запустить существующий Portable…", command=self.launch_legacy).pack(side="left")
        ttk.Button(extra, text="Остановить переводчик…", command=self.stop).pack(side="left", padx=8)
        ttk.Button(panel, text="Первоначальная подписанная установка…", command=self.bootstrap).pack(anchor="w", pady=4)
        ttk.Label(panel, textvariable=self.status, wraplength=760).pack(anchor="w", pady=8)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll)
        self.refresh_info()
        self.update_install_button()
        if self.automatic.get() and TRUSTED_KEYS:
            root.after_idle(self.check)

    def save(self):
        atomic_json(self.settings_path, {"directory": self.directory.get(), "profile": self.profile.get(),
                                         "automatic": self.automatic.get(), "external": self.external})

    def update_install_button(self):
        self.buttons[2].configure(state="normal" if self.pending is not None and self.thread is None else "disabled")

    def refresh_info(self):
        self.external_info.set(f"Внешняя программа — не управляется лаунчером: {self.external}" if self.external
                               else "Внешняя программа: не выбрана (её файлы лаунчер не обновляет)")
        try:
            root = plain(self.directory.get())
            if not (root / "launcher-store.json").is_file():
                self.installed_info.set(f"Управляемая версия: не установлена · профиль {self.profile.get()}")
                return
            manager = self.get_manager()
            state = manager.state()
            installed = state["current"]["version"] if state["current"] else "не установлена"
            suffix = " · выполнен откат" if state["current"] and state["highwater"] != installed else ""
            self.installed_info.set(f"Управляемая версия: {installed} · {manager.profile} · stable{suffix}")
        except (UpdateError, OSError) as exc:
            self.installed_info.set(f"Управляемая версия: недоступна ({exc})")

    def show_unchecked(self):
        selection = (self.directory.get(), self.profile.get())
        if self.thread is None and selection != self.last_selection:
            self.last_selection = selection
            self.pending = None
            self.available_info.set("Доступная версия: не проверялась · канал stable")
            self.refresh_info()
            self.update_install_button()

    def bootstrap(self):
        if self.thread is not None:
            return
        if not messagebox.askyesno("Первоначальная установка", "Выберите отдельный пустой каталог. Внешний EXE и модели останутся на месте. Проверим официальный подписанный Release; скачивание потребует отдельного подтверждения."):
            return
        folder = filedialog.askdirectory(title="Отдельный пустой каталог для подписанной установки")
        if folder:
            self.directory.set(folder)
            self.show_unchecked()
            self.check()

    def choose(self):
        if self.thread is not None:
            return
        folder = filedialog.askdirectory(title="Отдельный каталог управляемых версий")
        if folder:
            self.directory.set(folder)
            self.pending = None
            self.save()
            self.show_unchecked()

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

    def work(self, operation, on_success=None, manager=None):
        if self.thread is not None:
            return
        try:
            manager = manager or self.get_manager()
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
                event = (True, operation(manager, self.cancel), on_success)
            except Exception as exc:
                event = (False, f"Операция не выполнена: {exc}", None)
            self.events.put(event)
        self.thread = Thread(target=run, daemon=True)
        self.thread.start()

    def check(self):
        if self.thread is not None:
            return
        def ready(offer):
            manifest = offer.manifest
            self.refresh_info()
            self.available_info.set(f"Доступная версия: {manifest.version} · stable · {manifest.profile} · {manifest.size / (1024 * 1024):.1f} МБ · подпись Ed25519 подтверждена")
            self.pending = None if offer.current else manifest
            if offer.rolled_back:
                return "Вы используете откат. Этот Release уже проверялся; повторная установка запрещена защитой replay. Ожидайте более новую версию."
            if offer.current:
                return "Установлена актуальная версия. Новых обновлений нет."
            return "Подписанное обновление доступно. Нажмите «Установить», чтобы подтвердить загрузку."
        self.pending = None
        self.work(self.flow.check, ready)

    def install(self):
        pending = self.pending
        if pending is None:
            self.status.set("Сначала проверьте обновления: неподписанные ZIP не устанавливаются.")
            return
        if not messagebox.askyesno("Установка", f"Скачать и установить {pending.version} ({pending.profile})? Модели и внешний EXE не изменяются. После первой управляемой установки проверенная версия будет запущена."):
            return
        def run(manager, cancel):
            return self.flow.install(manager, pending, cancel, launch_first=True)
        def ready(result):
            self.pending = None
            self.refresh_info()
            if result.launch_error:
                return f"Версия {result.version} установлена и сохранена, но запуск не выполнен: {result.launch_error}"
            return f"Установлена версия {result.version}. " + ("Первая управляемая версия запущена." if result.started else "Нажмите «Запустить переводчик».")
        self.work(run, ready)

    def launch(self):
        def run(manager, cancel):
            if manager.state()["current"] is None and self.external:
                manager.launch(self.external)
                return "Внешняя программа запущена — не управляется лаунчером. Для обновлений используйте мастер отдельной подписанной установки."
            manager.launch()
            return "Управляемый переводчик запущен."
        self.work(run)

    def launch_legacy(self):
        if self.thread is not None:
            return
        folder = filedialog.askdirectory(title="Каталог, содержащий ScreenTranslator/ScreenTranslator.exe")
        if folder and messagebox.askyesno("Запуск Portable", "Запустить выбранный существующий Portable? Его подпись лаунчер не подтверждает."):
            def ready(_):
                self.external = str(Path(folder).resolve())
                self.save()
                self.refresh_info()
                return "Внешняя программа — не управляется лаунчером. Каталог не изменён; для обновлений выберите первоначальную подписанную установку."
            self.work(lambda manager, cancel: manager.launch(folder), ready)

    def rollback(self):
        if messagebox.askyesno("Явный откат", "Вернуться к предыдущей проверенной установленной версии? Защита от сетевого downgrade сохранится."):
            def ready(_):
                self.refresh_info()
                self.pending = None
                return "Выполнен откат. Модели и прежние каталоги сохранены, replay-защита не сброшена."
            self.work(lambda manager, cancel: manager.rollback(), ready)

    def stop(self):
        if self.manager is not None and messagebox.askyesno("Завершение", "Завершить только процесс переводчика, запущенный этим лаунчером? Сначала завершите работу в его окне."):
            self.work(lambda manager, cancel: (manager.stop(), "Переводчик остановлен.")[1], manager=self.manager)

    def poll(self):
        try:
            success, value, callback = self.events.get_nowait()
        except Empty:
            pass
        else:
            if self.thread is not None:
                self.thread.join()
            self.thread = None
            for button in self.buttons:
                button.configure(state="normal")
            self.path_entry.configure(state="normal")
            self.profile_box.configure(state="readonly")
            try:
                message = callback(value) if success and callback is not None else value
                self.status.set(message)
            except (UpdateError, OSError) as exc:
                self.status.set(f"Операция завершена, но статус не удалось сохранить: {exc}")
            self.update_install_button()
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

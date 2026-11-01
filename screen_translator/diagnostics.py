"""Явные локальные проверки. Не запускаются при обычном старте."""

import importlib.util
import importlib
import json
import sys
import time

from . import __version__


def report():
    # Наличие пакета не подтверждает совместимость DLL или установку модели.
    modules = {name: importlib.util.find_spec(name) is not None
               for name in ("tkinter", "mss", "PIL", "pytesseract", "argostranslate")}
    return json.dumps({"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
                       "modules": modules}, ensure_ascii=True)


def gui_smoke_test():
    """Настоящий Tk/оверлей и ручной конвейер с исходным текстом, без провайдеров."""
    import tkinter as tk
    from .gui import App

    # Проверяем также реальные импорты упакованных DLL/динамических модулей.
    # Модели и OCR не запускаем; отсутствующие необязательные пакеты допустимы.
    for module in ("mss", "PIL.Image", "pytesseract", "argostranslate.translate"):
        if importlib.util.find_spec(module.split(".")[0]) is not None:
            importlib.import_module(module)

    root = tk.Tk()
    app = App(root)
    try:
        root.update()
        app.input.delete("1.0", "end")
        app.input.insert("1.0", "Player: Hello\nИгрок: Привет\n玩家: 你好")
        app.start_manual()
        deadline = time.monotonic() + 10
        while app.thread is not None and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        if app.thread is not None:
            raise RuntimeError("Не завершён ручной конвейер")
        for text in ("Player: Hello", "Игрок: Привет", "玩家: 你好"):
            if text not in app.output.get("1.0", "end"):
                raise RuntimeError(f"Нет текста в окне: {text}")
        app.output_mode.set("Оверлей")
        app.change_output_mode()
        root.update()
        if app.overlay is None or "玩家: 你好" not in app.overlay.output.get("1.0", "end"):
            raise RuntimeError("Оверлей не сохранил историю")
        app.return_to_window()
        root.update()
        if app.overlay is not None:
            raise RuntimeError("Не выполнен возврат из оверлея")
        print("GUI smoke OK: EN/RU/中文, ручной конвейер, оверлей, возврат")
    finally:
        app.close()

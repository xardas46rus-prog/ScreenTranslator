"""Включение GUI-проверок: SCREENTRANSLATOR_GUI_TESTS=1 и доступный дисплей."""

import os
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("SCREENTRANSLATOR_GUI_TESTS") == "1", "GUI-проверки требуют дисплей и явное включение")
class GuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from screen_translator.gui import App
        self.root = tk.Tk()
        self.app = App(self.root)
        self.root.update()

    def tearDown(self):
        self.app.close()

    def finish_session(self):
        deadline = time.monotonic() + 4
        while self.app.thread is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertIsNone(self.app.thread, "Фоновая обработка не завершилась")

    def test_russian_window_and_manual_processing(self):
        self.assertIn("прототип", self.root.title())
        self.app.input.delete("1.0", "end")
        self.app.input.insert("1.0", "Player: Hello\nPlayer: Hello\nИгрок: Привет\n玩家: 你好")
        self.app.start_manual()
        self.finish_session()
        output = self.app.output.get("1.0", "end")
        self.assertEqual(output.count("→ Player: Hello"), 1)
        self.assertIn("→ Игрок: Привет", output)
        self.assertIn("→ 玩家: 你好", output)

    def test_missing_ocr_keeps_window_usable(self):
        with patch.dict("sys.modules", {"mss": None}):
            self.app.start_capture()
            self.finish_session()
        self.assertIn("requirements-ocr", self.app.status.get())
        self.assertEqual(str(self.app.stop_button["state"]), "disabled")
        self.app.start_manual()
        self.finish_session()
        self.assertIn("→ Player:", self.app.output.get("1.0", "end"))

    def test_ollama_without_consent_does_not_start(self):
        self.app.provider.set("Ollama (локально)")
        self.app.model.set("local-model")
        with patch("screen_translator.gui.messagebox.showerror") as show:
            self.app.start_manual()
        self.assertIsNone(self.app.thread)
        self.assertIn("Подтвердите", show.call_args.args[1])

    def test_hidden_chat_blocks_manual_processing(self):
        self.app.chat_visible.set(False)
        self.app.change_visibility()
        self.app.start_manual()
        self.assertIsNone(self.app.thread)
        self.assertIn("Обработка отключена", self.app.status.get())


if __name__ == "__main__":
    unittest.main()

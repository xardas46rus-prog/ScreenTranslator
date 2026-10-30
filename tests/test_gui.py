"""Включение GUI-проверок: SCREENTRANSLATOR_GUI_TESTS=1 и доступный дисплей."""

import os
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("SCREENTRANSLATOR_GUI_TESTS") == "1", "GUI-проверки требуют дисплей и явное включение")
class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tkinter не установлен")
        from screen_translator.gui import App
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Дисплей недоступен: {exc}")
        self.app = App(self.root)
        self.root.update()

    def tearDown(self):
        session = self.app.session
        self.app.close()
        if session:
            session.join(3)
            self.assertFalse(session.is_alive())

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
        # Область вне окна: здесь проверяется ошибка зависимости, не self-capture guard.
        self.app.region_vars[0].set("-1000")
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

    def test_collapsing_settings_only_hides_settings_not_application(self):
        self.app.history.append("Hello", "Привет")
        self.app.render_history()
        self.app.toggle_settings()
        self.root.update()
        self.assertFalse(self.app.settings_panel.winfo_ismapped())
        self.assertTrue(self.root.winfo_ismapped())
        self.assertNotEqual(self.root.state(), "iconic")
        self.assertTrue(self.app.output.winfo_ismapped())
        self.assertIn("Привет", self.app.output.get("1.0", "end"))
        self.app.toggle_settings()
        self.root.update()
        self.assertTrue(self.app.settings_panel.winfo_ismapped())

    def test_output_mode_and_fast_chat_switch_preserve_live_session(self):
        self.app.region_vars[0].set("-1000")
        with patch("screen_translator.providers.TesseractOCR.read", return_value="Player: Hello"):
            self.app.start_capture()
            session = self.app.session
            self.app.history.append("Player: Hello", "Игрок: Привет")
            self.app.output_mode.set("Оверлей")
            self.app.change_output_mode()
            self.root.update()
            self.assertIs(self.app.session, session)
            self.assertIn("Игрок: Привет", self.app.overlay.output.get("1.0", "end"))
            self.app.overlay.font_size.set("abc")
            self.app.overlay.change_opacity("0.1")
            self.app.fast_chat.set(False)
            self.app.change_fast_chat()
            self.assertFalse(session.snapshot().fast_chat)
            self.assertFalse(session.stop.is_set())
            self.app.return_to_window()
            self.assertIsNone(self.app.overlay)
            self.assertIs(self.app.session, session)
            self.app.stop_capture()
            self.finish_session()

    def test_intersecting_window_pauses_capture_until_moved(self):
        self.app.capture_excluded = False
        self.app.region_vars[0].set(str(self.root.winfo_rootx()))
        self.app.region_vars[1].set(str(self.root.winfo_rooty()))
        with patch("screen_translator.providers.TesseractOCR.read", return_value="Hello") as read:
            self.app.start_capture()
            self.assertTrue(self.app.session.snapshot().blocked)
            self.root.update()
            self.assertFalse(read.called)
            self.root.withdraw()
            self.app.sync_capture_guard()
            self.assertFalse(self.app.session.snapshot().blocked)
            self.app.stop_capture()
            self.finish_session()
            self.root.deiconify()

    def test_revoking_ollama_consent_switches_live_engine_to_original(self):
        from screen_translator.providers import OriginalText
        self.app.region_vars[0].set("-1000")
        self.app.provider.set("Ollama (локально)")
        self.app.model.set("local-model")
        self.app.consent.set(True)
        with patch("screen_translator.gui.make_translator", side_effect=lambda *args, **kwargs: OriginalText()), \
             patch("screen_translator.providers.TesseractOCR.read", return_value="Hello"):
            self.app.start_capture()
            session = self.app.session
            previous_epoch = session.snapshot().epoch
            self.app.consent.set(False)
            self.app.consent_changed()
            self.assertEqual(self.app.active_provider, "Без перевода")
            self.assertGreater(session.snapshot().epoch, previous_epoch)
            self.app.stop_capture()
            self.finish_session()


if __name__ == "__main__":
    unittest.main()

"""Синтетические кадры и синхронизация потоков без игры, GUI и моделей."""

from queue import Empty, Queue
from threading import Event
import time
import unittest

from screen_translator.providers import OriginalText, Region
from screen_translator.worker import LiveOptions, LiveSession


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Не дождались ожидаемого состояния конвейера")


class SequenceOCR:
    def __init__(self, frames):
        self.frames = frames
        self.calls = 0

    def read(self, region):
        index = min(self.calls, len(self.frames) - 1)
        self.calls += 1
        return self.frames[index]


class LiveTests(unittest.TestCase):
    def start_session(self, ocr, translator=None, **kwargs):
        options = kwargs.pop("options", LiveOptions(interval=0.05, confirmations=1))
        queue = Queue(maxsize=64)
        session = LiveSession(queue=queue, ocr=ocr, region=Region(0, 0, 10, 10),
                              translator=translator or OriginalText(), source="en", options=options, **kwargs)
        self.addCleanup(self.finish, session)
        session.start()
        return session

    def finish(self, session):
        session.cancel()
        session.join(3)
        self.assertFalse(session.is_alive(), "Потоки не завершились после остановки")

    def results(self, session):
        results = []
        while True:
            try:
                results.append(session.queue.get_nowait())
            except Empty:
                return results

    def test_multiline_scroll_noise_and_short_chinese(self):
        ocr = SequenceOCR(["Alice: hello", "Alice: hello", "Alice: hello\n好", "Alice: hello\n好",
                           "好\nИгрок: Привет", "好\nИгрок: Привет", "|||\n123", ""])
        session = self.start_session(ocr, options=LiveOptions(interval=0.05, confirmations=2))
        wait_for(lambda: session.stats["frames"] >= 8)
        wait_for(lambda: session.stats["translated"] == 3)
        self.assertEqual([r.original for r in self.results(session) if r.kind == "message"],
                         ["Alice: hello", "好", "Игрок: Привет"])

    def test_slow_translation_does_not_block_ocr_and_queue_is_bounded(self):
        entered, release = Event(), Event()
        class Slow:
            def translate(_, text, source):
                entered.set()
                release.wait(3)
                return "Перевод"
        class OCR:
            calls = 0
            def read(ocr, region):
                ocr.calls += 1
                return f"Player: message {ocr.calls}"
        session = self.start_session(OCR(), Slow(), options=LiveOptions(interval=0.05, confirmations=1, max_pending=2))
        # Cleanup releases the provider before joining the session, even on assertion failure.
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(3))
        wait_for(lambda: session.stats["frames"] >= 8)
        self.assertFalse(release.is_set())
        self.assertEqual(session.stats["translated"], 0)
        self.assertLessEqual(session.stats["pending"], 2)
        self.assertGreaterEqual(session.stats["dropped"], 4)
        session.cancel()
        release.set()

    def test_fast_chat_off_continues_ordinary_ocr_even_when_chat_is_hidden(self):
        ocr = SequenceOCR(["12345"])
        session = self.start_session(ocr, visible=False)
        time.sleep(0.12)
        self.assertEqual(ocr.calls, 0)
        session.set_fast_chat(False)
        wait_for(lambda: session.stats["translated"] == 1)
        self.assertTrue(session.is_alive())
        self.assertEqual([r.text for r in self.results(session) if r.kind == "message"], ["12345"])

    def test_hide_and_show_during_ocr_discards_old_frame(self):
        entered, release = Event(), Event()
        class OCR:
            calls = 0
            def read(ocr, region):
                ocr.calls += 1
                if ocr.calls == 1:
                    entered.set()
                    release.wait(3)
                    return "Ghost: 假消息"
                return "Fresh: 你好"
        session = self.start_session(OCR(), options=LiveOptions(interval=0.05, confirmations=2))
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(3))
        session.set_visible(False)
        session.set_visible(True)
        release.set()
        wait_for(lambda: session.stats["translated"] == 1)
        self.assertEqual([r.original for r in self.results(session) if r.kind == "message"], ["Fresh: 你好"])

    def test_cancelled_translation_is_retried_after_visibility_returns(self):
        entered, release = Event(), Event()
        class SlowFirst:
            calls = 0
            def translate(provider, text, source):
                provider.calls += 1
                if provider.calls == 1:
                    entered.set()
                    release.wait(3)
                    return "Устаревший ответ"
                return "Новый ответ"
        provider = SlowFirst()
        session = self.start_session(SequenceOCR(["Hello team"]), provider)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(3))
        session.set_visible(False)
        session.set_visible(True)
        release.set()
        wait_for(lambda: session.stats["translated"] == 1)
        self.assertEqual([r.text for r in self.results(session) if r.kind == "message"], ["Новый ответ"])

    def test_visibility_pause_preserves_delivered_dedup_and_new_messages(self):
        ocr = SequenceOCR(["Hello team"])
        session = self.start_session(ocr)
        wait_for(lambda: session.stats["translated"] == 1 and session.stats["frames"] >= 3)
        session.set_visible(False)
        before = ocr.calls
        time.sleep(0.12)
        self.assertLessEqual(ocr.calls - before, 1)
        session.set_visible(True)
        wait_for(lambda: ocr.calls >= before + 3)
        self.assertEqual(session.stats["translated"], 1)
        ocr.frames = ["Hello team\n玩家: 好"]
        wait_for(lambda: session.stats["translated"] == 2)
        self.assertEqual([r.original for r in self.results(session) if r.kind == "message"], ["Hello team", "玩家: 好"])

    def test_provider_switch_discards_late_reply_and_reprocesses_current_text(self):
        entered, release = Event(), Event()
        class Old:
            def translate(_, text, source):
                entered.set()
                release.wait(3)
                return "Старый перевод"
        session = self.start_session(SequenceOCR(["Hello team"]), Old())
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(3))
        session.set_translator(OriginalText())
        release.set()
        wait_for(lambda: session.stats["translated"] == 1)
        messages = [r for r in self.results(session) if r.kind == "message"]
        self.assertEqual([r.text for r in messages], ["Hello team"])
        self.assertTrue(session.accepts(messages[0]))

    def test_expired_translation_is_not_published(self):
        entered, release = Event(), Event()
        class Clock:
            now = 0
            def __call__(self): return self.now
        clock = Clock()
        class Slow:
            def translate(_, text, source):
                entered.set()
                release.wait(3)
                return text
        session = self.start_session(SequenceOCR(["Old message", "New message"]), Slow(), clock=clock)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(3))
        clock.now = 20
        release.set()
        wait_for(lambda: session.stats["translated"] >= 1)
        self.assertNotIn("Old message", [r.text for r in self.results(session) if r.kind == "message"])
        self.assertGreaterEqual(session.stats["dropped"], 1)

    def test_ocr_error_is_visible_and_new_session_can_restart(self):
        class Broken:
            def read(_, region): raise RuntimeError("нет языковой модели")
        session = self.start_session(Broken())
        wait_for(lambda: not session.is_alive())
        errors = [r for r in self.results(session) if r.kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("нет языковой модели", errors[0].text)
        self.assertTrue(session.accepts(errors[0]))
        restarted = self.start_session(SequenceOCR(["Working again"]))
        wait_for(lambda: restarted.stats["translated"] == 1)

    def test_translation_error_stops_both_threads_and_is_reported(self):
        class Broken:
            def translate(_, text, source): raise RuntimeError("Argos недоступен")
        session = self.start_session(SequenceOCR(["Hello"]), Broken())
        wait_for(lambda: not session.is_alive())
        errors = [r for r in self.results(session) if r.kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Argos недоступен", errors[0].text)

    def test_stop_during_ocr_error_does_not_report_a_late_failure(self):
        entered, release = Event(), Event()
        class OCR:
            def read(_, region):
                entered.set()
                release.wait(3)
                raise RuntimeError("OCR timeout")
        session = self.start_session(OCR())
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(3))
        session.cancel()
        release.set()
        wait_for(lambda: not session.is_alive())
        self.assertEqual(self.results(session), [])

    def test_self_capture_block_pauses_and_resumes(self):
        session = self.start_session(SequenceOCR(["Hello"]))
        session.set_blocked(True)
        before = session.stats["frames"]
        time.sleep(0.12)
        self.assertLessEqual(session.stats["frames"] - before, 1)
        session.set_blocked(False)
        wait_for(lambda: session.stats["translated"] >= 1)

    def test_live_options_reject_unbounded_or_invalid_limits(self):
        for kwargs in ({"interval": 0}, {"interval": float("nan")}, {"confirmations": 0},
                       {"max_pending": 0}, {"max_pending": 10000}, {"stale_after": -1}, {"cooldown": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LiveOptions(**kwargs)


if __name__ == "__main__":
    unittest.main()

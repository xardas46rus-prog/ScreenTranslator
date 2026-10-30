from queue import Queue
from threading import Event
import unittest

from screen_translator.providers import OriginalText, Region
from screen_translator.worker import Result, publish, run_session


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.queue = Queue(maxsize=64)
        self.stop = Event()
        self.visible = Event()
        self.visible.set()

    def run_session(self, **kwargs):
        run_session(queue=self.queue, stop=self.stop, visible=self.visible,
                    translator=kwargs.pop("translator", OriginalText()), source="en", **kwargs)
        results = []
        while not self.queue.empty():
            results.append(self.queue.get_nowait())
        return results

    def test_manual_end_to_end_filters_and_deduplicates(self):
        results = self.run_session(manual="Player: Hello\n|||\nPlayer: Hello\n玩家: 你好")
        self.assertEqual([r.text for r in results if r.kind == "message"], ["Player: Hello", "玩家: 你好"])
        self.assertEqual(results[-1].kind, "done")

    def test_hidden_chat_never_calls_ocr(self):
        self.visible.clear()
        class OCR:
            def read(_, region):
                raise AssertionError("Hidden chat was captured")
        # Event.wait sets stop deterministically after the first paused iteration.
        def wait(interval):
            self.stop.set()
            return True
        self.stop.wait = wait
        self.assertEqual(self.run_session(ocr=OCR(), region=Region(0, 0, 10, 10)), [Result("done")])

    def test_screen_ocr_confirms_frames_and_stops(self):
        calls = []
        class OCR:
            def read(_, region):
                calls.append(region)
                if len(calls) == 3:
                    self.stop.set()
                return "Hello team"
        results = self.run_session(ocr=OCR(), region=Region(1, 2, 30, 40), interval=0)
        self.assertEqual(len(calls), 3)
        self.assertEqual([r.text for r in results if r.kind == "message"], ["Hello team"])

    def test_hidden_during_ocr_does_not_translate(self):
        class OCR:
            def read(_, region):
                self.visible.clear()
                self.stop.set()
                return "Ghost text"
        self.assertEqual(self.run_session(ocr=OCR(), region=Region(0, 0, 10, 10)), [Result("done")])

    def test_errors_stop_session_and_are_reported(self):
        class BrokenTranslator:
            def translate(_, text, source):
                raise RuntimeError("Модель не установлена")
        results = self.run_session(manual="Hello team", translator=BrokenTranslator())
        self.assertEqual(results, [Result("error", text="Модель не установлена"), Result("done")])

    def test_stop_discards_late_translation(self):
        class SlowTranslator:
            def translate(_, text, source):
                self.stop.set()
                return "Поздний ответ"
        self.assertEqual(self.run_session(manual="Hello team", translator=SlowTranslator()), [Result("done")])

    def test_result_queue_keeps_latest_without_blocking(self):
        queue = Queue(maxsize=2)
        for text in ("one", "two", "three"):
            publish(queue, Result("message", text=text))
        self.assertEqual(queue.qsize(), 2)
        self.assertEqual(queue.get_nowait().text, "two")
        self.assertEqual(queue.get_nowait().text, "three")


if __name__ == "__main__":
    unittest.main()

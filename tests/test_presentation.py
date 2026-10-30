import unittest
from unittest.mock import patch

from screen_translator.core import ChatProcessor, RegionProcessor
from screen_translator.presentation import MessageHistory, clamp_font_size
from screen_translator.providers import Region
from screen_translator.windowing import exclude_from_capture, overlaps


class PresentationTests(unittest.TestCase):
    def test_history_is_bounded_and_keeps_unicode(self):
        history = MessageHistory(2)
        for text in ("old", "Игрок: Привет", "玩家: 你好"):
            history.append(text, text)
        self.assertNotIn("old", history.render())
        self.assertIn("Игрок: Привет", history.render())
        self.assertIn("玩家: 你好", history.render(False))
        self.assertNotIn("→", history.render(False))
        history.clear()
        self.assertEqual(history.render(), "")

    def test_individual_history_entries_are_bounded(self):
        history = MessageHistory(1)
        history.append("a" * 10000, "b" * 10000)
        self.assertEqual(len(history.messages[0][0]), 4096)
        self.assertEqual(len(history.messages[0][1]), 4096)

    def test_font_size_is_clamped_and_invalid_input_is_safe(self):
        for value, expected in (("24", 24), ("0", 10), ("100", 36), ("", 18), ("abc", 18), ("inf", 18)):
            self.assertEqual(clamp_font_size(value), expected)

    def test_window_intersections_include_negative_monitor_coordinates(self):
        region = Region(-200, 0, 100, 100)
        self.assertTrue(overlaps(region, (-150, 20, 20, 20)))
        self.assertFalse(overlaps(region, (-100, 0, 20, 20)))
        self.assertFalse(overlaps(region, (0, 0, 100, 100)))

    def test_capture_exclusion_honestly_falls_back_on_other_platforms(self):
        with patch("screen_translator.windowing.sys.platform", "linux"):
            self.assertFalse(exclude_from_capture(123))

    def test_ordinary_region_preserves_numbers_and_multiline_blocks(self):
        processor = RegionProcessor(confirmations=2)
        self.assertEqual(processor.process("123\n你好", visible=True), [])
        self.assertEqual(processor.process("123\n你好", visible=True), ["123\n你好"])
        self.assertEqual(processor.process("123\n你好", visible=True), [])
        processor.process("", visible=True)
        processor.process("123\n你好", visible=True)
        self.assertEqual(processor.process("123\n你好", visible=True), ["123\n你好"])

    def test_pending_chat_dedup_is_committed_only_after_successful_delivery(self):
        processor = ChatProcessor(interval=0, confirmations=1)
        self.assertEqual(processor.process("Hello", visible=True, mark_seen=False), ["Hello"])
        self.assertEqual(processor.process("Hello", visible=True, mark_seen=False), ["Hello"])
        processor.remember("Hello")
        self.assertEqual(processor.process("Hello", visible=True, mark_seen=False), [])

    def test_pending_region_is_retried_until_successful_delivery(self):
        processor = RegionProcessor(confirmations=1)
        self.assertEqual(processor.process("123", visible=True, mark_seen=False), ["123"])
        self.assertEqual(processor.process("123", visible=True, mark_seen=False), ["123"])
        processor.remember("123")
        self.assertEqual(processor.process("123", visible=True, mark_seen=False), [])


if __name__ == "__main__":
    unittest.main()

import unittest

from screen_translator.core import ChatProcessor, is_chat_line, normalize_line


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class NormalizationTests(unittest.TestCase):
    def test_preserves_scripts_and_names(self):
        for text in ("Player: Hello team!", "Игрок: Привет, команда!", "玩家: 你好", "好"):
            with self.subTest(text=text):
                self.assertEqual(normalize_line(text), text)
                self.assertTrue(is_chat_line(text))

    def test_normalizes_spaces_unicode_width_and_invisible_format(self):
        self.assertEqual(normalize_line("  Ｈｅｌｌｏ\t\u200b team\u00a0! "), "Hello team !")

    def test_does_not_transliterate_similar_letters(self):
        self.assertNotEqual(normalize_line("Player"), normalize_line("Рlayer"))

    def test_noise_and_oversize_are_rejected(self):
        for text in ("", " ", "||| ... _", "123456", "x", "aaaaaaaaaaa", "a" * 501):
            with self.subTest(text=text[:30]):
                self.assertFalse(is_chat_line(text))


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.processor = ChatProcessor(clock=self.clock, interval=0, cooldown=5)

    def frame(self, text, visible=True):
        self.clock.now += 1
        return self.processor.process(text, visible=visible)

    def test_requires_two_consecutive_frames(self):
        self.assertEqual(self.frame("Player: hello"), [])
        self.assertEqual(self.frame("Player: hello"), ["Player: hello"])

    def test_transient_noise_is_not_emitted(self):
        self.frame("Ghost: garbage")
        self.frame("")
        self.assertEqual(self.frame("Ghost: garbage"), [])

    def test_scrolling_chat_only_emits_new_lines(self):
        self.frame("Alice: hi\nBob: 你好")
        self.assertEqual(self.frame("Alice: hi\nBob: 你好"), ["Alice: hi", "Bob: 你好"])
        self.assertEqual(self.frame("Bob: 你好\nИгрок: Привет"), [])
        self.assertEqual(self.frame("Bob: 你好\nИгрок: Привет"), ["Игрок: Привет"])

    def test_confirmation_does_not_require_entire_frame_to_stop_changing(self):
        self.frame("Alice: hello")
        self.assertEqual(self.frame("Alice: hello\nBob: hi"), ["Alice: hello"])
        self.assertEqual(self.frame("Alice: hello\nBob: hi\nLee: 你好"), ["Bob: hi"])

    def test_visible_messages_do_not_repeat_when_cooldown_passes(self):
        self.frame("Hello team")
        self.frame("Hello team")
        for _ in range(20):
            self.assertEqual(self.frame("HELLO   team"), [])

    def test_duplicate_lines_in_same_frame_only_emit_once(self):
        self.frame("Hello\nHello")
        self.assertEqual(self.frame("Hello\nHello"), ["Hello"])

    def test_same_text_can_return_after_absence_and_cooldown(self):
        self.frame("Hello team")
        self.frame("Hello team")
        self.frame("")
        self.clock.now += 6
        self.frame("Hello team")
        self.assertEqual(self.frame("Hello team"), ["Hello team"])

    def test_hidden_chat_rejects_even_plausible_ocr_and_resets_confirmation(self):
        self.frame("Player: hello")
        self.assertEqual(self.frame("Player: hello", visible=False), [])
        self.assertEqual(self.frame("Player: hello"), [])
        self.assertEqual(self.frame("Player: hello"), ["Player: hello"])

    def test_hidden_chat_does_not_clear_recent_deduplication(self):
        self.frame("Hello team")
        self.frame("Hello team")
        self.frame("Hello team", visible=False)
        self.frame("Hello team")
        self.assertEqual(self.frame("Hello team"), [])

    def test_rate_limit_does_not_count_rapid_frames_as_confirmations(self):
        p = ChatProcessor(clock=self.clock, interval=1)
        self.assertEqual(p.process("Hello", visible=True), [])
        self.assertEqual(p.process("Hello", visible=True), [])
        self.clock.now += 1
        self.assertEqual(p.process("Hello", visible=True), ["Hello"])

    def test_buffers_are_bounded_and_newest_lines_are_preferred(self):
        p = ChatProcessor(clock=self.clock, interval=0, confirmations=1, capacity=4, max_lines=2)
        self.assertEqual(p.process("Old line\nNew line\nLast line", visible=True), ["New line", "Last line"])
        for i in range(20):
            p.process(f"Player: message {i}", visible=True)
        self.assertLessEqual(len(p._seen), 4)
        self.assertLessEqual(len(p._candidates), 2)

    def test_invalid_limits(self):
        for kwargs in ({"interval": -1}, {"cooldown": 0}, {"confirmations": 0},
                       {"capacity": 1}, {"interval": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ChatProcessor(**kwargs)


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from screen_translator.providers import (
    ArgosTranslator, OllamaTranslator, ProviderError, Region, TesseractOCR,
    _NoRedirect, make_translator,
)


class ProviderTests(unittest.TestCase):
    def test_default_is_offline_and_returns_original(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected network")):
            self.assertEqual(make_translator("Без перевода").translate("你好", "zh"), "你好")

    def test_unknown_provider_is_not_silently_used(self):
        with self.assertRaises(ProviderError):
            make_translator("Облако")

    def test_ollama_requires_explicit_consent_and_model(self):
        for model, consent in (("qwen", False), ("", True), ("a" * 201, True)):
            with self.subTest(model=model[:10], consent=consent), self.assertRaises(ProviderError):
                OllamaTranslator(model, consent=consent)

    def test_ollama_payload_and_response(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"message": {"content": "Привет, команда!"}}
        ).encode()
        with patch("urllib.request.build_opener", return_value=opener) as build:
            translated = OllamaTranslator("local-model", consent=True).translate("Hello team!", "en")
        self.assertEqual(translated, "Привет, команда!")
        req = opener.open.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "Hello team!"})
        self.assertFalse(payload["stream"])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], _NoRedirect)

    def test_ollama_rejects_redirects(self):
        with self.assertRaises(ProviderError):
            _NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_ollama_invalid_or_large_responses_are_reported(self):
        for body in (b"not json", b"{}", b'{"message":{"content":null}}',
                     b'{"message":{"content":""}}', b"x" * 65537):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = body
            with self.subTest(body=body[:40]), patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(ProviderError):
                    OllamaTranslator("local-model", consent=True).translate("hello", "en")

    def test_missing_argos_is_actionable(self):
        with patch.dict(sys.modules, {"argostranslate": None}), self.assertRaisesRegex(ProviderError, "Argos не установлен"):
            ArgosTranslator().translate("hello", "en")

    def test_argos_uses_only_installed_models(self):
        translation = SimpleNamespace(translate=lambda text: "Привет")
        source = SimpleNamespace(code="en", get_translation=lambda target: translation)
        target = SimpleNamespace(code="ru")
        module = SimpleNamespace(translate=SimpleNamespace(get_installed_languages=lambda: [source, target]))
        with patch.dict(sys.modules, {"argostranslate": module}):
            self.assertEqual(ArgosTranslator().translate("hello", "en"), "Привет")

    def test_argos_missing_model_is_actionable(self):
        module = SimpleNamespace(translate=SimpleNamespace(get_installed_languages=lambda: []))
        with patch.dict(sys.modules, {"argostranslate": module}), self.assertRaisesRegex(ProviderError, "Нет установленной модели"):
            ArgosTranslator().translate("hello", "en")

    def test_region_validates_size_and_allows_negative_monitor_coordinates(self):
        self.assertEqual(Region(-100, 0, 100, 100).left, -100)
        for values in ((0, 0, 0, 1), (0, 0, 9000, 1), (0, 0, 8192, 8192), (0, 0, 1.5, 2)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Region(*values)

    def test_missing_ocr_dependencies_are_actionable(self):
        with patch.dict(sys.modules, {"mss": None}), self.assertRaisesRegex(ProviderError, "requirements-ocr"):
            TesseractOCR().read(Region(0, 0, 10, 10))

    def test_ocr_captures_only_selected_region_with_timeout(self):
        capture = MagicMock()
        frame = SimpleNamespace(size=(20, 10), rgb=b"pixels")
        capture.return_value.__enter__.return_value.grab.return_value = frame
        image = MagicMock()
        pytesseract = SimpleNamespace(pytesseract=SimpleNamespace(), image_to_string=MagicMock(return_value="Hello"))
        modules = {"mss": SimpleNamespace(mss=capture), "PIL": SimpleNamespace(Image=image),
                   "pytesseract": pytesseract}
        with patch.dict(sys.modules, modules), patch.object(Path, "is_file", return_value=True):
            result = TesseractOCR("eng", "tesseract.exe").read(Region(-20, 5, 20, 10))
        self.assertEqual(result, "Hello")
        capture.return_value.__enter__.return_value.grab.assert_called_once_with(
            {"left": -20, "top": 5, "width": 20, "height": 10})
        self.assertEqual(pytesseract.image_to_string.call_args.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()

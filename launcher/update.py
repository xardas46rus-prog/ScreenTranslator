"""Строгий подписанный контракт обновлений. Никаких сетевых действий при импорте."""

import base64
from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__

REPOSITORY = "xardas46rus-prog/ScreenTranslator"
API = f"https://api.github.com/repos/{REPOSITORY}"
PREFIX = b"ScreenTranslator update manifest v1\n"
MAX_ZIP = 1024 * 1024 * 1024
MAX_UNPACKED = 4 * MAX_ZIP


class UpdateError(RuntimeError):
    """Ошибка, безопасная для показа пользователю."""


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})", value):
        raise UpdateError("Версия должна иметь формат X.Y.Z")
    return tuple(map(int, value.split(".")))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("Повторяющееся поле JSON")
        result[key] = value
    return result


def parse_json(data, limit=65536):
    if len(data) > limit:
        raise UpdateError("Слишком большой JSON")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(UpdateError("Некорректное число JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UpdateError("Повреждённый JSON") from exc


@dataclass(frozen=True)
class Manifest:
    version: str
    profile: str
    url: str
    asset_id: int
    size: int
    sha256: str
    envelope: bytes


def verify_manifest(data, keys, profile):
    if not keys:
        raise UpdateError("Обновления отключены: доверенный публичный ключ ещё не задан владельцем")
    obj = parse_json(data)
    if not isinstance(obj, dict) or set(obj) != {"key_id", "manifest", "signature"}:
        raise UpdateError("Неверная оболочка подписанного манифеста")
    key_id, payload = obj["key_id"], obj["manifest"]
    if not isinstance(key_id, str) or key_id not in keys or not isinstance(payload, dict):
        raise UpdateError("Неизвестный ключ подписи")
    try:
        signature = base64.b64decode(obj["signature"], validate=True)
        if len(signature) != 64:
            raise ValueError("signature length")
        Ed25519PublicKey.from_public_bytes(keys[key_id]).verify(signature, PREFIX + canonical(payload))
    except (InvalidSignature, ValueError, TypeError, RecursionError) as exc:
        raise UpdateError("Подпись Ed25519 не подтверждена") from exc
    fields = {"schema", "version", "channel", "platform", "profile", "url", "asset_id",
              "size", "sha256", "min_launcher"}
    if set(payload) != fields or type(payload["schema"]) is not int or payload["schema"] != 1:
        raise UpdateError("Неподдерживаемая схема манифеста")
    if payload["channel"] != "stable" or payload["platform"] != "windows-x64":
        raise UpdateError("Неверный канал или платформа")
    if profile not in ("ocr", "argos") or payload["profile"] != profile:
        raise UpdateError("Профиль обновления не совпадает; Argos нельзя заменять OCR автоматически")
    version(payload["version"])
    if version(payload["min_launcher"]) > version(__version__):
        raise UpdateError("Сначала требуется обновить сам лаунчер вручную")
    if type(payload["asset_id"]) is not int or not 1 <= payload["asset_id"] < 2**63:
        raise UpdateError("Неверный asset ID")
    if type(payload["size"]) is not int or not 1 <= payload["size"] <= MAX_ZIP:
        raise UpdateError("Недопустимый размер ZIP")
    if not isinstance(payload["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", payload["sha256"]):
        raise UpdateError("Неверный SHA-256")
    expected = (f"https://github.com/{REPOSITORY}/releases/download/v{payload['version']}/"
                f"ScreenTranslator-{payload['version']}-{profile}-windows-x64.zip")
    if payload["url"] != expected:
        raise UpdateError("Разрешён только фиксированный asset официального GitHub Release")
    return Manifest(payload["version"], profile, expected, payload["asset_id"], payload["size"],
                    payload["sha256"], bytes(data))


def verify_archive(path, manifest, cancel=None):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if cancel is not None and cancel.is_set():
                raise UpdateError("Операция отменена")
            total += len(block)
            if total > manifest.size:
                raise UpdateError("Размер ZIP не совпадает с подписанным манифестом")
            digest.update(block)
    if total != manifest.size or digest.hexdigest() != manifest.sha256:
        raise UpdateError("Размер или SHA-256 ZIP не совпадает с подписанным манифестом")


def validate_redirect(url):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.port in (None, 443) and not parts.username
                 and not parts.password and not parts.fragment and parts.hostname == "release-assets.githubusercontent.com")
    except ValueError:
        valid = False
    if not valid:
        raise UpdateError("Запрещено перенаправление за пределы GitHub Release assets")

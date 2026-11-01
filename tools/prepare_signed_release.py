"""Локальные keygen/inspect/sign/verify. Не содержит GitHub API и не публикует Release."""

import argparse
import base64
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import re
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from launcher import __version__
from launcher.install import check_pe, zip_entries
from launcher.update import (PREFIX, REPOSITORY, MAX_ZIP, UpdateError, canonical, parse_json,
                             validate_payload, verify_archive, verify_manifest)


def _key_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise UpdateError("key_id: 1–64 латинских букв/цифр, _ или -")
    return value


def private_location(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT):
        raise UpdateError("Приватный ключ должен находиться вне checkout")
    for parent in path.parents:
        if (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file():
            raise UpdateError("Приватный ключ нельзя хранить внутри Git checkout")
    if not path.parent.is_dir():
        raise UpdateError("Сначала создайте защищённый каталог ключа вне checkout")
    return path


def write_new(path, data, private=False):
    # Не перезаписываем существующие файлы, в том числе symlinks.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    with os.fdopen(os.open(path, flags, 0o600 if private else 0o644), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def public_record(private, key_id):
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"algorithm": "Ed25519", "key_id": _key_id(key_id), "public_key_hex": raw.hex(),
            "fingerprint": "SHA256:" + hashlib.sha256(raw).hexdigest()}


def generate_key(private_path, public_path, key_id, password):
    private_path = private_location(private_path)
    public_path = Path(public_path).resolve()
    _key_id(key_id)
    if len(password) < 12:
        raise UpdateError("Для шифрования ключа нужен пароль не короче 12 байт")
    if private_path == public_path or private_path.exists() or public_path.exists():
        raise UpdateError("Файлы ключа уже существуют; они не перезаписаны")
    if not public_path.parent.is_dir():
        raise UpdateError("Каталог публичного ключа отсутствует")
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(password))
    record = public_record(private, key_id)
    write_new(private_path, pem, private=True)
    write_new(public_path, canonical(record))
    return record


def load_private(path, password):
    path = private_location(path)
    if not password or path.stat().st_size > 65536:
        raise UpdateError("Нужен пароль и зашифрованный PKCS8 Ed25519 PEM")
    pem = path.read_bytes()
    if not pem.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise UpdateError("Незашифрованный приватный ключ не принимается")
    try:
        private = serialization.load_pem_private_key(pem, password=password)
    except (ValueError, TypeError) as exc:
        raise UpdateError("Не удалось открыть зашифрованный ключ: проверьте пароль/формат") from exc
    if not isinstance(private, Ed25519PrivateKey):
        raise UpdateError("Требуется ключ Ed25519")
    return private


def inspect_archive(archive_path, release, profile, asset_id, min_launcher=__version__):
    archive_path = Path(archive_path)
    size = archive_path.stat().st_size
    if not 1 <= size <= MAX_ZIP:
        raise UpdateError("Недопустимый размер ZIP")
    with archive_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    payload = {"schema": 1, "version": release, "channel": "stable", "platform": "windows-x64",
               "profile": profile, "url": f"https://github.com/{REPOSITORY}/releases/download/v{release}/ScreenTranslator-{release}-{profile}-windows-x64.zip",
               "asset_id": asset_id, "size": size, "sha256": digest, "min_launcher": min_launcher}
    validate_payload(payload, profile)
    validate_zip(archive_path)
    return payload


def validate_zip(path):
    try:
        with zipfile.ZipFile(path) as archive:
            zip_entries(archive)
            info = archive.getinfo("ScreenTranslator/ScreenTranslator.exe")
            with archive.open(info) as stream:
                check_pe(stream, info.file_size)
            if archive.testzip() is not None:
                raise UpdateError("CRC ZIP не подтверждён")
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError, zlib.error) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Повреждённый или неподдерживаемый portable ZIP") from exc


def sign_release(payload, archive, private_path, key_id, password):
    if not isinstance(payload, dict):
        raise UpdateError("Нужен объект manifest")
    manifest = validate_payload(payload, payload.get("profile"))
    verify_archive(Path(archive), manifest)
    validate_zip(archive)
    private = load_private(private_path, password)
    record = public_record(private, key_id)
    envelope = canonical({"key_id": key_id, "manifest": payload,
                          "signature": base64.b64encode(private.sign(PREFIX + canonical(payload))).decode("ascii")})
    # Подпись и ZIP проверяются тем же кодом, который использует лаунчер.
    verified = verify_manifest(envelope, {key_id: bytes.fromhex(record["public_key_hex"])}, manifest.profile)
    verify_archive(Path(archive), verified)
    return envelope, record


def verify_release(envelope, archive, public, fingerprint):
    if not isinstance(fingerprint, str) or not re.fullmatch(r"SHA256:[0-9a-f]{64}", fingerprint):
        raise UpdateError("Нужен проверенный SHA256-отпечаток публичного ключа")
    if (not isinstance(public, dict) or set(public) != {"algorithm", "key_id", "public_key_hex", "fingerprint"}
            or public["algorithm"] != "Ed25519"):
        raise UpdateError("Неверный файл публичного ключа")
    _key_id(public["key_id"])
    try:
        raw = bytes.fromhex(public["public_key_hex"])
    except (TypeError, ValueError) as exc:
        raise UpdateError("Неверный публичный ключ") from exc
    actual = "SHA256:" + hashlib.sha256(raw).hexdigest()
    if len(raw) != 32 or not hmac.compare_digest(actual, fingerprint) or public["fingerprint"] != actual:
        raise UpdateError("Отпечаток публичного ключа не совпадает с проверенным владельцем")
    payload = parse_json(envelope)
    if not isinstance(payload, dict) or not isinstance(payload.get("manifest"), dict):
        raise UpdateError("Неверный манифест")
    manifest = verify_manifest(envelope, {public["key_id"]: raw}, payload["manifest"].get("profile"))
    verify_archive(Path(archive), manifest)
    validate_zip(archive)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Локальная подготовка подписанного выпуска; без сети и публикации")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("keygen", help="На ПК владельца: создать зашифрованный ключ вне checkout")
    gen.add_argument("--private-key", type=Path, required=True)
    gen.add_argument("--public-out", type=Path, required=True)
    gen.add_argument("--key-id", required=True)
    inspect = sub.add_parser("inspect", help="Проверить неизменённый ZIP, сформировать manifest")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--version", required=True)
    inspect.add_argument("--profile", choices=("ocr", "argos"), required=True)
    inspect.add_argument("--asset-id", type=int, required=True)
    inspect.add_argument("--min-launcher", default=__version__)
    inspect.add_argument("--out", type=Path, required=True)
    sign = sub.add_parser("sign", help="Подписать локальный manifest зашифрованным ключом")
    sign.add_argument("--manifest", type=Path, required=True)
    sign.add_argument("--archive", type=Path, required=True)
    sign.add_argument("--private-key", type=Path, required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--out", type=Path, required=True)
    verify = sub.add_parser("verify", help="Проверить подпись, ZIP и отдельно сверенный fingerprint")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--fingerprint", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("keygen", "sign"):
            if os.environ.get("GITHUB_ACTIONS") == "true":
                raise UpdateError("Production keygen/sign выполняются только локально на ПК владельца, не в GitHub Actions")
            if not sys.stdin.isatty():
                raise UpdateError("Нужен интерактивный терминал: пароль нельзя передавать через pipeline/аргументы/CI")
        if args.command == "keygen":
            password = getpass.getpass("Новый пароль зашифрованного PEM (не передавайте его в чат): ")
            if password != getpass.getpass("Повторите пароль: "):
                raise UpdateError("Пароли не совпадают")
            record = generate_key(args.private_key, args.public_out, args.key_id, password.encode("utf-8"))
            print("Создан зашифрованный ключ. Публичный отпечаток:", record["fingerprint"])
        elif args.command == "inspect":
            payload = inspect_archive(args.archive, args.version, args.profile, args.asset_id, args.min_launcher)
            write_new(args.out, canonical(payload))
            print("ZIP проверен, manifest подготовлен. Загрузка/публикация не выполнялась.")
        elif args.command == "sign":
            password = getpass.getpass("Пароль зашифрованного PEM: ").encode("utf-8")
            envelope, record = sign_release(parse_json(args.manifest.read_bytes()), args.archive,
                                             args.private_key, args.key_id, password)
            write_new(args.out, envelope)
            print("Подпись и SHA-256 проверены локально. Публичный отпечаток:", record["fingerprint"])
        else:
            manifest = verify_release(args.manifest.read_bytes(), args.archive,
                                      parse_json(args.public_key.read_bytes()), args.fingerprint)
            print(f"Подпись Ed25519, SHA-256 и ZIP подтверждены: {manifest.version}, {manifest.profile}")
        return 0
    except (UpdateError, OSError, ValueError, EOFError) as exc:
        # Никаких traceback с локальными переменными/материалом ключа.
        print(f"Не выполнено: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Операция отменена владельцем", file=sys.stderr)
        return 130


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())

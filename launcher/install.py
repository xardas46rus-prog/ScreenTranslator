"""Неизменяемые каталоги версий + атомарный указатель, без удаления чужих файлов."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import uuid
import zipfile

from .update import MAX_UNPACKED, UpdateError, canonical, parse_json, verify_archive, verify_manifest, version


def plain(path):
    path = Path(path).absolute()
    # Windows TEMP часто содержит легальный 8.3 alias RUNNER~1. Отличие resolve()
    # от исходной строки само по себе не означает symlink/reparse point.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise UpdateError(f"Ссылки или junction в пути недопустимы: {part}")
    return path.resolve()


def atomic_json(path, value):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("xb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def zip_entries(archive):
    entries, seen, total = [], set(), 0
    if len(archive.infolist()) > 30000:
        raise UpdateError("Слишком много файлов ZIP")
    for info in archive.infolist():
        name = info.filename
        if ("\\" in name or "\x00" in name or ":" in name or name.startswith("/")
                or not name or info.flag_bits & 1):
            raise UpdateError("Опасный или зашифрованный путь ZIP")
        parts = name.rstrip("/").split("/")
        if parts[0] != "ScreenTranslator" or any(
            p in ("", ".", "..") or p.endswith((".", " ")) or
            re.match(r"(?i)^(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\.|$)", p) or
            any(ord(c) < 32 or c in '<>"|?*' for c in p) for p in parts):
            raise UpdateError("ZIP должен содержать только безопасное дерево ScreenTranslator/")
        key = "/".join(parts).casefold()
        mode = info.external_attr >> 16
        if key in seen or info.external_attr & 0x400 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise UpdateError("Дубликат, ссылка или специальный файл ZIP")
        seen.add(key)
        if info.file_size < 0 or info.compress_size < 0:
            raise UpdateError("Повреждённый размер ZIP")
        total += info.file_size
        if total > MAX_UNPACKED or (info.file_size > 16 * 1024 * 1024 and
                                   info.file_size > max(1, info.compress_size) * 500):
            raise UpdateError("Превышен распакованный объём ZIP")
        # Корень portable: только ожидаемые EXE, README/лицензия и _internal/.
        if len(parts) > 1 and parts[1] not in (
                "ScreenTranslator.exe", "ScreenTranslator-console.exe", "_internal", "LICENSE.txt", "ПРОЧИТАЙТЕ.md"):
            raise UpdateError("Неожиданное содержимое portable ZIP")
        if len(parts) > 2 and parts[1] != "_internal":
            raise UpdateError("Неверная структура portable ZIP")
        entries.append((info, parts))
    if "screentranslator/screentranslator.exe" not in seen:
        raise UpdateError("В ZIP отсутствует ScreenTranslator.exe")
    return entries, total


def extract_verified(zip_path, target, cancel):
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries, total = zip_entries(archive)
            if shutil.disk_usage(target.parent).free < total + 64 * 1024 * 1024:
                raise UpdateError("Недостаточно свободного места для распаковки")
            for info, parts in entries:
                if cancel.is_set():
                    raise UpdateError("Операция отменена")
                destination = target.joinpath(*parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                count = 0
                with archive.open(info) as source, destination.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        if cancel.is_set():
                            raise UpdateError("Операция отменена")
                        count += len(block)
                        if count > info.file_size:
                            raise UpdateError("Распакованный размер не совпадает")
                        output.write(block)
                if count != info.file_size:
                    raise UpdateError("Неполная распаковка ZIP")
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, ValueError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("ZIP повреждён или распаковка невозможна; рабочая версия сохранена") from exc


def check_executable(path):
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise UpdateError("ScreenTranslator.exe не является Windows x64 PE")
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset < 64 or offset > path.stat().st_size - 6:
            raise UpdateError("Повреждён PE-заголовок")
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise UpdateError("Требуется Windows x64 executable")


class Installation:
    def __init__(self, root, keys, profile="argos"):
        self.root = plain(root)
        self.keys, self.profile = keys, profile
        if profile not in ("ocr", "argos"):
            raise UpdateError("Неверный профиль")
        marker = self.root / "launcher-store.json"
        if self.root.exists() and any(self.root.iterdir()) and not marker.is_file():
            raise UpdateError("Выберите отдельный пустой каталог установки; существующие файлы не изменены")
        self.root.mkdir(parents=True, exist_ok=True)
        if marker.exists():
            if parse_json(plain(marker).read_bytes()) != {"schema": 1, "owner": "ScreenTranslatorLauncher"}:
                raise UpdateError("Неизвестный каталог хранилища")
        else:
            # Не заменяем даже конфликтующий marker, созданный другим процессом.
            with marker.open("xb") as stream:
                stream.write(canonical({"schema": 1, "owner": "ScreenTranslatorLauncher"}))
        for name in ("versions", "staging"):
            plain(self.root / name).mkdir(exist_ok=True)
        self.state_path = plain(self.root / "state.json")
        self.child = None

    @contextmanager
    def lock(self):
        path = plain(self.root / "operation.lock")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise UpdateError("Другая операция активна или остался lock после сбоя; см. инструкцию восстановления") from exc
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            path.unlink()

    def state(self):
        empty = {"schema": 1, "profile": self.profile, "current": None, "previous": None, "highwater": None}
        if not self.state_path.exists():
            return empty
        data = parse_json(plain(self.state_path).read_bytes())
        if not isinstance(data, dict) or set(data) != set(empty) or type(data["schema"]) is not int or data["schema"] != 1:
            raise UpdateError("Повреждён указатель установки; файлы версий сохранены")
        if data["profile"] != self.profile:
            raise UpdateError("В этом каталоге другой профиль; выберите отдельный каталог, модели сохранятся")
        if data["highwater"] is not None:
            version(data["highwater"])
        for entry in (data["current"], data["previous"]):
            if entry is not None:
                if (not isinstance(entry, dict) or set(entry) != {"id", "version"}
                        or not isinstance(entry["id"], str) or not re.fullmatch("[0-9a-f]{32}", entry["id"])):
                    raise UpdateError("Неверный путь установленной версии")
                version(entry["version"])
                if data["highwater"] is None or version(entry["version"]) > version(data["highwater"]):
                    raise UpdateError("Повреждён счётчик защиты от downgrade")
        return data

    def candidate(self, envelope):
        manifest = verify_manifest(envelope, self.keys, self.profile)
        state = self.state()
        if state["highwater"] is not None and version(manifest.version) <= version(state["highwater"]):
            raise UpdateError("Downgrade/replay запрещён; для возврата используйте явный «Откат»")
        return manifest

    def busy(self):
        return self.child is not None and self.child.poll() is None

    def install(self, envelope, archive, cancel):
        with self.lock():
            if self.busy():
                raise UpdateError("Сначала завершите запущенный переводчик")
            manifest = self.candidate(envelope)
            state = self.state()
            verify_archive(Path(archive), manifest, cancel)
            if shutil.disk_usage(self.root).free < manifest.size + 64 * 1024 * 1024:
                raise UpdateError("Недостаточно свободного места для staging")
            identity = uuid.uuid4().hex
            stage = plain(self.root / "staging" / identity)
            stage.mkdir()
            cached = stage / "archive.zip"
            shutil.copyfile(archive, cached)
            # Повторная проверка именно staging-копии защищает от изменения исходника.
            verify_archive(cached, manifest, cancel)
            extract_verified(cached, stage / "payload", cancel)
            (stage / "manifest.json").write_bytes(envelope)
            exe = stage / "payload" / "ScreenTranslator" / "ScreenTranslator.exe"
            check_executable(exe)
            if cancel.is_set():
                raise UpdateError("Операция отменена")
            final = plain(self.root / "versions" / identity)
            os.replace(stage, final)
            # До этого места старый state и его каталог не затрагиваются.
            atomic_json(self.state_path, {"schema": 1, "profile": self.profile,
                                         "current": {"id": identity, "version": manifest.version},
                                         "previous": state["current"], "highwater": manifest.version})
            return manifest.version

    def checked_exe(self, entry):
        if entry is None:
            raise UpdateError("Переводчик ещё не установлен")
        folder = plain(self.root / "versions" / entry["id"])
        manifest = verify_manifest(plain(folder / "manifest.json").read_bytes(), self.keys, self.profile)
        if manifest.version != entry["version"]:
            raise UpdateError("Подменены сведения установленной версии")
        verify_archive(plain(folder / "archive.zip"), manifest)
        # Запуск/откат сверяет и распакованные файлы с подписанным архивом.
        # Неизвестные файлы не удаляются; никакие их команды не запускаются.
        try:
            with zipfile.ZipFile(folder / "archive.zip") as archive:
                entries, _ = zip_entries(archive)
                expected = {"/".join(parts) for info, parts in entries if not info.is_dir()}
                payload = plain(folder / "payload")
                actual_files = set()
                for directory, subdirs, files in os.walk(payload, followlinks=False):
                    for name in subdirs + files:
                        plain(Path(directory) / name)
                    actual_files.update((Path(directory) / name).relative_to(payload).as_posix() for name in files)
                if actual_files != expected:
                    raise UpdateError("В приложении есть неизвестные или отсутствующие файлы; они сохранены, запуск запрещён")
                for info, parts in entries:
                    if info.is_dir():
                        continue
                    installed = plain((folder / "payload").joinpath(*parts))
                    if not installed.is_file() or installed.stat().st_size != info.file_size:
                        raise UpdateError("Повреждены файлы установленной версии")
                    with archive.open(info) as original, installed.open("rb") as actual:
                        if hashlib.file_digest(original, "sha256").digest() != hashlib.file_digest(actual, "sha256").digest():
                            raise UpdateError("Изменены файлы установленной версии; запуск запрещён")
        except (OSError, zipfile.BadZipFile) as exc:
            raise UpdateError("Невозможно проверить установленную версию") from exc
        exe = plain(folder / "payload" / "ScreenTranslator" / "ScreenTranslator.exe")
        if not exe.is_file():
            raise UpdateError("Исполняемый файл отсутствует")
        check_executable(exe)
        return exe

    def rollback(self):
        with self.lock():
            if self.busy():
                raise UpdateError("Сначала завершите запущенный переводчик")
            state = self.state()
            self.checked_exe(state["previous"])
            state["current"], state["previous"] = state["previous"], state["current"]
            # highwater не уменьшается: откат не разрешает повтор старого сетевого манифеста.
            atomic_json(self.state_path, state)

    def launch(self, legacy=None):
        with self.lock():
            if self.busy():
                raise UpdateError("Переводчик уже запущен этим лаунчером")
            if legacy is not None:
                exe = plain(Path(legacy) / "ScreenTranslator" / "ScreenTranslator.exe")
                if not exe.is_file():
                    raise UpdateError("Нет ScreenTranslator/ScreenTranslator.exe в выбранном каталоге")
            else:
                exe = self.checked_exe(self.state()["current"])
            self.child = subprocess.Popen([str(exe)], cwd=exe.parent, shell=False)

    def stop(self):
        if self.busy():
            self.child.terminate()
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise UpdateError("Переводчик ещё завершает работу; дождитесь закрытия окна") from exc

"""Ограниченный GitHub transport; никаких URL из пользовательских настроек."""

import time
from urllib import request, error

from .update import API, REPOSITORY, UpdateError, parse_json, validate_redirect, verify_manifest, version


class _Redirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not req.full_url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
            raise UpdateError("Перенаправление API/CDN запрещено")
        validate_redirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, opener=None):
        self.opener = opener or request.build_opener(_Redirect())

    def read(self, url, limit, cancel, destination=None, exact=False):
        if not (url == API + "/releases/latest" or
                url.startswith(f"https://github.com/{REPOSITORY}/releases/download/")):
            raise UpdateError("Неофициальный адрес обновления")
        total, blocks = 0, []
        deadline = time.monotonic() + 120
        try:
            if cancel.is_set():
                raise UpdateError("Операция отменена")
            req = request.Request(url, headers={"User-Agent": "ScreenTranslatorLauncher/0.1.0",
                                               "Accept": "application/vnd.github+json" if url.startswith(API) else "application/octet-stream"})
            with self.opener.open(req, timeout=10) as response:
                while True:
                    if cancel.is_set():
                        raise UpdateError("Операция отменена")
                    if time.monotonic() > deadline:
                        raise UpdateError("Превышено общее время загрузки")
                    block = response.read(min(65536, limit + 1 - total))
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise UpdateError("Превышен допустимый размер загрузки")
                    if destination is None:
                        blocks.append(block)
                    else:
                        destination.write(block)
            if exact and total != limit:
                raise UpdateError("Неполная загрузка ZIP")
            return b"".join(blocks)
        except error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError("Подписанный Release пока отсутствует (HTTP 404)") from exc
            if exc.code == 403:
                raise UpdateError("Доступ к GitHub запрещён (HTTP 403): проверьте proxy/сеть и лимит GitHub API") from exc
            raise UpdateError(f"GitHub вернул HTTP {exc.code}; обновление не установлено") from exc
        except (error.URLError, OSError, TimeoutError) as exc:
            raise UpdateError("GitHub недоступен, загрузка прервана или истёк таймаут") from exc


class Updates:
    def __init__(self, keys, transport=None):
        self.keys = keys
        self.transport = transport or GitHub()
        self.last_check = None

    def check(self, profile, cancel):
        if not self.keys:
            raise UpdateError("Обновления отключены: владелец ещё не задал доверенный публичный ключ")
        if profile not in ("ocr", "argos"):
            raise UpdateError("Неверный профиль")
        now = time.monotonic()
        if self.last_check is not None and now - self.last_check < 30:
            raise UpdateError("Повторная проверка возможна через 30 секунд")
        self.last_check = now
        release = parse_json(self.transport.read(API + "/releases/latest", 1024 * 1024, cancel), 1024 * 1024)
        if (not isinstance(release, dict) or release.get("draft") is not False
                or release.get("prerelease") is not False or not isinstance(release.get("assets"), list)):
            raise UpdateError("Неверные метаданные GitHub Release")
        matches = [a for a in release["assets"] if isinstance(a, dict) and a.get("name") == f"update-{profile}.json"]
        tag = release.get("tag_name")
        if len(matches) != 1 or not isinstance(tag, str) or not tag.startswith("v"):
            raise UpdateError("В Release нет единственного подписанного манифеста выбранного профиля")
        version(tag[1:])
        url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/update-{profile}.json"
        if matches[0].get("browser_download_url") != url:
            raise UpdateError("Подменён адрес манифеста")
        manifest = verify_manifest(self.transport.read(url, 65536, cancel), self.keys, profile)
        assets = [a for a in release["assets"] if isinstance(a, dict) and type(a.get("id")) is int and a["id"] == manifest.asset_id]
        if (tag != "v" + manifest.version or len(assets) != 1
                or assets[0].get("browser_download_url") != manifest.url
                or type(assets[0].get("size")) is not int or assets[0]["size"] != manifest.size):
            raise UpdateError("Asset ID, размер, версия или URL Release не совпадает с подписью")
        return manifest

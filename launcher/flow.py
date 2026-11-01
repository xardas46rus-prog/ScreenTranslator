"""Единый поток check → предложение → явная установка; без интерфейса Tk."""

from dataclasses import dataclass
import shutil
import uuid

from .update import Manifest, UpdateError, version


@dataclass(frozen=True)
class Offer:
    installed: str | None
    manifest: Manifest
    current: bool
    rolled_back: bool


@dataclass(frozen=True)
class Installed:
    version: str
    started: bool
    launch_error: str | None = None


class UpdateFlow:
    def __init__(self, updates):
        self.updates = updates

    def check(self, manager, cancel):
        manifest = self.updates.check(manager.profile, cancel)
        state = manager.state()
        installed = state["current"]["version"] if state["current"] else None
        highwater = state["highwater"]
        if highwater is not None and version(manifest.version) < version(highwater):
            raise UpdateError("Release старее уже проверенной версии; downgrade/replay запрещён")
        current = highwater == manifest.version
        if not current:
            manager.candidate(manifest.envelope)
        return Offer(installed, manifest, current, current and installed != highwater)

    def install(self, manager, manifest, cancel, *, launch_first=False):
        # Повторная проверка актуального state и подписи, даже если GUI сохранил offer.
        manifest = manager.candidate(manifest.envelope)
        if manager.busy():
            raise UpdateError("Сначала завершите запущенный переводчик")
        if cancel.is_set():
            raise UpdateError("Операция отменена")
        if shutil.disk_usage(manager.root).free < manifest.size * 2 + 64 * 1024 * 1024:
            raise UpdateError("Недостаточно места для загрузки и staging")
        folder = manager.root / "staging" / uuid.uuid4().hex
        folder.mkdir()
        archive = folder / "download.zip"
        with archive.open("xb") as stream:
            self.updates.transport.read(manifest.url, manifest.size, cancel, stream, exact=True)
        result = manager.install(manifest.envelope, archive, cancel)
        committed = manager.state()
        first = committed["previous"] is None and committed["current"]["version"] == result
        if first and launch_first:
            try:
                manager.launch(expected_version=result, cancel=cancel)
            except (UpdateError, OSError) as exc:
                return Installed(result, False, str(exc))
            return Installed(result, True)
        return Installed(result, False)

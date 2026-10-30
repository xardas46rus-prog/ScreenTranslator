"""Синхронная ручная проверка и ограниченный конвейер живого OCR/перевода.

Рабочие потоки не используют Tk. Видимость, режим и движок меняются атомарно;
смена поколения отменяет отложенные задания и результаты старого состояния.
"""

from dataclasses import dataclass, replace
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, current_thread
import math
import time

from .core import ChatProcessor, RegionProcessor
from .providers import OCR, Region, Translator


@dataclass(frozen=True)
class Result:
    kind: str
    original: str = ""
    text: str = ""
    epoch: int = 0


def publish(queue: Queue, value: Result):
    """Ограниченная очередь: при отставании GUI вытесняется самый старый результат."""
    while True:
        try:
            queue.put_nowait(value)
            return
        except Full:
            try:
                queue.get_nowait()
            except Empty:
                pass


def run_session(*, queue: Queue, stop: Event, visible: Event, translator: Translator,
                source: str, ocr: OCR | None = None, region: Region | None = None,
                manual: str | None = None, interval=0.75):
    processor = ChatProcessor(interval=0, confirmations=1 if manual is not None else 2)
    try:
        while not stop.is_set():
            if not visible.is_set():
                processor.process("", visible=False)
                if manual is not None or stop.wait(interval):
                    return
                continue
            if manual is None:
                if ocr is None or region is None:
                    raise ValueError("Не заданы OCR и область захвата")
                text = ocr.read(region)
            else:
                text = manual
            # Повторная проверка: чат мог скрыться во время медленного OCR.
            if stop.is_set():
                return
            lines = processor.process(text, visible=visible.is_set())
            for line in lines:
                if stop.is_set() or not visible.is_set():
                    break
                translated = translator.translate(line, source)
                if not stop.is_set() and visible.is_set():
                    publish(queue, Result("message", line, translated))
            if manual is not None or stop.wait(interval):
                return
    except Exception as exc:
        if not stop.is_set():
            publish(queue, Result("error", text=str(exc)))
    finally:
        publish(queue, Result("done"))


@dataclass(frozen=True)
class LiveOptions:
    interval: float = 0.25
    confirmations: int = 2
    cooldown: float = 30.0
    max_pending: int = 16
    stale_after: float = 10.0

    def __post_init__(self):
        if (not math.isfinite(self.interval) or not 0.05 <= self.interval <= 10
                or type(self.confirmations) is not int or not 1 <= self.confirmations <= 5
                or not math.isfinite(self.cooldown) or not 1 <= self.cooldown <= 300
                or type(self.max_pending) is not int or not 1 <= self.max_pending <= 64
                or not math.isfinite(self.stale_after) or not 0 < self.stale_after <= 300):
            raise ValueError("Интервал OCR: 0,05–10 с; подтверждения: 1–5; окно повторов: 1–300 с")


@dataclass(frozen=True)
class _State:
    epoch: int
    visible: bool
    fast_chat: bool
    blocked: bool
    translator: Translator

    @property
    def active(self):
        return not self.blocked and (self.visible or not self.fast_chat)


@dataclass(frozen=True)
class _Task:
    text: str
    state: _State
    created: float


class LiveSession:
    """По одному потоку OCR и перевода; максимум max_pending ожидающих заданий.

    Медленный перевод не задерживает OCR. При переполнении вытесняется старое
    задание, при скрытии/смене движка — всё предыдущее поколение. Потоки не
    убиваются принудительно: повторный запуск доступен после их завершения.
    """

    def __init__(self, *, queue: Queue, ocr: OCR, region: Region, translator: Translator,
                 source: str, options: LiveOptions | None = None, fast_chat=True,
                 visible=True, clock=time.monotonic):
        self.queue = queue
        self.ocr, self.region, self.source = ocr, region, source
        self.options = options or LiveOptions()
        self.clock = clock
        self.stop = Event()
        self._capture_done = Event()
        self._lock = Lock()
        self._state = _State(0, visible, fast_chat, False, translator)
        self._tasks = Queue(maxsize=self.options.max_pending)
        self._acks = Queue(maxsize=256)
        self._pending = set()
        self._threads = []
        self._frames = self._dropped = self._translated = 0
        self._started = False

    def snapshot(self):
        with self._lock:
            return self._state

    def _change(self, **changes):
        with self._lock:
            if all(getattr(self._state, name) == value for name, value in changes.items()):
                return
            self._state = replace(self._state, epoch=self._state.epoch + 1, **changes)
        self._drain()

    def set_visible(self, value: bool):
        self._change(visible=bool(value))

    def set_fast_chat(self, value: bool):
        self._change(fast_chat=bool(value))

    def set_blocked(self, value: bool):
        self._change(blocked=bool(value))

    def set_translator(self, translator: Translator):
        self._change(translator=translator)

    def accepts(self, result: Result):
        state = self.snapshot()
        return (result.epoch == state.epoch and (result.kind == "error" or not self.stop.is_set())
                and (result.kind != "message" or state.active))

    @property
    def stats(self):
        with self._lock:
            return {"frames": self._frames, "dropped": self._dropped,
                    "translated": self._translated, "pending": self._tasks.qsize()}

    def _drop(self):
        with self._lock:
            self._dropped += 1

    def _release(self, task):
        with self._lock:
            self._pending.discard((task.state.epoch, task.text.casefold()))

    def _drain(self):
        while True:
            try:
                task = self._tasks.get_nowait()
                self._release(task)
                self._drop()
            except Empty:
                return

    def start(self):
        if self._started:
            raise RuntimeError("Сессия уже запускалась; создайте новую после остановки")
        self._started = True
        self._threads = [Thread(target=self._capture, daemon=True, name="screen-ocr"),
                         Thread(target=self._translate, daemon=True, name="screen-translation")]
        for thread in self._threads:
            thread.start()

    def is_alive(self):
        return any(thread.is_alive() for thread in self._threads)

    def join(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            if thread is not current_thread():
                thread.join(None if deadline is None else max(0, deadline - time.monotonic()))

    def cancel(self):
        self.stop.set()
        self._drain()

    def _valid(self, task):
        state = self.snapshot()
        return (not self.stop.is_set() and state.active and task.state.epoch == state.epoch
                and self.clock() - task.created < self.options.stale_after)

    def _enqueue(self, task):
        key = (task.state.epoch, task.text.casefold())
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        while not self.stop.is_set():
            try:
                self._tasks.put_nowait(task)
                return
            except Full:
                try:
                    old = self._tasks.get_nowait()
                    self._release(old)
                    self._drop()
                except Empty:
                    pass
        self._release(task)

    def _capture(self):
        chat = ChatProcessor(interval=0, confirmations=self.options.confirmations,
                             cooldown=self.options.cooldown, clock=self.clock)
        region = RegionProcessor(confirmations=self.options.confirmations)
        epoch = -1
        translator = None
        try:
            while not self.stop.is_set():
                started = self.clock()
                state = self.snapshot()
                if state.epoch != epoch:
                    chat.reset_confirmation()
                    region = RegionProcessor(confirmations=self.options.confirmations)
                    # При смене движка текст нужно выдать новому провайдеру снова.
                    if epoch >= 0 and state.translator is not translator:
                        chat = ChatProcessor(interval=0, confirmations=self.options.confirmations,
                                             cooldown=self.options.cooldown, clock=self.clock)
                        region = RegionProcessor(confirmations=self.options.confirmations)
                    epoch, translator = state.epoch, state.translator
                while True:
                    try:
                        ack = self._acks.get_nowait()
                    except Empty:
                        break
                    if ack.state.translator is state.translator:
                        if ack.state.fast_chat:
                            chat.remember(ack.text)
                        elif ack.state.epoch == state.epoch:
                            region.remember(ack.text)
                    self._release(ack)
                if state.active:
                    text = self.ocr.read(self.region)
                    with self._lock:
                        self._frames += 1
                    if state.epoch == self.snapshot().epoch and not self.stop.is_set():
                        lines = (chat.process(text, visible=True, mark_seen=False) if state.fast_chat
                                 else region.process(text, visible=True, mark_seen=False))
                        for line in lines:
                            self._enqueue(_Task(line, state, self.clock()))
                self.stop.wait(max(0, self.options.interval - (self.clock() - started)))
        except Exception as exc:
            if not self.stop.is_set():
                publish(self.queue, Result("error", text=f"OCR остановлен: {exc}", epoch=self.snapshot().epoch))
                self.cancel()
        finally:
            self._capture_done.set()

    def _translate(self):
        try:
            while not self.stop.is_set():
                try:
                    task = self._tasks.get(timeout=0.05)
                except Empty:
                    if self._capture_done.is_set():
                        return
                    continue
                if not self._valid(task):
                    self._release(task)
                    self._drop()
                    continue
                try:
                    translated = task.state.translator.translate(task.text, self.source)
                except Exception as exc:
                    if self._valid(task):
                        publish(self.queue, Result("error", text=f"Перевод остановлен: {exc}", epoch=task.state.epoch))
                        self.cancel()
                    else:
                        self._drop()
                    self._release(task)
                    continue
                if self._valid(task):
                    publish(self.queue, Result("message", task.text, translated, task.state.epoch))
                    publish(self._acks, task)
                    with self._lock:
                        self._translated += 1
                else:
                    self._release(task)
                    self._drop()
        finally:
            self._drain()

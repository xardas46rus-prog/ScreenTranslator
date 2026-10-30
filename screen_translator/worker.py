"""Один последовательный рабочий поток; Tk вызывается только из GUI."""

from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event

from .core import ChatProcessor
from .providers import OCR, Region, Translator


@dataclass(frozen=True)
class Result:
    kind: str
    original: str = ""
    text: str = ""


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

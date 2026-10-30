"""Обработка OCR без GUI, моделей и сетевых запросов."""

from collections import OrderedDict
import math
import time
import unicodedata


def normalize_line(text: str) -> str:
    """Сохранить алфавит и регистр, убрать артефакты пробелов и формата."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(text.split())


def is_chat_line(text: str) -> bool:
    """Консервативный фильтр; не пытается исправлять/транслитерировать OCR."""
    if not text or len(text) > 500:
        return False
    letters = sum(c.isalpha() for c in text)
    visible = sum(not c.isspace() for c in text)
    if not letters or letters / visible < 0.35:
        return False
    # Одиночные китайские ответы допустимы; одиночный латинский шум — нет.
    if letters < 2 and not any("\u3400" <= c <= "\u9fff" for c in text):
        return False
    compact = text.replace(" ", "")
    return not (len(compact) > 6 and len(set(compact.casefold())) == 1)


class ChatProcessor:
    """Ограниченный поток + подтверждение строк + дедупликация с TTL.

    Видимость задаёт пользователь/адаптер, а не догадка по распознанному тексту.
    При скрытии подтверждения сбрасываются. Уже выданные строки сохраняются
    в ограниченном LRU, пока видны, и ещё cooldown секунд после исчезновения.
    """

    def __init__(self, *, interval=0.2, confirmations=2, cooldown=30.0,
                 capacity=256, max_lines=40, clock=time.monotonic):
        if (not math.isfinite(interval) or interval < 0
                or not math.isfinite(cooldown) or cooldown <= 0
                or confirmations < 1 or max_lines < 1 or capacity < max_lines):
            raise ValueError("Некорректные ограничения обработчика чата")
        self.interval = interval
        self.confirmations = confirmations
        self.cooldown = cooldown
        self.capacity = capacity
        self.max_lines = max_lines
        self.clock = clock
        self._last_frame = float("-inf")
        self._candidates = {}
        self._seen = OrderedDict()

    def process(self, text: str, *, visible: bool) -> list[str]:
        if not visible:
            self._candidates.clear()
            return []
        now = self.clock()
        if now - self._last_frame < self.interval:
            return []
        self._last_frame = now
        for key, seen_at in list(self._seen.items()):
            if now - seen_at >= self.cooldown:
                del self._seen[key]
        lines = text[:32768].splitlines()[-self.max_lines:]
        candidates = {}
        result = []
        for raw in lines:
            line = normalize_line(raw)
            if not is_chat_line(line):
                continue
            key = line.casefold()
            if key in candidates:
                continue
            count = min(self._candidates.get(key, 0) + 1, self.confirmations)
            candidates[key] = count
            if key in self._seen:
                self._seen[key] = now
                self._seen.move_to_end(key)
            elif count >= self.confirmations:
                result.append(line)
                self._seen[key] = now
                while len(self._seen) > self.capacity:
                    self._seen.popitem(last=False)
        self._candidates = candidates
        return result

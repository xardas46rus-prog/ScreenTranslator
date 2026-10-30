"""Ограниченная история и настройки вывода без зависимости от Tk."""

from collections import deque


def clamp_font_size(value) -> int:
    try:
        return max(10, min(36, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return 18


class MessageHistory:
    def __init__(self, capacity=100):
        if not 1 <= capacity <= 500:
            raise ValueError("История должна содержать от 1 до 500 сообщений")
        self.messages = deque(maxlen=capacity)

    def append(self, original: str, translated: str):
        self.messages.append((original[:4096], translated[:4096]))

    def render(self, show_original=True):
        return "\n\n".join(f"{original}\n→ {translated}" if show_original else translated
                            for original, translated in self.messages)

    def clear(self):
        self.messages.clear()

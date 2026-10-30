"""Небольшие платформенные операции; импорт не требует дисплея или Windows."""

import sys


def overlaps(region, bounds):
    left, top, width, height = bounds
    return (region.left < left + width and left < region.left + region.width
            and region.top < top + height and top < region.top + region.height)


def exclude_from_capture(window_id: int) -> bool:
    """Windows 10 2004+: исключение собственного окна из поддерживаемого capture.

    False означает обязательный fallback: размещение вне выбранной области.
    Это функция ОС, не гарантия для каждого драйвера/метода захвата.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        handle = user32.GetAncestor(window_id, 2) or window_id
        return bool(user32.SetWindowDisplayAffinity(handle, 0x11))
    except (AttributeError, OSError):
        return False

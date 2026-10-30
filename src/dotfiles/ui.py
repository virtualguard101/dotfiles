"""终端输出：颜色、分节标题、中英文混排对齐与大小格式化。"""
from __future__ import annotations

import os
import sys
import unicodedata


def _enable_windows_vt() -> bool:
    """在 Windows 控制台中开启 ANSI 转义序列；失败时返回 False（改为无颜色输出）。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")   # 控制台编码不支持的字符转义输出，不崩溃
    except (AttributeError, ValueError):
        pass

COLOR = not os.environ.get("NO_COLOR") and sys.stdout.isatty() and (os.name != "nt" or _enable_windows_vt())


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def section(text: str) -> None:
    print(f"\n{_paint('1;34', f'== {text} ==')}")


def info(text: str = "") -> None:
    print(text)


def dim(text: str) -> None:
    print(_paint("2", text))


def ok(text: str) -> None:
    print(f"{_paint('32', '✓')} {text}")


def warn(text: str) -> None:
    sys.stdout.flush()
    print(f"{_paint('33', '!')} {text}", file=sys.stderr)


def err(text: str) -> None:
    sys.stdout.flush()
    print(f"{_paint('31', '✗')} {text}", file=sys.stderr)


_MARKS = {"ok": ("32", "✓"), "warn": ("33", "!"), "fail": ("31", "✗"), "info": ("2", "·"),
          "todo": ("33", "○"), "skip": ("2", "-")}


def status_line(level: str, text: str, indent: int = 2) -> None:
    """带颜色状态标记的一行，写到 stdout（报告类输出保持顺序）。"""
    code, mark = _MARKS.get(level, ("0", " "))
    print(f"{' ' * indent}{_paint(code, mark)} {text}")


def note(text: str) -> None:
    """强调提示（黄色），写到 stdout。"""
    print(_paint("33", text))


class Abort(Exception):
    """可预期的失败：打印消息后以指定退出码结束。"""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def width(text: str) -> int:
    """终端显示宽度：中日韩全角字符占两列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, cols: int) -> str:
    return text + " " * max(0, cols - width(text))


def human(num_bytes: int) -> str:
    if num_bytes < 0:
        return "-"
    if num_bytes == 0:
        return "0"
    value = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            if unit == "B":
                return f"{num_bytes}B"
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
        value /= 1024
    return f"{num_bytes}B"


def confirm(question: str, assume_yes: bool) -> bool:
    """交互确认；--yes 时直接通过，非交互终端时视为拒绝。"""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        warn(f"非交互终端，跳过需要确认的操作：{question}")
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")

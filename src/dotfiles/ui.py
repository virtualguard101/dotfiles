"""Terminal output: colors, section titles, CJK-aware alignment and size formatting."""
from __future__ import annotations

import os
import sys
import unicodedata


def _enable_windows_vt() -> bool:
    """Enable ANSI escape sequences in a Windows console; returns False on failure (no colors)."""
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
        _s.reconfigure(errors="backslashreplace")   # escape characters the console codec can't represent, don't crash
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
    """One line with a colored status mark, written to stdout (keeps report output in order)."""
    code, mark = _MARKS.get(level, ("0", " "))
    print(f"{' ' * indent}{_paint(code, mark)} {text}")


def note(text: str) -> None:
    """Emphasized hint (yellow), written to stdout."""
    print(_paint("33", text))


class Abort(Exception):
    """An expected failure: print the message and exit with the given code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def width(text: str) -> int:
    """Display width in terminal columns: CJK fullwidth characters count as two."""
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
    """Interactive confirmation; passes through with --yes, treated as declined on a non-tty."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        warn(f"not a terminal, skipping a step that needs confirmation: {question}")
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")

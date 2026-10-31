"""Filesystem helpers: size accounting that never follows links, and deletion behind a scope assertion."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterable

from dotfiles.utils import ui

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def is_link(entry: os.DirEntry | Path) -> bool:
    """A symlink or Windows reparse point (junction etc.). Such entries are only sized/deleted as the link itself."""
    try:
        st = entry.stat(follow_symlinks=False) if isinstance(entry, os.DirEntry) else os.lstat(entry)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def size_of(paths: Iterable[Path | str]) -> int:
    """Total bytes of several paths. Never follows links; unreadable dirs are ignored."""
    total = 0
    for p in paths:
        p = Path(p)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or is_link(p):
            total += st.st_size if stat.S_ISREG(st.st_mode) else 0
            continue
        stack = [p]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if is_link(e):
                                continue
                            if e.is_dir(follow_symlinks=False):
                                stack.append(Path(e.path))
                            elif e.is_file(follow_symlinks=False):
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
    return total


def tilde(path: Path | str) -> str:
    text = str(path)
    home = str(Path.home())
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/")


class Deleter:
    """Every deletion goes through here: the path must be absolute, lie under an allowed root, and
    must not be a protected directory itself. With dry=True it only prints. Files locked by other
    processes are counted in `locked` without aborting."""

    def __init__(self, dry: bool, safe_roots: Iterable[Path], protected: Iterable[Path]):
        self.dry = dry
        self.safe_roots = [_norm(p) for p in safe_roots if p]
        self.protected = {_norm(p) for p in protected if p}
        self.locked = 0

    def assert_deletable(self, path: Path) -> None:
        if not path or not Path(path).is_absolute():
            raise ui.Abort(f"refusing to delete a non-absolute path: '{path}'", 2)
        full = _norm(Path(path))
        if full in self.protected or len(full) <= 3 or full == _norm(Path(full).anchor):
            raise ui.Abort(f"refusing to delete a protected directory: {full}", 2)
        if not any(full.startswith(root + os.sep) for root in self.safe_roots):
            raise ui.Abort(f"refusing to delete outside the allowed roots: {full}", 2)

    def remove(self, paths: Iterable[Path | str]) -> None:
        for p in paths:
            p = Path(p)
            if not os.path.lexists(p):
                continue
            self.assert_deletable(p)
            if self.dry:
                ui.dim(f"  + delete {tilde(p)}")
                continue
            self._remove_tree(p)

    def _remove_tree(self, p: Path) -> None:
        try:
            st = os.lstat(p)
        except OSError:
            return
        if stat.S_ISDIR(st.st_mode) and not is_link(p):
            try:
                with os.scandir(p) as it:
                    children = [Path(e.path) for e in it]
            except OSError:
                children = []
            for child in children:
                self._remove_tree(child)
            try:
                os.rmdir(p)
            except OSError:
                pass   # the dir still holds locked files
            return
        for attempt in range(2):
            try:
                if stat.S_ISDIR(st.st_mode):
                    os.rmdir(p)      # a Windows directory junction: deletes the link itself
                else:
                    os.unlink(p)
                return
            except PermissionError:
                if attempt == 0:
                    try:
                        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)   # Windows read-only attribute
                        continue
                    except OSError:
                        pass
                self.locked += 1
                return
            except OSError:
                self.locked += 1
                return

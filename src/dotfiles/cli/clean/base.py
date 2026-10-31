"""Cleanup engine types shared by the per-platform target tables.

Each target provides:
  paths(ctx)  paths that would be deleted/pruned this run (used for size reporting); Skip("reason")
              marks something skipped because an app is running etc.
  clean(ctx)  perform the cleanup (only prints in dry-run); defaults to deleting the paths() output
  avail(ctx)  optional: return False when the target does not apply (tool not installed)
  size(ctx)   optional: override the size statistic (bytes; -1 when it cannot be measured)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from dotfiles.utils import fsutil, platforms, ui


class Skip(str):
    """A skip note inside paths()."""


@dataclass
class Ctx:
    deep: bool = False
    dry: bool = True
    deleter: fsutil.Deleter | None = None
    failed: bool = False

    def cmd(self, *argv: str) -> bool:
        """Run a cleanup command; only prints in dry-run. Failures are recorded, not raised."""
        if self.dry:
            ui.dim(f"  + {' '.join(argv)}")
            return True
        if not platforms.have(argv[0]):
            ui.warn(f"{argv[0]} not found (continuing)")
            self.failed = True
            return False
        rc = platforms.run_live(list(argv))
        if rc != 0:
            ui.warn(f"command failed (continuing): {' '.join(argv)}")
            self.failed = True
        return rc == 0

    def remove(self, paths: Iterable[Path | str]) -> None:
        assert self.deleter is not None
        self.deleter.remove(paths)


Paths = Iterable["Path | Skip"]


@dataclass
class Target:
    id: str
    tier: str            # default | opt
    desc: str
    paths: Callable[[Ctx], Paths]
    clean: Callable[[Ctx], None] | None = None
    avail: Callable[[Ctx], bool] | None = None
    size: Callable[[Ctx], int] | None = None
    extra: dict = field(default_factory=dict)

    def available(self, ctx: Ctx) -> bool:
        if self.avail is None:
            return True
        try:
            return bool(self.avail(ctx))
        except Exception:
            return False

    def run_clean(self, ctx: Ctx) -> None:
        if self.clean is not None:
            self.clean(ctx)
            return
        clean_from_paths(ctx, self.paths(ctx))


def clean_from_paths(ctx: Ctx, items: Paths) -> None:
    for p in items:
        if isinstance(p, Skip):
            ui.warn(f"{p}, skipping")
        elif p:
            ctx.remove([p])


def split_paths(items: Paths) -> tuple[list[Path], list[str]]:
    paths, skips = [], []
    for p in items:
        if isinstance(p, Skip):
            skips.append(str(p))
        elif p:
            paths.append(Path(p))
    return paths, skips


def measure(t: Target, ctx: Ctx) -> tuple[int, list[str]]:
    try:
        paths, skips = split_paths(t.paths(ctx))
    except Exception as e:   # a failed measurement must not affect other targets
        return -1, [f"measurement failed: {e}"]
    size = t.size(ctx) if t.size else fsutil.size_of(paths)
    return size, skips


def existing(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p and p.exists()]


def sub_dirs(path: Path, pattern: str = "*") -> list[Path]:
    try:
        return sorted(p for p in path.glob(pattern) if p.is_dir() and not fsutil.is_link(p))
    except OSError:
        return []


def path_target(spec: dict, home: Path) -> Target:
    """A path-only target declared in dotfiles.toml [[clean.paths]]: deletes the listed paths.
    The deleter still enforces its scope (allowed roots, protected dirs) on every path."""
    raw = spec["paths"]
    rels = [raw] if isinstance(raw, str) else list(raw)

    def paths(_ctx: Ctx) -> list[Path]:
        out = []
        for r in rels:
            p = home / r[2:] if r.startswith("~/") else Path(r)
            if p.is_absolute() and p.exists():
                out.append(p)
        return out

    return Target(spec["id"], spec["tier"], spec["desc"], paths=paths)

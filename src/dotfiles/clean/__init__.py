"""Cache cleanup engine: table-driven targets, preview by default. Per-platform target tables
live in linux.py / windows.py.

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

from dotfiles import fsutil, sysinfo, ui


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
        if not sysinfo.have(argv[0]):
            ui.warn(f"{argv[0]} not found (continuing)")
            self.failed = True
            return False
        rc = sysinfo.run_live(list(argv))
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


# ---------------------------------------------------------------- platform module

def platform():
    if sysinfo.IS_WINDOWS:
        from dotfiles.clean import windows as mod
    elif sysinfo.IS_LINUX:
        from dotfiles.clean import linux as mod
    else:
        raise ui.Abort(f"unsupported platform: {sysinfo.OS_NAME}", 2)
    return mod


def make_ctx(mod, *, dry: bool, deep: bool) -> Ctx:
    return Ctx(deep=deep, dry=dry, deleter=fsutil.Deleter(dry, mod.safe_roots(), mod.protected()))


# ---------------------------------------------------------------- subcommands

def cmd_list() -> int:
    mod = platform()
    ui.info(f"{ui.pad('target', 12)}{ui.pad('tier', 9)}description")
    for t in mod.targets():
        ui.info(f"{ui.pad(t.id, 12)}{ui.pad(t.tier, 9)}{t.desc}")
    return 0


def cmd_scan(deep: bool) -> int:
    mod = platform()
    ctx = make_ctx(mod, dry=True, deep=deep)
    ui.section("Disk overview")
    mod.overview()

    ui.section("Cleanup targets (run executes the default tier by default; sizes with --deep: scan --deep)")
    ui.info(f"{ui.pad('target', 12)}{ui.pad('tier', 9)}{ui.pad('size', 10)}{ui.pad('state', 10)}description")
    total = 0
    for t in mod.targets():
        if not t.available(ctx):
            ui.dim(f"{ui.pad(t.id, 12)}{ui.pad(t.tier, 9)}{ui.pad('-', 10)}{ui.pad('n/a', 10)}{t.desc}")
            continue
        size, skips = measure(t, ctx)
        state = ("skips" if skips else
                 "unmeasured" if size < 0 else
                 "no data" if size == 0 else "cleanable")
        if t.tier == "default" and size > 0:
            total += size
        ui.info(f"{ui.pad(t.id, 12)}{ui.pad(t.tier, 9)}{ui.pad(ui.human(size), 10)}{ui.pad(state, 10)}{t.desc}")
        if skips:
            ui.note(f"{' ' * 31}↳ {'; '.join(skips)}")
    ui.info()
    ui.info(f"default tier totals about {ui.human(total)} (uv/pnpm/pre-commit etc. prune; actual freed space may be less)")
    mod.scan_extra(ctx)
    scan_uncovered(mod)
    cmd_leftovers()
    ui.info()
    ui.info("next: clean run to preview the default tier; clean run --apply to execute; clean run opt for high-cost and system targets")
    return 0


def scan_uncovered(mod) -> None:
    """Large subdirectories of the cache roots covered by no target: hints that a target may be missing."""
    min_mb = mod.UNCOVERED_MIN_MB
    roots = mod.uncovered_roots()
    ui.section(f"Entries >= {min_mb}M in {' and '.join(fsutil.tilde(r) for r in roots)} covered by no target")
    ctx = make_ctx(mod, dry=True, deep=True)   # judge coverage by the --deep path set
    covered = {n.lower() for n in mod.UNCOVERED_ALWAYS}
    for t in mod.targets():
        if not t.available(ctx):
            continue
        try:
            paths, _ = split_paths(t.paths(ctx))
        except Exception:
            continue
        for p in paths:
            for r in roots:
                try:
                    rel = p.relative_to(r)
                except ValueError:
                    continue
                if rel.parts:
                    covered.add(rel.parts[0].lower())
    rows = []
    for r in roots:
        for d in sub_dirs(r):
            if d.name.lower() in covered:
                continue
            size = fsutil.size_of([d])
            if size >= min_mb * 1024 * 1024:
                rows.append((size, d))
    for size, d in sorted(rows, reverse=True):
        ui.info(f"  {ui.pad(ui.human(size), 10)}{fsutil.tilde(d)}")
    if not rows:
        ui.ok("none")


def cmd_leftovers() -> int:
    platform().leftovers()
    return 0


def cmd_run(selection: list[str], exclude: list[str], apply: bool, deep: bool) -> int:
    mod = platform()
    targets = {t.id: t for t in mod.targets()}
    order = list(targets)
    ids: list[str] = []
    for s in selection or ["default"]:
        if s == "all":
            ids += order
        elif s in ("default", "opt"):
            ids += [i for i in order if targets[i].tier == s]
        elif s in targets:
            ids.append(s)
        else:
            raise ui.Abort(f"unknown target: {s} (see clean list)", 2)
    excl = {x for e in exclude for x in e.split(",") if x}
    for s in excl:
        if s not in targets:
            raise ui.Abort(f"unknown target: {s}", 2)

    ctx = make_ctx(mod, dry=not apply, deep=deep)
    if ctx.dry:
        ui.note("dry-run: the commands below will not run; add --apply to clean for real")
    if deep:
        ui.note("--deep: a more thorough cleanup mode for the targets that support it")
    freed = 0
    done: set[str] = set()
    for i in ids:
        if i in done or i in excl:
            continue
        done.add(i)
        t = targets[i]
        ui.section(i)
        if not t.available(ctx):
            ui.info("  not applicable (not installed or no data)")
            continue
        before = measure(t, ctx)[0] if not ctx.dry else 0
        ctx.deleter.locked = 0
        try:
            t.run_clean(ctx)
        except ui.Abort:
            raise
        except Exception as e:
            ui.warn(f"{i} failed (continuing): {e}")
            ctx.failed = True
        if not ctx.dry:
            if ctx.deleter.locked:
                ui.info(f"  {ctx.deleter.locked} files were locked and skipped")
            after = measure(t, ctx)[0]
            if before > after >= 0:
                freed += before - after
    ui.info()
    if ctx.dry:
        ui.info("this was a preview. Add --apply to run.")
    else:
        ui.ok(f"done, estimated {ui.human(freed)} freed")
    if ctx.failed:
        ui.err("some commands failed; see the warnings above")
        return 1
    return 0

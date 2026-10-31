"""dotfiles clean: table-driven cache cleanup, preview by default. Engine types live in
base.py, per-platform target tables in linux.py / windows.py, shared toolchain targets in shared.py."""
from __future__ import annotations

from dotfiles.cli.clean.base import (
    Ctx,
    Target,
    measure,
    path_target,
    split_paths,
    sub_dirs,
)
from dotfiles.utils import fsutil, platforms, ui
from dotfiles.utils.common import config as dotconf

__all__ = ["Ctx", "Target", "cmd_leftovers", "cmd_list", "cmd_run", "cmd_scan"]


# ---------------------------------------------------------------- platform module

def platform():
    if platforms.IS_WINDOWS:
        from dotfiles.cli.clean import windows as mod
    elif platforms.IS_LINUX:
        from dotfiles.cli.clean import linux as mod
    else:
        raise ui.Abort(f"unsupported platform: {platforms.OS_NAME}", 2)
    return mod


def targets(mod) -> list[Target]:
    """The platform's built-in targets adjusted by dotfiles.toml [clean]: `disabled` ids are dropped,
    `opt` ids move to the opt tier, and [[clean.paths]] adds path-only targets."""
    builtin = mod.targets()
    ids = {t.id for t in builtin}
    disabled, opt = set(dotconf.get("clean.disabled")), set(dotconf.get("clean.opt"))
    out = []
    for t in builtin:
        if t.id in disabled:
            continue
        if t.id in opt:
            t.tier = "opt"
        out.append(t)
    for spec in dotconf.get("clean.paths"):
        if spec.get("os") not in (None, platforms.OS_NAME):
            continue
        if spec["id"] in ids:
            raise ui.Abort(f"dotfiles.toml [[clean.paths]] id {spec['id']!r} clashes with a built-in target", 2)
        if spec["tier"] not in ("default", "opt"):
            raise ui.Abort(f"dotfiles.toml [[clean.paths]] {spec['id']}: tier must be default or opt", 2)
        ids.add(spec["id"])
        if spec["id"] not in disabled:
            out.append(path_target(spec, platforms.home()))
    unknown = (disabled | opt) - ids
    if unknown:
        ui.warn(f"dotfiles.toml [clean] names unknown targets: {', '.join(sorted(unknown))}")
    return out


def make_ctx(mod, *, dry: bool, deep: bool) -> Ctx:
    return Ctx(deep=deep, dry=dry, deleter=fsutil.Deleter(dry, mod.safe_roots(), mod.protected()))


# ---------------------------------------------------------------- subcommands

def cmd_list() -> int:
    mod = platform()
    ui.info(f"{ui.pad('target', 12)}{ui.pad('tier', 9)}description")
    for t in targets(mod):
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
    for t in targets(mod):
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
    min_mb = dotconf.get(f"clean.{platforms.OS_NAME}.uncovered_min_mb")
    roots = mod.uncovered_roots()
    ui.section(f"Entries >= {min_mb}M in {' and '.join(fsutil.tilde(r) for r in roots)} covered by no target")
    ctx = make_ctx(mod, dry=True, deep=True)   # judge coverage by the --deep path set
    covered = {n.lower() for n in dotconf.get(f"clean.{platforms.OS_NAME}.uncovered_always")}
    for t in targets(mod):
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
    by_id = {t.id: t for t in targets(mod)}
    order = list(by_id)
    ids: list[str] = []
    for s in selection or ["default"]:
        if s == "all":
            ids += order
        elif s in ("default", "opt"):
            ids += [i for i in order if by_id[i].tier == s]
        elif s in by_id:
            ids.append(s)
        else:
            raise ui.Abort(f"unknown target: {s} (see clean list)", 2)
    excl = {x for e in exclude for x in e.split(",") if x}
    for s in excl:
        if s not in by_id:
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
        t = by_id[i]
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

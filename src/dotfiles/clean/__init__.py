"""缓存清理引擎：目标表驱动，默认只预览。各平台的目标表见 linux.py / windows.py。

每个目标提供：
  paths(ctx)  本次会删除/修剪的路径（用于统计占用）；Skip("原因") 表示因应用运行等原因跳过
  clean(ctx)  执行清理（dry-run 时只打印）；缺省时删除 paths 给出的路径
  avail(ctx)  可选：返回 False 表示不适用（工具未安装）
  size(ctx)   可选：覆盖默认的占用统计（字节；-1 表示无法统计）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from dotfiles import fsutil, sysinfo, ui


class Skip(str):
    """paths() 中的跳过说明。"""


@dataclass
class Ctx:
    deep: bool = False
    dry: bool = True
    deleter: fsutil.Deleter | None = None
    failed: bool = False

    def cmd(self, *argv: str) -> bool:
        """运行清理命令；dry-run 时只打印。失败只记录，不中断其他目标。"""
        if self.dry:
            ui.dim(f"  + {' '.join(argv)}")
            return True
        if not sysinfo.have(argv[0]):
            ui.warn(f"未找到 {argv[0]}（继续）")
            self.failed = True
            return False
        rc = sysinfo.run_live(list(argv))
        if rc != 0:
            ui.warn(f"命令失败（继续）: {' '.join(argv)}")
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
            ui.warn(f"{p}，跳过")
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
    except Exception as e:   # 统计失败不影响其他目标
        return -1, [f"统计失败: {e}"]
    size = t.size(ctx) if t.size else fsutil.size_of(paths)
    return size, skips


def existing(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p and p.exists()]


def sub_dirs(path: Path, pattern: str = "*") -> list[Path]:
    try:
        return sorted(p for p in path.glob(pattern) if p.is_dir() and not fsutil.is_link(p))
    except OSError:
        return []


# ---------------------------------------------------------------- 平台模块

def platform():
    if sysinfo.IS_WINDOWS:
        from dotfiles.clean import windows as mod
    elif sysinfo.IS_LINUX:
        from dotfiles.clean import linux as mod
    else:
        raise ui.Abort(f"不支持的平台: {sysinfo.OS_NAME}", 2)
    return mod


def make_ctx(mod, *, dry: bool, deep: bool) -> Ctx:
    return Ctx(deep=deep, dry=dry, deleter=fsutil.Deleter(dry, mod.safe_roots(), mod.protected()))


# ---------------------------------------------------------------- 子命令

def cmd_list() -> int:
    mod = platform()
    ui.info(f"{ui.pad('目标', 12)}{ui.pad('层级', 9)}说明")
    for t in mod.targets():
        ui.info(f"{ui.pad(t.id, 12)}{ui.pad(t.tier, 9)}{t.desc}")
    return 0


def cmd_scan(deep: bool) -> int:
    mod = platform()
    ctx = make_ctx(mod, dry=True, deep=deep)
    ui.section("空间概览")
    mod.overview()

    ui.section("清理目标（run 默认只执行 default 层；--deep 时的数字见 scan --deep）")
    ui.info(f"{ui.pad('目标', 12)}{ui.pad('层级', 9)}{ui.pad('占用', 10)}{ui.pad('状态', 10)}说明")
    total = 0
    for t in mod.targets():
        if not t.available(ctx):
            ui.dim(f"{ui.pad(t.id, 12)}{ui.pad(t.tier, 9)}{ui.pad('-', 10)}{ui.pad('不适用', 10)}{t.desc}")
            continue
        size, skips = measure(t, ctx)
        state = "有跳过" if skips else ("未统计" if size < 0 else ("无数据" if size == 0 else "可清理"))
        if t.tier == "default" and size > 0:
            total += size
        ui.info(f"{ui.pad(t.id, 12)}{ui.pad(t.tier, 9)}{ui.pad(ui.human(size), 10)}{ui.pad(state, 10)}{t.desc}")
        if skips:
            ui.note(f"{' ' * 31}↳ {'; '.join(skips)}")
    ui.info()
    ui.info(f"default 层合计占用约 {ui.human(total)}（uv/pnpm/pre-commit 等为修剪，实际释放可能更少）")
    mod.scan_extra(ctx)
    scan_uncovered(mod)
    cmd_leftovers()
    ui.info()
    ui.info("下一步: clean run 预览 default 层；clean run --apply 执行；clean run opt 预览高代价与系统级目标")
    return 0


def scan_uncovered(mod) -> None:
    """缓存根目录中体积较大、却没有被任何目标覆盖的子目录：提示可能需要新增目标。"""
    min_mb = mod.UNCOVERED_MIN_MB
    roots = mod.uncovered_roots()
    ui.section(f"{' 与 '.join(fsutil.tilde(r) for r in roots)} 中未被任何目标覆盖的项（≥ {min_mb}M）")
    ctx = make_ctx(mod, dry=True, deep=True)   # 以 --deep 的路径集合判断覆盖范围
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
        ui.ok("无")


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
            raise ui.Abort(f"未知目标: {s}（用 clean list 查看）", 2)
    excl = {x for e in exclude for x in e.split(",") if x}
    for s in excl:
        if s not in targets:
            raise ui.Abort(f"未知目标: {s}", 2)

    ctx = make_ctx(mod, dry=not apply, deep=deep)
    if ctx.dry:
        ui.note("dry-run：以下命令不会执行，加 --apply 实际清理")
    if deep:
        ui.note("--deep：对支持的目标使用更彻底的清理方式")
    freed = 0
    done: set[str] = set()
    for i in ids:
        if i in done or i in excl:
            continue
        done.add(i)
        t = targets[i]
        ui.section(i)
        if not t.available(ctx):
            ui.info("  不适用（未安装或无数据）")
            continue
        before = measure(t, ctx)[0] if not ctx.dry else 0
        ctx.deleter.locked = 0
        try:
            t.run_clean(ctx)
        except ui.Abort:
            raise
        except Exception as e:
            ui.warn(f"{i} 失败（继续）: {e}")
            ctx.failed = True
        if not ctx.dry:
            if ctx.deleter.locked:
                ui.info(f"  {ctx.deleter.locked} 个文件被占用，已跳过")
            after = measure(t, ctx)[0]
            if before > after >= 0:
                freed += before - after
    ui.info()
    if ctx.dry:
        ui.info("以上为预览。加 --apply 执行。")
    else:
        ui.ok(f"完成，估算释放 {ui.human(freed)}")
    if ctx.failed:
        ui.err("部分命令失败，见上方警告")
        return 1
    return 0

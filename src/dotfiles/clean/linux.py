"""Linux（Arch）清理目标：以 ~/.cache 为主，覆盖 archpkg 中会产生缓存的工具与应用。"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from dotfiles import chez, fsutil, sysinfo, ui
from dotfiles.clean import Ctx, Skip, Target, common, existing, sub_dirs
from dotfiles.sysinfo import have, home, xdg_cache, xdg_config, xdg_data


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value.isdigit() else default


THUMBNAIL_DAYS = _int_env("THUMBNAIL_DAYS", 90)
STALE_DAYS = _int_env("STALE_DAYS", 180)
JOURNAL_MAX = os.environ.get("JOURNAL_MAX", "1G")
PACCACHE_KEEP = _int_env("PACCACHE_KEEP", 1)
UNCOVERED_MIN_MB = _int_env("UNCOVERED_MIN_MB", 10)
UNCOVERED_ALWAYS = ("yay", "paru", "mozilla", "typescript", "jedi", "thumbnails")

ELECTRON_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
                       "GPUPersistentCache", "GrShaderCache", "ShaderCache")
VSCODE_FAMILY = ("Code", "Code - OSS", "VSCodium", "Cursor", "Windsurf")
VSCODE_CACHE_DIRS = ("CachedData", "CachedExtensionVSIXs", "CachedProfilesData", "logs")

# 已卸载应用的残留：检测名（pacman 包名或命令，任一存在即视为已安装）→ 路径（相对 ~）
LEFTOVER_MAP = (
    (("cursor-bin", "cursor"), (".config/Cursor",)),
    (("google-chrome", "google-chrome-stable"), (".config/google-chrome", ".cache/google-chrome")),
    (("chromium",), (".config/chromium", ".cache/chromium")),
    (("visual-studio-code-bin", "code"), (".config/Code", ".vscode")),
    (("discord",), (".config/discord",)),
    (("notion-app-electron",), (".config/Notion",)),
    (("obsidian",), (".config/obsidian",)),
    (("feishu-bin",), (".config/LarkShell", ".cache/LarkShell")),
    (("telegram-desktop",), (".local/share/TelegramDesktop",)),
    (("steam",), (".local/share/Steam",)),
    (("wine",), (".wine", ".cache/wine")),
)


# ---------------------------------------------------------------- 删除范围

def safe_roots() -> list[Path]:
    roots = [home()]
    for name in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "CARGO_HOME", "GRADLE_USER_HOME", "VCPKG_ROOT",
                 "PRE_COMMIT_HOME", "BUN_INSTALL", "NUGET_HTTP_CACHE_PATH"):
        if os.environ.get(name):
            roots.append(Path(os.environ[name]).parent)
    return roots


def protected() -> list[Path]:
    h = home()
    return [h, xdg_cache(), xdg_config(), xdg_data(), h / ".local", h / ".local" / "state",
            h / "Documents", h / "Desktop", h / "Downloads", h / ".ssh", h / ".gnupg", chez.REPO]


# ---------------------------------------------------------------- 运行检测

def lock_alive(path: Path) -> bool:
    """锁文件对应的进程是否存活：符号链接锁（Chromium SingletonLock → host-PID，Firefox lock → IP:+PID）
    或写有 PID 的普通文件（VS Code code.lock）。"""
    try:
        if path.is_symlink():
            m = re.search(r"[-+](\d+)$", os.readlink(path))
        elif path.is_file():
            m = re.search(r"(\d+)", path.read_bytes()[:32].decode(errors="ignore"))
        else:
            return False
    except OSError:
        return False
    return bool(m) and sysinfo.pid_alive(int(m.group(1)))


def _argv_match(rx: str) -> bool:
    pattern = re.compile(rx, re.I)
    return any(pattern.search(" ".join(p.argv[:2]) or p.exe) for p in sysinfo.processes())


def _cmdline_match(rx: str) -> bool:
    pattern = re.compile(rx)
    return any(pattern.search(" ".join(p.argv)) for p in sysinfo.processes())


def _pgrep(*names: str) -> bool:
    return sysinfo.proc_running(names)


# ---------------------------------------------------------------- 目标

def _yay() -> Target:
    def bases() -> set[str]:
        out: set[str] = set()
        for desc in Path("/var/lib/pacman/local").glob("*/desc"):
            try:
                lines = desc.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines[:-1]):
                if line in ("%BASE%", "%NAME%"):
                    out.add(lines[i + 1].strip())
        return out

    def dirs() -> list[Path]:
        c = xdg_cache()
        return sub_dirs(c / "yay") + sub_dirs(c / "paru" / "clone")

    def busy() -> bool:
        return _pgrep("yay", "paru", "makepkg")

    def untracked(d: Path) -> list[Path]:
        r = sysinfo.run(["git", "-C", str(d), "ls-files", "-o", "--directory"])
        return [d / line.rstrip("/") for line in r.stdout.splitlines() if line.strip()]

    def paths(c: Ctx):
        if busy():
            return [Skip("yay/paru/makepkg 正在运行")]
        installed = bases()
        out: list = []
        for d in dirs():
            out += untracked(d) if d.name in installed and (d / ".git").is_dir() else [d]
        return out

    def clean(c: Ctx):
        if busy():
            ui.warn("yay/paru/makepkg 正在运行，跳过")
            return
        installed = bases()
        for d in dirs():
            if d.name in installed and (d / ".git").is_dir():
                if untracked(d):
                    c.cmd("git", "-C", str(d), "clean", "-ffdxq")
            else:
                ui.info(f"  已卸载或非 git 克隆: {d.name}")
                c.remove([d])

    return Target("yay", "default", "AUR 构建目录（yay/paru）：已安装包的克隆 git clean -ffdx，已卸载包的克隆整体删除",
                  avail=lambda c: bool(dirs()), paths=paths, clean=clean)


def _firefox() -> Target:
    def paths(c: Ctx):
        out: list = []
        for p in sub_dirs(xdg_cache() / "mozilla" / "firefox"):
            locks = [home() / ".mozilla" / "firefox" / p.name / "lock", xdg_config() / "mozilla" / "firefox" / p.name / "lock"]
            if any(lock_alive(lock) for lock in locks):
                out.append(Skip(f"Firefox 配置 {p.name} 正在使用"))
                continue
            out += [p / "cache2", p / "startupCache", p / "thumbnails"]
        return out

    return Target("firefox", "default", "Firefox 网络缓存 cache2 / startupCache / thumbnails（配置文件被占用时跳过）",
                  avail=lambda c: (xdg_cache() / "mozilla" / "firefox").is_dir(), paths=paths)


def electron_app_dirs() -> list[Path]:
    out = []
    for d in sub_dirs(xdg_config()):
        if d.name in VSCODE_FAMILY:
            out.append(d)
            continue
        if any((d / s).is_dir() or any(d.glob(f"Partitions/*/{s}")) or (d / "Default" / s).is_dir()
               for s in ELECTRON_CACHE_DIRS):
            out.append(d)
    return out


def electron_busy(d: Path) -> bool:
    lock = d / "SingletonLock"
    if lock.is_symlink() or lock.exists():
        return lock_alive(lock)
    if (d / "code.lock").exists():
        return lock_alive(d / "code.lock")
    return _argv_match(rf"/{re.escape(d.name)}(/| |$)")


def _electron() -> Target:
    def paths(c: Ctx):
        out: list = []
        for d in electron_app_dirs():
            if electron_busy(d):
                out.append(Skip(f"{d.name} 正在运行"))
                continue
            bases = [d, d / "Default", *sub_dirs(d, "Profile*"), *sub_dirs(d / "Partitions")]
            for b in bases:
                out += [b / s for s in ELECTRON_CACHE_DIRS if (b / s).is_dir()]
            if d.name in VSCODE_FAMILY:
                out += [d / s for s in VSCODE_CACHE_DIRS if (d / s).is_dir()]
        return out

    return Target("electron", "default",
                  "~/.config 下 Electron/Chromium 应用的 Cache、Code Cache、GPUCache 等（应用运行时跳过）", paths=paths)


def _lsp() -> Target:
    dirs = [xdg_cache() / "typescript", xdg_cache() / "jedi"]

    def paths(c: Ctx):
        if _cmdline_match(r"tsserver|typingsInstaller|jedi-language-server|pylsp"):
            return [Skip("语言服务正在运行（关闭编辑器后再清理）")]
        return dirs

    return Target("lsp", "default", "编辑器语言服务缓存 ~/.cache/typescript ~/.cache/jedi（语言服务运行时跳过）",
                  avail=lambda c: bool(existing(dirs)), paths=paths)


def _wine() -> Target:
    dirs = [xdg_cache() / "wine", xdg_cache() / "winetricks"]
    return Target("wine", "default", "wine-mono/gecko 安装包与 winetricks 下载缓存",
                  avail=lambda c: bool(existing(dirs)), paths=lambda c: dirs)


def _kde() -> Target:
    """只碰可再生且非 mmap 的缓存；ksycoca / *.kcache 交给 Plasma 自己维护。"""
    c_ = xdg_cache()

    def paths(c: Ctx):
        out: list = [c_ / "drkonqi" / "crashes", c_ / "bookmarksrunner", c_ / "kcrash-metadata"]
        if _pgrep("plasma-discover"):
            out.append(Skip("Discover 正在运行，保留 AppStream 缓存"))
        else:
            out += [c_ / "appstream", c_ / "discover"]
        return out

    return Target("kde", "default", "DrKonqi 崩溃报告、KRunner 书签副本、Discover/AppStream 目录缓存",
                  avail=lambda c: bool(existing([c_ / "drkonqi", c_ / "bookmarksrunner", c_ / "appstream"])),
                  paths=paths)


def _thumbnails() -> Target:
    root = xdg_cache() / "thumbnails"

    def old_files() -> list[Path]:
        cutoff = time.time() - THUMBNAIL_DAYS * 86400
        out = []
        for dirpath, _, files in os.walk(root):
            for f in files:
                p = Path(dirpath, f)
                try:
                    if p.stat().st_atime < cutoff:
                        out.append(p)
                except OSError:
                    continue
        return out

    def paths(c: Ctx):
        return [root] if c.deep else old_files()

    def clean(c: Ctx):
        if c.deep:
            c.remove([root])
            return
        files = old_files()
        if not files:
            return
        if c.dry:
            ui.dim(f"  + 删除 ~/.cache/thumbnails 中 {THUMBNAIL_DAYS} 天未访问的 {len(files)} 个文件")
        else:
            c.remove(files)

    return Target("thumbnails", "default", f"超过 THUMBNAIL_DAYS({THUMBNAIL_DAYS}) 天未访问的缩略图（--deep: 全部）",
                  avail=lambda c: root.is_dir(), paths=paths, clean=clean)


def _zcompdump() -> Target:
    def stale() -> list[Path]:
        line = sysinfo.query(["zsh", "--version"]) or ""
        parts = line.split()
        if len(parts) < 2:
            return []
        cur = parts[1]
        return [p for p in sorted(home().glob(".zcompdump-*"))
                if not (p.name.endswith(f"-{cur}") or p.name.endswith(f"-{cur}.zwc"))]

    return Target("zcompdump", "default", "旧 zsh 版本遗留的 ~/.zcompdump-*",
                  avail=lambda c: have("zsh"), paths=lambda c: stale())


def _shader() -> Target:
    c_ = xdg_cache()

    def dirs() -> list[Path]:
        return [c_ / "mesa_shader_cache", c_ / "mesa_shader_cache_db", c_ / "radv_builtin_shaders",
                *sorted(c_.glob("qtshadercache-*")), c_ / "nvidia" / "GLCache"]

    return Target("shader", "opt", "Mesa/RADV/Qt 着色器缓存（删除后首次启动游戏/应用会卡顿）",
                  avail=lambda c: bool(existing(dirs())), paths=lambda c: dirs())


def _trash() -> Target:
    root = xdg_data() / "Trash"

    def clean(c: Ctx):
        if have("gio"):
            c.cmd("gio", "trash", "--empty")
        else:
            c.remove([*sorted((root / "files").glob("*")), *sorted((root / "files").glob(".*")),
                      *sorted((root / "info").glob("*"))])

    return Target("trash", "opt", "清空回收站（不可恢复）", avail=lambda c: root.is_dir(),
                  paths=lambda c: [root / "files", root / "info", root / "expunged"], clean=clean)


def _pacman() -> Target:
    def clean(c: Ctx):
        c.cmd("sudo", "paccache", f"-rk{PACCACHE_KEEP}")
        c.cmd("sudo", "paccache", "-ruk0")

    return Target("pacman", "opt", f"sudo paccache -rk{PACCACHE_KEEP} 与 -ruk0（已卸载包的缓存全部删除）",
                  avail=lambda c: have("paccache"), paths=lambda c: [Path("/var/cache/pacman/pkg")], clean=clean)


def _journal_size(c: Ctx) -> int:
    line = sysinfo.query(["journalctl", "--disk-usage"]) or ""
    m = re.search(r"([\d.]+)([KMGT])", line)
    if not m:
        return 0
    return int(float(m.group(1)) * 1024 ** "KMGT".index(m.group(2)) * 1024)


def _journal() -> Target:
    return Target("journal", "opt", f"sudo journalctl --vacuum-size={JOURNAL_MAX}",
                  avail=lambda c: have("journalctl"), paths=lambda c: [], size=_journal_size,
                  clean=lambda c: c.cmd("sudo", "journalctl", f"--vacuum-size={JOURNAL_MAX}"))


def targets() -> list[Target]:
    return [
        _yay(), _firefox(), _electron(), common.uv(), common.pip(), common.pnpm(), common.npm(),
        common.bun(), common.yarn(), common.go(), common.cargo(), common.nuget(), common.vcpkg(),
        common.precommit(), _lsp(), _wine(), _kde(), _thumbnails(), _zcompdump(),
        _shader(), common.gradle(), common.maven(), _trash(), _pacman(), _journal(), common.docker(),
    ]


# ---------------------------------------------------------------- 报告

def overview() -> None:
    seen = set()
    for p in (Path("/"), home()):
        try:
            st = os.stat(p)
            if st.st_dev in seen:
                continue
            seen.add(st.st_dev)
            u = shutil.disk_usage(p)
        except OSError:
            continue
        ui.info(f"  {ui.pad(str(p), 30)}已用 {ui.human(u.used)} / {ui.human(u.total)}，可用 {ui.human(u.free)}")
    ui.info(f"~/.cache 总占用: {ui.human(fsutil.size_of([xdg_cache()]))}")


def uncovered_roots() -> list[Path]:
    return [xdg_cache()]


def scan_extra(ctx: Ctx) -> None:
    ui.section("仅报告")
    if have("paccache"):
        def saved(args: list[str]) -> str:
            m = re.search(r"saved: ([^)]*)", sysinfo.run(["paccache", *args]).stdout)
            return m.group(1) if m else "0"
        ui.info(f"pacman: 每包保留 {PACCACHE_KEEP} 版可省 {saved([f'-dk{PACCACHE_KEEP}'])}，已卸载包可省 {saved(['-duk0'])}")
    if have("pacman"):
        orphans = sysinfo.run(["pacman", "-Qdtq"]).stdout.split()
        debug = sum(1 for o in orphans if o.endswith("-debug"))
        ui.info(f"孤立包: {len(orphans)} 个（其中 -debug 包 {debug} 个）；检查 pacman -Qdt，移除 pacman -Qdtq | sudo pacman -Rns -")
    core = Path("/var/lib/systemd/coredump")
    if core.is_dir():
        ui.info(f"coredump: {ui.human(fsutil.size_of([core]))}（systemd-tmpfiles 按期清理；coredumpctl list 查看）")
    ui.info("Electron 的 Service Worker / IndexedDB 属于离线数据，不在清理范围内")


def _installed(names: tuple[str, ...]) -> bool:
    for n in names:
        if have(n) or (have("pacman") and sysinfo.run(["pacman", "-Qq", n]).ok):
            return True
    return False


def leftovers() -> None:
    ui.section("已卸载应用的残留目录（确认无用后手动删除）")
    any_ = False
    for names, rels in LEFTOVER_MAP:
        if _installed(names):
            continue
        for rel in rels:
            p = home() / rel
            if p.exists():
                ui.info(f"  {ui.pad(ui.human(fsutil.size_of([p])), 10)}~/{ui.pad(rel, 36)}（{names[0]} 未安装）")
                any_ = True
    if not any_:
        ui.ok("无")

    ui.section(f"超过 {STALE_DAYS} 天无写入的 Electron/Chromium 配置目录")
    any_ = False
    cutoff = time.time() - STALE_DAYS * 86400
    for d in electron_app_dirs():
        newest = 0.0
        for p in [d, *d.glob("*"), *d.glob("*/*")]:
            try:
                newest = max(newest, p.lstat().st_mtime)
            except OSError:
                continue
        if newest and newest < cutoff:
            ui.info(f"  {ui.pad(ui.human(fsutil.size_of([d])), 10)}{ui.pad(fsutil.tilde(d), 36)}"
                    f"最后写入 {time.strftime('%Y-%m-%d', time.localtime(newest))}")
            any_ = True
    if not any_:
        ui.ok("无")

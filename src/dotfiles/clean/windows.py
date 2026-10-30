"""Windows 清理目标：%TEMP%、scoop、浏览器与 Electron 缓存、语言工具链缓存，以及需要管理员的系统级目标。
从原 scripts/clean.ps1 移植（见提交 a15c9b5）；路径在运行时求值，便于在其他平台上测试文件系统逻辑。"""
from __future__ import annotations

import json
import os
import re
import shutil
import string
import time
from functools import cache
from pathlib import Path

from dotfiles import chez, fsutil, sysinfo, ui
from dotfiles.clean import Ctx, Skip, Target, common, existing, sub_dirs
from dotfiles.sysinfo import appdata, have, home, localappdata, xdg_cache


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value.isdigit() else default


TEMP_DAYS = _int_env("TEMP_DAYS", 7)
STALE_DAYS = _int_env("STALE_DAYS", 180)
UNCOVERED_MIN_MB = _int_env("UNCOVERED_MIN_MB", 100)
UNCOVERED_ALWAYS = ("Temp", "Mozilla", "Google", "Microsoft", "BraveSoftware", "CrashDumps")

ELECTRON_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
                       "GPUPersistentCache", "GrShaderCache", "GraphiteDawnCache", "ShaderCache")
VSCODE_FAMILY = ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf", "Kiro", "Trae")
VSCODE_CACHE_DIRS = ("CachedData", "CachedExtensionVSIXs", "CachedProfilesData", "logs")
# %APPDATA% 目录名与进程名不一致的应用（没有锁文件时用于运行检测）
PROCESS_ALIAS = {"LarkShell": ("Feishu", "Lark"), "Docker Desktop": ("Docker Desktop",), "Code": ("Code",)}
BROWSERS = (Path("Microsoft/Edge/User Data"), Path("Google/Chrome/User Data"),
            Path("BraveSoftware/Brave-Browser/User Data"))
BROWSER_PROFILE_CACHES = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache")
BROWSER_ROOT_CACHES = ("GrShaderCache", "GraphiteDawnCache", "ShaderCache", "GPUPersistentCache")

# 已卸载应用的残留：检测名（命令、“应用和功能”中的程序名、scoop 应用，或含 / 的相对路径存在）→ 路径（相对 ~）
LEFTOVER_MAP = (
    (("cursor",), ("AppData/Roaming/Cursor",)),
    (("cursor", "cursor-agent", "AppData/Local/cursor-agent"), (".cursor",)),
    (("code", "Microsoft Visual Studio Code"), ("AppData/Roaming/Code", ".vscode")),
    (("chrome", "Google Chrome"), ("AppData/Local/Google/Chrome",)),
    (("notion",), ("AppData/Roaming/Notion",)),
    (("Feishu", "飞书", "Lark"), ("AppData/Roaming/LarkShell", "AppData/Roaming/LarkShellSingleton")),
    (("Docker Desktop",), ("AppData/Roaming/Docker", "AppData/Roaming/Docker Desktop")),
    (("MongoDB Compass", "mongodb-compass"), ("AppData/Roaming/MongoDB Compass",)),
    (("Quark", "夸克"), ("AppData/Roaming/Quark", "AppData/Roaming/The Quark Authors")),
    (("BaiduNetdisk", "百度网盘"), ("AppData/Roaming/BaiduNetdisk", "AppData/Roaming/BaiduYunGuanjia",
                                     "AppData/Roaming/BaiduYunKernel")),
    (("WeMeet", "腾讯会议", "TencentMeeting"), ("AppData/Roaming/WeMeetApp",)),
    (("draw.io",), ("AppData/Roaming/draw.io",)),
    (("Wireshark",), ("AppData/Roaming/Wireshark",)),
    (("VMware Workstation", "VMware Player"), ("AppData/Roaming/VMware",)),
    (("Apifox",), ("AppData/Roaming/apifox",)),
    (("uTools",), ("AppData/Roaming/uTools",)),
    (("Clash Party", "Mihomo Party", "mihomo-party"), ("AppData/Roaming/mihomo-party",)),
    (("MongoDB", "mongodb"), ("AppData/Roaming/mongodb",)),
    (("Nushell", "nu"), ("AppData/Roaming/nushell",)),
)


def scoop_dir() -> Path:
    return Path(os.environ["SCOOP"]) if os.environ.get("SCOOP") else home() / "scoop"


def temp_dir() -> Path:
    return Path(os.environ.get("TEMP") or localappdata() / "Temp")


def wu_download() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SoftwareDistribution" / "Download"


def safe_roots() -> list[Path]:
    return [home(), scoop_dir(), wu_download()]


def protected() -> list[Path]:
    h = home()
    return [h, localappdata(), appdata(), temp_dir(), scoop_dir(), xdg_cache(), h / "Documents",
            h / "Desktop", h / "Downloads", h / ".ssh", chez.REPO]


# ---------------------------------------------------------------- 运行检测

def pid_lock(path: Path) -> bool:
    """VS Code 的 code.lock 中是 PID。"""
    try:
        m = re.search(r"(\d+)", path.read_text(errors="ignore"))
    except OSError:
        return False
    return bool(m) and sysinfo.pid_alive(int(m.group(1)))


def electron_busy(d: Path) -> bool:
    if (d / "code.lock").exists():
        return pid_lock(d / "code.lock")
    if (d / "lockfile").exists():
        return sysinfo.file_locked(d / "lockfile")
    names = [d.name, re.sub(r"\s", "", d.name), *PROCESS_ALIAS.get(d.name, ())]
    return sysinfo.proc_running(names)


def electron_app_dirs() -> list[Path]:
    """只认 VS Code 系或带 Chromium 标记（Local State 或 GPUCache）的目录。"""
    out = []
    for d in sub_dirs(appdata()):
        if d.name in VSCODE_FAMILY or (d / "Local State").exists() or (d / "GPUCache").exists():
            out.append(d)
    return out


# ---------------------------------------------------------------- 目标

def _temp() -> Target:
    def stale() -> list[Path]:
        cutoff = time.time() - TEMP_DAYS * 86400
        out = []
        try:
            entries = list(os.scandir(temp_dir()))
        except OSError:
            return out
        for e in entries:
            try:
                if e.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
                if e.is_dir(follow_symlinks=False) and not fsutil.is_link(e):
                    recent = False
                    for dirpath, dirnames, filenames in os.walk(e.path):
                        for n in dirnames + filenames:
                            try:
                                if os.lstat(os.path.join(dirpath, n)).st_mtime > cutoff:
                                    recent = True
                                    break
                            except OSError:
                                continue
                        if recent:
                            break
                    if recent:
                        continue
                out.append(Path(e.path))
            except OSError:
                continue
        return out

    def clean(c: Ctx):
        items = stale()
        if not c.dry:
            c.remove(items)
            return
        for p in items:
            c.deleter.assert_deletable(p)
        ui.dim(f"  + 删除 %TEMP% 中 {len(items)} 个顶层条目（{ui.human(fsutil.size_of(items))}）")

    return Target("temp", "default", f"%TEMP% 中超过 TEMP_DAYS({TEMP_DAYS}) 天未修改的文件与目录（被占用的文件跳过）",
                  avail=lambda c: temp_dir().is_dir(), paths=lambda c: stale(), clean=clean)


def _scoop() -> Target:
    def stale(c: Ctx) -> list[Path]:
        apps = sub_dirs(scoop_dir() / "apps")
        out: list[Path] = []
        installed: dict[str, str] = {}
        for app in apps:
            cur = app / "current"
            if not os.path.lexists(cur):
                continue
            try:
                target = Path(os.readlink(cur)).name if fsutil.is_link(cur) else "current"
            except OSError:
                target = "current"
            out += [v for v in sub_dirs(app) if v.name not in ("current", target)]
            try:
                installed[app.name] = json.loads((cur / "manifest.json").read_text(encoding="utf-8-sig")).get("version", "")
            except (OSError, ValueError):
                pass
        cache_dir = scoop_dir() / "cache"
        if cache_dir.is_dir():
            for f in sorted(cache_dir.iterdir()):
                if not f.is_file():
                    continue
                parts = f.name.split("#")
                if c.deep or len(parts) < 2 or installed.get(parts[0]) != parts[1]:
                    out.append(f)
        return out

    def clean(c: Ctx):
        c.cmd("scoop", "cleanup", "-a", "-k")
        if c.deep:
            c.cmd("scoop", "cache", "rm", "-a")

    return Target("scoop", "default", "scoop cleanup -a -k：旧版本与过期安装包（--deep 额外 scoop cache rm -a）",
                  avail=lambda c: have("scoop"), paths=stale, clean=clean)


def _electron() -> Target:
    def paths(c: Ctx):
        out: list = []
        for d in electron_app_dirs():
            if electron_busy(d):
                out.append(Skip(f"{d.name} 正在运行"))
                continue
            for b in [d, *sub_dirs(d, "Default"), *sub_dirs(d, "Profile*"), *sub_dirs(d / "Partitions")]:
                out += existing([b / s for s in ELECTRON_CACHE_DIRS])
            if d.name in VSCODE_FAMILY:
                out += existing([d / s for s in VSCODE_CACHE_DIRS])
        return out

    return Target("electron", "default", "%APPDATA% 下 Electron 应用的 Cache、Code Cache、GPUCache 等（应用运行时跳过）",
                  paths=paths)


def _browser() -> Target:
    def paths(c: Ctx):
        out: list = []
        for rel in BROWSERS:
            root = localappdata() / rel
            if not root.is_dir():
                continue
            if sysinfo.file_locked(root / "lockfile"):
                out.append(Skip(f"{rel.parts[1]} 正在运行"))
                continue
            profiles = [*sub_dirs(root, "Default"), *sub_dirs(root, "Profile *"), *sub_dirs(root, "Guest Profile")]
            for p in profiles:
                out += existing([p / s for s in BROWSER_PROFILE_CACHES])
            out += existing([root / s for s in BROWSER_ROOT_CACHES])
        return out

    return Target("browser", "default", "Edge / Chrome / Brave 各配置文件的 Cache、Code Cache、GPUCache 与着色器缓存（浏览器运行时跳过）",
                  avail=lambda c: any((localappdata() / r).is_dir() for r in BROWSERS), paths=paths)


def _firefox() -> Target:
    """缓存在 Local，锁在 Roaming 的同名配置目录中。"""
    def paths(c: Ctx):
        out: list = []
        for p in sub_dirs(localappdata() / "Mozilla" / "Firefox" / "Profiles"):
            if sysinfo.file_locked(appdata() / "Mozilla" / "Firefox" / "Profiles" / p.name / "parent.lock"):
                out.append(Skip(f"Firefox 配置 {p.name} 正在使用"))
                continue
            out += existing([p / s for s in ("cache2", "startupCache", "thumbnails", "jumpListCache")])
        return out

    return Target("firefox", "default", "Firefox 网络缓存 cache2 / startupCache / thumbnails（配置文件被占用时跳过）",
                  avail=lambda c: (localappdata() / "Mozilla" / "Firefox" / "Profiles").is_dir(), paths=paths)


def _updaters() -> Target:
    """electron-updater 下载的安装包；应用运行时可能在等待退出后安装 pending 中的更新，所以跳过。"""
    def paths(c: Ctx):
        out: list = []
        for d in sub_dirs(localappdata(), "*-updater"):
            app = re.sub(r"^@[^-]+-", "", re.sub(r"-updater$", "", d.name))
            roaming = appdata() / app
            if sysinfo.proc_running([app, app.replace("-", ""), app.replace("-", " ")]) or \
                    (roaming.exists() and electron_busy(roaming)):
                out.append(Skip(f"{app} 正在运行"))
                continue
            out += existing([d / "installer.exe", d / "pending"])
        return out

    return Target("updaters", "default", "%LOCALAPPDATA%\\*-updater 中 electron-updater 下载的安装包（应用运行时跳过）",
                  avail=lambda c: bool(sub_dirs(localappdata(), "*-updater")), paths=paths)


def _cloudmusic() -> Target:
    cache_dir = localappdata() / "NetEase" / "CloudMusic" / "Cache"
    return Target("cloudmusic", "opt", "网易云音乐播放缓存（不含下载的歌曲；运行时跳过）",
                  avail=lambda c: cache_dir.is_dir(),
                  paths=lambda c: [Skip("网易云音乐正在运行")] if sysinfo.proc_running(["cloudmusic"]) else [cache_dir])


def _crashdumps() -> Target:
    wer = localappdata() / "Microsoft" / "Windows" / "WER"
    dirs = [localappdata() / "CrashDumps", wer / "ReportArchive", wer / "ReportQueue"]
    return Target("crashdumps", "opt", "%LOCALAPPDATA%\\CrashDumps 与 WER 报告（调试崩溃时可能需要）",
                  avail=lambda c: bool(existing(dirs)), paths=lambda c: existing(dirs))


def _shader() -> Target:
    def dirs() -> list[Path]:
        return [localappdata() / p for p in ("D3DSCache", "NVIDIA/DXCache", "NVIDIA/GLCache", "AMD/DxCache",
                                             "AMD/DxcCache", "AMD/VkCache", "Intel/ShaderCache")]

    return Target("shader", "opt", "DirectX / NVIDIA / AMD / Intel 着色器缓存（删除后首次启动游戏会卡顿）",
                  avail=lambda c: bool(existing(dirs())), paths=lambda c: existing(dirs()))


def _nugetpkgs() -> Target:
    path = Path(os.environ["NUGET_PACKAGES"]) if os.environ.get("NUGET_PACKAGES") else home() / ".nuget" / "packages"
    busy = ("devenv", "MSBuild", "dotnet")

    def clean(c: Ctx):
        if sysinfo.proc_running(busy):
            ui.warn("Visual Studio / MSBuild / dotnet 正在运行，跳过")
            return
        if have("dotnet"):
            c.cmd("dotnet", "nuget", "locals", "global-packages", "--clear")
        else:
            c.remove([path])

    return Target("nugetpkgs", "opt", "dotnet nuget locals global-packages --clear（下次构建重新下载全部包）",
                  avail=lambda c: path.is_dir(),
                  paths=lambda c: [Skip("Visual Studio / MSBuild / dotnet 正在运行")] if sysinfo.proc_running(busy) else [path],
                  clean=clean)


def _recycle() -> Target:
    def size(c: Ctx) -> int:
        r = sysinfo.powershell("$s=0; (New-Object -ComObject Shell.Application).NameSpace(10).Items() | "
                               "ForEach-Object { $s += [long]$_.Size }; $s")
        try:
            return int(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return -1

    def clean(c: Ctx):
        if c.dry:
            ui.dim("  + Clear-RecycleBin -Force")
            return
        r = sysinfo.powershell("Clear-RecycleBin -Force -ErrorAction Stop")
        if not r.ok:
            ui.warn(f"清空回收站失败: {r.stderr.strip()[:200]}")
            c.failed = True

    return Target("recycle", "opt", "清空所有驱动器的回收站（不可恢复）", paths=lambda c: [], size=size, clean=clean)


def _require_admin(c: Ctx) -> bool:
    if sysinfo.is_admin():
        return True
    ui.warn("需要在管理员终端中运行，跳过")
    c.failed = True
    return False


def _wu() -> Target:
    def paths(c: Ctx):
        try:
            return sorted(wu_download().iterdir())
        except OSError:
            return []

    def clean(c: Ctx):
        if c.dry:
            ui.dim(f"  + Stop-Service wuauserv, bits；删除 {wu_download()}\\*；Start-Service wuauserv, bits")
            return
        if not _require_admin(c):
            return
        sysinfo.powershell("Stop-Service wuauserv, bits -Force")
        try:
            c.remove(paths(c))
        finally:
            sysinfo.powershell("Start-Service wuauserv, bits")

    return Target("wu", "opt", "Windows Update 下载缓存 SoftwareDistribution\\Download（需要管理员）",
                  avail=lambda c: wu_download().is_dir(), paths=paths, clean=clean)


def _dism() -> Target:
    def clean(c: Ctx):
        if c.dry:
            ui.dim("  + DISM /Online /Cleanup-Image /StartComponentCleanup")
            return
        if _require_admin(c):
            c.cmd("dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup")

    return Target("dism", "opt", "DISM /StartComponentCleanup 清理组件存储（需要管理员，耗时较长）",
                  paths=lambda c: [], size=lambda c: -1, clean=clean)


def targets() -> list[Target]:
    return [
        _temp(), _scoop(), _electron(), _browser(), _firefox(), _updaters(),
        common.uv(), common.pip(), common.pnpm(), common.npm(), common.bun(), common.yarn(), common.go(),
        common.cargo(), common.nuget(), common.vcpkg(), common.precommit(),
        _crashdumps(), _cloudmusic(), _shader(), _nugetpkgs(), common.maven(), common.gradle(),
        _recycle(), common.docker(), _wu(), _dism(),
    ]


# ---------------------------------------------------------------- 报告

def overview() -> None:
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        try:
            u = shutil.disk_usage(root)
        except OSError:
            continue
        if u.used:
            ui.info(f"  {letter}:  已用 {ui.human(u.used)}  可用 {ui.human(u.free)}")
    ui.info(f"%TEMP% 总占用: {ui.human(fsutil.size_of([temp_dir()]))}")


def uncovered_roots() -> list[Path]:
    return [localappdata(), xdg_cache()]


def scan_extra(ctx: Ctx) -> None:
    ui.section("仅报告")
    explorer = localappdata() / "Microsoft" / "Windows" / "Explorer"
    thumbs = sorted(explorer.glob("*cache_*.db")) if explorer.is_dir() else []
    ui.info(f"缩略图 / 图标缓存: {ui.human(fsutil.size_of(thumbs))}（被资源管理器占用；用磁盘清理 cleanmgr 的缩略图项清理）")
    vhds = [*(localappdata() / "Packages").glob("*/LocalState/ext4.vhdx"),
            *(localappdata() / "Docker" / "wsl").rglob("*.vhdx")]
    for v in vhds:
        ui.info(f"WSL 虚拟磁盘: {ui.human(fsutil.size_of([v]))}  {fsutil.tilde(v)}（压缩需先 wsl --shutdown，建议手动执行）")
    ui.info("Electron / 浏览器的 Service Worker、IndexedDB、Local Storage 属于离线数据，不在清理范围内")


@cache
def _installed_names() -> tuple[str, ...]:
    names: list[str] = []
    try:
        import winreg

        roots = ((winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                 (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                 (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"))
        for hive, path in roots:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            names.append(str(winreg.QueryValueEx(sub, "DisplayName")[0]))
                    except OSError:
                        continue
    except ImportError:
        pass
    names += sysinfo.powershell("Get-AppxPackage -ErrorAction SilentlyContinue | ForEach-Object Name").stdout.split()
    names += [d.name for d in sub_dirs(scoop_dir() / "apps")]
    return tuple(names)


def app_installed(names: tuple[str, ...]) -> bool:
    for n in names:
        if "/" in n:
            if (home() / n).exists():
                return True
            continue
        if have(n):
            return True
        # 中文名没有词边界，按子串匹配；ASCII 名按整词匹配，避免 nu 之类的短名误命中
        rx = rf"(?<!\w){re.escape(n)}(?!\w)" if n.isascii() else re.escape(n)
        if any(re.search(rx, inst, re.I) for inst in _installed_names()):
            return True
    return False


def leftovers() -> None:
    ui.section("已卸载应用的残留目录（确认无用后手动删除）")
    any_ = False
    for names, rels in LEFTOVER_MAP:
        if app_installed(names):
            continue
        for rel in rels:
            p = home() / rel
            if not p.exists() or electron_busy(p):   # 目录正被占用：应用改名或以其他方式安装
                continue
            ui.info(f"  {ui.pad(ui.human(fsutil.size_of([p])), 10)}~/{ui.pad(rel, 40)}（{names[0]} 未安装）")
            any_ = True
    if not any_:
        ui.ok("无")

    ui.section(f"超过 {STALE_DAYS} 天无写入的 Electron 配置目录")
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
            ui.info(f"  {ui.pad(ui.human(fsutil.size_of([d])), 10)}{ui.pad(fsutil.tilde(d), 40)}"
                    f"最后写入 {time.strftime('%Y-%m-%d', time.localtime(newest))}")
            any_ = True
    if not any_:
        ui.ok("无")

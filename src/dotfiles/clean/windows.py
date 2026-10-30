"""Windows cleanup targets: %TEMP%, scoop, browser and Electron caches, language toolchain caches,
plus admin-only system targets. Ported from the original scripts/clean.ps1 (commit a15c9b5);
paths are resolved at call time so the filesystem logic is testable on other platforms."""
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
# apps whose %APPDATA% dir name differs from their process name (used for running detection without a lock file)
PROCESS_ALIAS = {"LarkShell": ("Feishu", "Lark"), "Docker Desktop": ("Docker Desktop",), "Code": ("Code",)}
BROWSERS = (Path("Microsoft/Edge/User Data"), Path("Google/Chrome/User Data"),
            Path("BraveSoftware/Brave-Browser/User Data"))
BROWSER_PROFILE_CACHES = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache")
BROWSER_ROOT_CACHES = ("GrShaderCache", "GraphiteDawnCache", "ShaderCache", "GPUPersistentCache")

# Leftovers from uninstalled apps: detection names (command, "Apps & Features" entry, scoop app, or an
# existing relative path with /) -> paths (relative to ~). Some detection names are the localized
# DisplayName values shown in "Apps & Features" on a Chinese-locale Windows; keep them as-is.
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


# ---------------------------------------------------------------- running detection

def pid_lock(path: Path) -> bool:
    """VS Code's code.lock contains a PID."""
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
    """Only VS Code-family dirs or those with a Chromium marker (Local State or GPUCache)."""
    out = []
    for d in sub_dirs(appdata()):
        if d.name in VSCODE_FAMILY or (d / "Local State").exists() or (d / "GPUCache").exists():
            out.append(d)
    return out


# ---------------------------------------------------------------- targets

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
        ui.dim(f"  + delete {len(items)} top-level entries in %TEMP% ({ui.human(fsutil.size_of(items))})")

    return Target("temp", "default", f"top-level %TEMP% entries older than TEMP_DAYS({TEMP_DAYS}) days (locked files are skipped)",
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

    return Target("scoop", "default", "scoop cleanup -a -k: old versions and stale installers (--deep also scoop cache rm -a)",
                  avail=lambda c: have("scoop"), paths=stale, clean=clean)


def _electron() -> Target:
    def paths(c: Ctx):
        out: list = []
        for d in electron_app_dirs():
            if electron_busy(d):
                out.append(Skip(f"{d.name} is running"))
                continue
            for b in [d, *sub_dirs(d, "Default"), *sub_dirs(d, "Profile*"), *sub_dirs(d / "Partitions")]:
                out += existing([b / s for s in ELECTRON_CACHE_DIRS])
            if d.name in VSCODE_FAMILY:
                out += existing([d / s for s in VSCODE_CACHE_DIRS])
        return out

    return Target("electron", "default",
                  "Cache, Code Cache, GPUCache etc. of Electron apps under %APPDATA% (skipped while running)",
                  paths=paths)


def _browser() -> Target:
    def paths(c: Ctx):
        out: list = []
        for rel in BROWSERS:
            root = localappdata() / rel
            if not root.is_dir():
                continue
            if sysinfo.file_locked(root / "lockfile"):
                out.append(Skip(f"{rel.parts[1]} is running"))
                continue
            profiles = [*sub_dirs(root, "Default"), *sub_dirs(root, "Profile *"), *sub_dirs(root, "Guest Profile")]
            for p in profiles:
                out += existing([p / s for s in BROWSER_PROFILE_CACHES])
            out += existing([root / s for s in BROWSER_ROOT_CACHES])
        return out

    return Target("browser", "default",
                  "profile caches and shader caches of Edge / Chrome / Brave (skipped while the browser is running)",
                  avail=lambda c: any((localappdata() / r).is_dir() for r in BROWSERS), paths=paths)


def _firefox() -> Target:
    """Caches live in Local; the profile lock lives in Roaming under the same name."""
    def paths(c: Ctx):
        out: list = []
        for p in sub_dirs(localappdata() / "Mozilla" / "Firefox" / "Profiles"):
            if sysinfo.file_locked(appdata() / "Mozilla" / "Firefox" / "Profiles" / p.name / "parent.lock"):
                out.append(Skip(f"Firefox profile {p.name} is in use"))
                continue
            out += existing([p / s for s in ("cache2", "startupCache", "thumbnails", "jumpListCache")])
        return out

    return Target("firefox", "default", "Firefox network cache cache2 / startupCache / thumbnails (skipped while in use)",
                  avail=lambda c: (localappdata() / "Mozilla" / "Firefox" / "Profiles").is_dir(), paths=paths)


def _updaters() -> Target:
    """Installers downloaded by electron-updater; skipped while the app runs because it may be
    waiting to install a pending update on exit."""
    def paths(c: Ctx):
        out: list = []
        for d in sub_dirs(localappdata(), "*-updater"):
            app = re.sub(r"^@[^-]+-", "", re.sub(r"-updater$", "", d.name))
            roaming = appdata() / app
            if sysinfo.proc_running([app, app.replace("-", ""), app.replace("-", " ")]) or \
                    (roaming.exists() and electron_busy(roaming)):
                out.append(Skip(f"{app} is running"))
                continue
            out += existing([d / "installer.exe", d / "pending"])
        return out

    return Target("updaters", "default", "electron-updater installers in %LOCALAPPDATA%\\*-updater (skipped while running)",
                  avail=lambda c: bool(sub_dirs(localappdata(), "*-updater")), paths=paths)


def _cloudmusic() -> Target:
    cache_dir = localappdata() / "NetEase" / "CloudMusic" / "Cache"
    return Target("cloudmusic", "opt", "NetEase Cloud Music playback cache (not downloaded songs; skipped while running)",
                  avail=lambda c: cache_dir.is_dir(),
                  paths=lambda c: [Skip("NetEase Cloud Music is running")] if sysinfo.proc_running(["cloudmusic"]) else [cache_dir])


def _crashdumps() -> Target:
    wer = localappdata() / "Microsoft" / "Windows" / "WER"
    dirs = [localappdata() / "CrashDumps", wer / "ReportArchive", wer / "ReportQueue"]
    return Target("crashdumps", "opt", "%LOCALAPPDATA%\\CrashDumps and WER reports (may be needed to debug a crash)",
                  avail=lambda c: bool(existing(dirs)), paths=lambda c: existing(dirs))


def _shader() -> Target:
    def dirs() -> list[Path]:
        return [localappdata() / p for p in ("D3DSCache", "NVIDIA/DXCache", "NVIDIA/GLCache", "AMD/DxCache",
                                             "AMD/DxcCache", "AMD/VkCache", "Intel/ShaderCache")]

    return Target("shader", "opt", "DirectX / NVIDIA / AMD / Intel shader caches (first game launch after deletion will stutter)",
                  avail=lambda c: bool(existing(dirs())), paths=lambda c: existing(dirs()))


def _nugetpkgs() -> Target:
    path = Path(os.environ["NUGET_PACKAGES"]) if os.environ.get("NUGET_PACKAGES") else home() / ".nuget" / "packages"
    busy = ("devenv", "MSBuild", "dotnet")

    def clean(c: Ctx):
        if sysinfo.proc_running(busy):
            ui.warn("Visual Studio / MSBuild / dotnet is running; skipping")
            return
        if have("dotnet"):
            c.cmd("dotnet", "nuget", "locals", "global-packages", "--clear")
        else:
            c.remove([path])

    return Target("nugetpkgs", "opt", "dotnet nuget locals global-packages --clear (all packages re-download next build)",
                  avail=lambda c: path.is_dir(),
                  paths=lambda c: [Skip("Visual Studio / MSBuild / dotnet is running")] if sysinfo.proc_running(busy) else [path],
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
            ui.warn(f"failed to empty the Recycle Bin: {r.stderr.strip()[:200]}")
            c.failed = True

    return Target("recycle", "opt", "empty the Recycle Bin on all drives (unrecoverable)", paths=lambda c: [], size=size, clean=clean)


def _require_admin(c: Ctx) -> bool:
    if sysinfo.is_admin():
        return True
    ui.warn("needs an elevated terminal; skipping")
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
            ui.dim(f"  + Stop-Service wuauserv, bits; delete {wu_download()}\\*; Start-Service wuauserv, bits")
            return
        if not _require_admin(c):
            return
        sysinfo.powershell("Stop-Service wuauserv, bits -Force")
        try:
            c.remove(paths(c))
        finally:
            sysinfo.powershell("Start-Service wuauserv, bits")

    return Target("wu", "opt", "Windows Update download cache SoftwareDistribution\\Download (needs admin)",
                  avail=lambda c: wu_download().is_dir(), paths=paths, clean=clean)


def _dism() -> Target:
    def clean(c: Ctx):
        if c.dry:
            ui.dim("  + DISM /Online /Cleanup-Image /StartComponentCleanup")
            return
        if _require_admin(c):
            c.cmd("dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup")

    return Target("dism", "opt", "DISM /StartComponentCleanup component store cleanup (needs admin, takes a while)",
                  paths=lambda c: [], size=lambda c: -1, clean=clean)


def targets() -> list[Target]:
    return [
        _temp(), _scoop(), _electron(), _browser(), _firefox(), _updaters(),
        common.uv(), common.pip(), common.pnpm(), common.npm(), common.bun(), common.yarn(), common.go(),
        common.cargo(), common.nuget(), common.vcpkg(), common.precommit(),
        _crashdumps(), _cloudmusic(), _shader(), _nugetpkgs(), common.maven(), common.gradle(),
        _recycle(), common.docker(), _wu(), _dism(),
    ]


# ---------------------------------------------------------------- reports

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
            ui.info(f"  {letter}:  used {ui.human(u.used)}  free {ui.human(u.free)}")
    ui.info(f"%TEMP% total: {ui.human(fsutil.size_of([temp_dir()]))}")


def uncovered_roots() -> list[Path]:
    return [localappdata(), xdg_cache()]


def scan_extra(ctx: Ctx) -> None:
    ui.section("Report only")
    explorer = localappdata() / "Microsoft" / "Windows" / "Explorer"
    thumbs = sorted(explorer.glob("*cache_*.db")) if explorer.is_dir() else []
    ui.info(f"thumbnail / icon caches: {ui.human(fsutil.size_of(thumbs))} (held by Explorer; clean via Disk Cleanup's Thumbnails item)")
    vhds = [*(localappdata() / "Packages").glob("*/LocalState/ext4.vhdx"),
            *(localappdata() / "Docker" / "wsl").rglob("*.vhdx")]
    for v in vhds:
        ui.info(f"WSL virtual disk: {ui.human(fsutil.size_of([v]))}  {fsutil.tilde(v)} "
                "(compact manually after wsl --shutdown)")
    ui.info("Electron / browser Service Worker, IndexedDB and Local Storage are offline data and outside the cleanup scope")


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
        # CJK names have no word boundaries, so match as a substring; ASCII names match whole words
        # to avoid short names like "nu" hitting everything
        rx = rf"(?<!\w){re.escape(n)}(?!\w)" if n.isascii() else re.escape(n)
        if any(re.search(rx, inst, re.I) for inst in _installed_names()):
            return True
    return False


def leftovers() -> None:
    ui.section("Leftover dirs of uninstalled apps (delete manually once confirmed useless)")
    any_ = False
    for names, rels in LEFTOVER_MAP:
        if app_installed(names):
            continue
        for rel in rels:
            p = home() / rel
            if not p.exists() or electron_busy(p):   # dir is in use: the app was renamed or installed differently
                continue
            ui.info(f"  {ui.pad(ui.human(fsutil.size_of([p])), 10)}~/{ui.pad(rel, 40)} ({names[0]} not installed)")
            any_ = True
    if not any_:
        ui.ok("none")

    ui.section(f"Electron config dirs with no writes for {STALE_DAYS} days")
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
                    f"last write {time.strftime('%Y-%m-%d', time.localtime(newest))}")
            any_ = True
    if not any_:
        ui.ok("none")

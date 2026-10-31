"""Linux (Arch) cleanup targets: centered on ~/.cache, covering tools and apps from archpkg that build up caches."""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from dotfiles.cli.clean import shared as common
from dotfiles.cli.clean.base import Ctx, Skip, Target, existing, sub_dirs
from dotfiles.utils import fsutil, platforms, ui
from dotfiles.utils.common import chez
from dotfiles.utils.common import config as dotconf
from dotfiles.utils.platforms import have, home, xdg_cache, xdg_config, xdg_data

ELECTRON_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
                       "GPUPersistentCache", "GrShaderCache", "ShaderCache")
VSCODE_FAMILY = ("Code", "Code - OSS", "VSCodium", "Cursor", "Windsurf")
VSCODE_CACHE_DIRS = ("CachedData", "CachedExtensionVSIXs", "CachedProfilesData", "logs")




def _c(name: str):
    """Tunable from dotfiles.toml [clean.linux] (an environment variable may override it)."""
    return dotconf.get(f"clean.linux.{name}")


def leftover_map() -> list[tuple[list[str], list[str]]]:
    """(detection names, paths relative to ~) from dotfiles.toml [[clean.linux.leftovers]]."""
    def as_list(v):
        return [v] if isinstance(v, str) else list(v)
    return [(as_list(t["detect"]), as_list(t["paths"])) for t in dotconf.get("clean.linux.leftovers")]

# ---------------------------------------------------------------- deletion scope

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


# ---------------------------------------------------------------- running detection

def lock_alive(path: Path) -> bool:
    """Whether the process behind a lock file is alive: symlink locks (Chromium SingletonLock ->
    host-PID, Firefox lock -> IP:+PID) or regular files holding a PID (VS Code code.lock)."""
    try:
        if path.is_symlink():
            m = re.search(r"[-+](\d+)$", os.readlink(path))
        elif path.is_file():
            m = re.search(r"(\d+)", path.read_bytes()[:32].decode(errors="ignore"))
        else:
            return False
    except OSError:
        return False
    return bool(m) and platforms.pid_alive(int(m.group(1)))


def _argv_match(rx: str) -> bool:
    pattern = re.compile(rx, re.I)
    return any(pattern.search(" ".join(p.argv[:2]) or p.exe) for p in platforms.processes())


def _cmdline_match(rx: str) -> bool:
    pattern = re.compile(rx)
    return any(pattern.search(" ".join(p.argv)) for p in platforms.processes())


def _pgrep(*names: str) -> bool:
    return platforms.proc_running(names)


# ---------------------------------------------------------------- targets

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
        r = platforms.run(["git", "-C", str(d), "ls-files", "-o", "--directory"])
        return [d / line.rstrip("/") for line in r.stdout.splitlines() if line.strip()]

    def paths(c: Ctx):
        if busy():
            return [Skip("yay/paru/makepkg is running")]
        installed = bases()
        out: list = []
        for d in dirs():
            out += untracked(d) if d.name in installed and (d / ".git").is_dir() else [d]
        return out

    def clean(c: Ctx):
        if busy():
            ui.warn("yay/paru/makepkg is running; skipping")
            return
        installed = bases()
        for d in dirs():
            if d.name in installed and (d / ".git").is_dir():
                if untracked(d):
                    c.cmd("git", "-C", str(d), "clean", "-ffdxq")
            else:
                ui.info(f"  uninstalled or not a git clone: {d.name}")
                c.remove([d])

    return Target("yay", "default",
                  "AUR build dirs (yay/paru): `git clean -ffdx` for installed packages' clones, delete clones of uninstalled ones",
                  avail=lambda c: bool(dirs()), paths=paths, clean=clean)


def _firefox() -> Target:
    def paths(c: Ctx):
        out: list = []
        for p in sub_dirs(xdg_cache() / "mozilla" / "firefox"):
            locks = [home() / ".mozilla" / "firefox" / p.name / "lock", xdg_config() / "mozilla" / "firefox" / p.name / "lock"]
            if any(lock_alive(lock) for lock in locks):
                out.append(Skip(f"Firefox profile {p.name} is in use"))
                continue
            out += [p / "cache2", p / "startupCache", p / "thumbnails"]
        return out

    return Target("firefox", "default",
                  "Firefox network cache cache2 / startupCache / thumbnails (skipped while the profile is in use)",
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
                out.append(Skip(f"{d.name} is running"))
                continue
            bases = [d, d / "Default", *sub_dirs(d, "Profile*"), *sub_dirs(d / "Partitions")]
            for b in bases:
                out += [b / s for s in ELECTRON_CACHE_DIRS if (b / s).is_dir()]
            if d.name in VSCODE_FAMILY:
                out += [d / s for s in VSCODE_CACHE_DIRS if (d / s).is_dir()]
        return out

    return Target("electron", "default",
                  "Cache, Code Cache, GPUCache etc. of Electron/Chromium apps under ~/.config (skipped while running)",
                  paths=paths)


def _lsp() -> Target:
    dirs = [xdg_cache() / "typescript", xdg_cache() / "jedi"]

    def paths(c: Ctx):
        if _cmdline_match(r"tsserver|typingsInstaller|jedi-language-server|pylsp"):
            return [Skip("a language server is running (close the editor first)")]
        return dirs

    return Target("lsp", "default", "editor language-server caches ~/.cache/typescript ~/.cache/jedi (skipped while running)",
                  avail=lambda c: bool(existing(dirs)), paths=paths)


def _wine() -> Target:
    dirs = [xdg_cache() / "wine", xdg_cache() / "winetricks"]
    return Target("wine", "default", "wine-mono/gecko installers and winetricks download cache",
                  avail=lambda c: bool(existing(dirs)), paths=lambda c: dirs)


def _kde() -> Target:
    """Only touches regenerable, non-mmapped caches; ksycoca / *.kcache are left to Plasma."""
    c_ = xdg_cache()

    def paths(c: Ctx):
        out: list = [c_ / "drkonqi" / "crashes", c_ / "bookmarksrunner", c_ / "kcrash-metadata"]
        if _pgrep("plasma-discover"):
            out.append(Skip("Discover is running; keeping the AppStream cache"))
        else:
            out += [c_ / "appstream", c_ / "discover"]
        return out

    return Target("kde", "default", "DrKonqi crash reports, KRunner bookmark copies, Discover/AppStream caches",
                  avail=lambda c: bool(existing([c_ / "drkonqi", c_ / "bookmarksrunner", c_ / "appstream"])),
                  paths=paths)


def _thumbnails() -> Target:
    root = xdg_cache() / "thumbnails"

    def old_files() -> list[Path]:
        cutoff = time.time() - _c("thumbnail_days") * 86400
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
            ui.dim(f"  + delete {len(files)} thumbnails untouched for {_c('thumbnail_days')} days in ~/.cache/thumbnails")
        else:
            c.remove(files)

    return Target("thumbnails", "default",
                  f"thumbnails not accessed for {_c('thumbnail_days')} days (thumbnail_days) (--deep: all)",
                  avail=lambda c: root.is_dir(), paths=paths, clean=clean)


def _zcompdump() -> Target:
    def stale() -> list[Path]:
        line = platforms.query(["zsh", "--version"]) or ""
        parts = line.split()
        if len(parts) < 2:
            return []
        cur = parts[1]
        return [p for p in sorted(home().glob(".zcompdump-*"))
                if not (p.name.endswith(f"-{cur}") or p.name.endswith(f"-{cur}.zwc"))]

    return Target("zcompdump", "default", "~/.zcompdump-* left over from older zsh versions",
                  avail=lambda c: have("zsh"), paths=lambda c: stale())


def _shader() -> Target:
    c_ = xdg_cache()

    def dirs() -> list[Path]:
        return [c_ / "mesa_shader_cache", c_ / "mesa_shader_cache_db", c_ / "radv_builtin_shaders",
                *sorted(c_.glob("qtshadercache-*")), c_ / "nvidia" / "GLCache"]

    return Target("shader", "opt", "Mesa/RADV/Qt shader caches (first game/app launch after deletion will stutter)",
                  avail=lambda c: bool(existing(dirs())), paths=lambda c: dirs())


def _trash() -> Target:
    root = xdg_data() / "Trash"

    def clean(c: Ctx):
        if have("gio"):
            c.cmd("gio", "trash", "--empty")
        else:
            c.remove([*sorted((root / "files").glob("*")), *sorted((root / "files").glob(".*")),
                      *sorted((root / "info").glob("*"))])

    return Target("trash", "opt", "empty the trash (unrecoverable)", avail=lambda c: root.is_dir(),
                  paths=lambda c: [root / "files", root / "info", root / "expunged"], clean=clean)


def _pacman() -> Target:
    def clean(c: Ctx):
        c.cmd("sudo", "paccache", f"-rk{_c('paccache_keep')}")
        c.cmd("sudo", "paccache", "-ruk0")

    return Target("pacman", "opt", f"sudo paccache -rk{_c('paccache_keep')} and -ruk0 (caches of uninstalled packages are all removed)",
                  avail=lambda c: have("paccache"), paths=lambda c: [Path("/var/cache/pacman/pkg")], clean=clean)


def _journal_size(c: Ctx) -> int:
    line = platforms.query(["journalctl", "--disk-usage"]) or ""
    m = re.search(r"([\d.]+)([KMGT])", line)
    if not m:
        return 0
    return int(float(m.group(1)) * 1024 ** "KMGT".index(m.group(2)) * 1024)


def _journal() -> Target:
    return Target("journal", "opt", f"sudo journalctl --vacuum-size={_c('journal_max')}",
                  avail=lambda c: have("journalctl"), paths=lambda c: [], size=_journal_size,
                  clean=lambda c: c.cmd("sudo", "journalctl", f"--vacuum-size={_c('journal_max')}"))


def targets() -> list[Target]:
    return [
        _yay(), _firefox(), _electron(), common.uv(), common.pip(), common.pnpm(), common.npm(),
        common.bun(), common.yarn(), common.go(), common.cargo(), common.nuget(), common.vcpkg(),
        common.precommit(), _lsp(), _wine(), _kde(), _thumbnails(), _zcompdump(),
        _shader(), common.gradle(), common.maven(), _trash(), _pacman(), _journal(), common.docker(),
    ]


# ---------------------------------------------------------------- reports

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
        ui.info(f"  {ui.pad(str(p), 30)}used {ui.human(u.used)} / {ui.human(u.total)}, free {ui.human(u.free)}")
    ui.info(f"~/.cache total: {ui.human(fsutil.size_of([xdg_cache()]))}")


def uncovered_roots() -> list[Path]:
    return [xdg_cache()]


def scan_extra(ctx: Ctx) -> None:
    ui.section("Report only")
    if have("paccache"):
        def saved(args: list[str]) -> str:
            m = re.search(r"saved: ([^)]*)", platforms.run(["paccache", *args]).stdout)
            return m.group(1) if m else "0"
        keep = _c("paccache_keep")
        ui.info(f"pacman: keeping {keep} version(s) saves {saved([f'-dk{keep}'])}, "
                f"removed packages save {saved(['-duk0'])}")
    if have("pacman"):
        orphans = platforms.run(["pacman", "-Qdtq"]).stdout.split()
        debug = sum(1 for o in orphans if o.endswith("-debug"))
        ui.info(f"orphan packages: {len(orphans)} ({debug} -debug packages); inspect with pacman -Qdt, "
                "remove with pacman -Qdtq | sudo pacman -Rns -")
    core = Path("/var/lib/systemd/coredump")
    if core.is_dir():
        ui.info(f"coredumps: {ui.human(fsutil.size_of([core]))} (cleaned periodically by systemd-tmpfiles; see coredumpctl list)")
    ui.info("Electron Service Worker / IndexedDB are offline data and outside the cleanup scope")


def _installed(names: tuple[str, ...]) -> bool:
    for n in names:
        if have(n) or (have("pacman") and platforms.run(["pacman", "-Qq", n]).ok):
            return True
    return False


def leftovers() -> None:
    ui.section("Leftover dirs of uninstalled apps (delete manually once confirmed useless)")
    any_ = False
    for names, rels in leftover_map():
        if _installed(names):
            continue
        for rel in rels:
            p = home() / rel
            if p.exists():
                ui.info(f"  {ui.pad(ui.human(fsutil.size_of([p])), 10)}~/{ui.pad(rel, 36)} ({names[0]} not installed)")
                any_ = True
    if not any_:
        ui.ok("none")

    ui.section(f"Electron/Chromium config dirs with no writes for {_c('stale_days')} days")
    any_ = False
    cutoff = time.time() - _c("stale_days") * 86400
    for d in electron_app_dirs():
        newest = 0.0
        for p in [d, *d.glob("*"), *d.glob("*/*")]:
            try:
                newest = max(newest, p.lstat().st_mtime)
            except OSError:
                continue
        if newest and newest < cutoff:
            ui.info(f"  {ui.pad(ui.human(fsutil.size_of([d])), 10)}{ui.pad(fsutil.tilde(d), 36)}"
                    f"last write {time.strftime('%Y-%m-%d', time.localtime(newest))}")
            any_ = True
    if not any_:
        ui.ok("none")

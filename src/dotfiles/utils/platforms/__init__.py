"""Platform detection, standard directories, external commands and process queries.
Windows-specific calls are branched at runtime."""
from __future__ import annotations

import locale
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Iterable, Sequence

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME = "windows" if IS_WINDOWS else ("linux" if IS_LINUX else sys.platform)


# ---------------------------------------------------------------- directories

def home() -> Path:
    return Path.home()


def _env_dir(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def xdg_cache() -> Path:
    return _env_dir("XDG_CACHE_HOME", home() / ".cache")


def xdg_config() -> Path:
    return _env_dir("XDG_CONFIG_HOME", home() / ".config")


def xdg_data() -> Path:
    return _env_dir("XDG_DATA_HOME", home() / ".local" / "share")


def xdg_state() -> Path:
    return _env_dir("XDG_STATE_HOME", home() / ".local" / "state")


def localappdata() -> Path:
    return _env_dir("LOCALAPPDATA", home() / "AppData" / "Local")


def appdata() -> Path:
    return _env_dir("APPDATA", home() / "AppData" / "Roaming")


def runtime_dir() -> Path:
    """Where transient plaintext goes: Linux uses the user-only $XDG_RUNTIME_DIR (tmpfs)."""
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value and Path(value).is_dir():
        return Path(value)
    import tempfile

    return Path(tempfile.gettempdir())


# ---------------------------------------------------------------- external commands

def which(name: str) -> str | None:
    """Resolve an executable's full path. On Windows this honors PATHEXT, so it finds shims like
    npm.cmd but never picks a .ps1."""
    return shutil.which(name)


def have(name: str) -> bool:
    return which(name) is not None


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd: Sequence[str], *, input: str | None = None, env: dict[str, str] | None = None,
        cwd: str | os.PathLike | None = None, timeout: float | None = None) -> Result:
    """Run a command capturing output (decoded as UTF-8, falling back to the local codec).
    Returns 127 when the command does not exist instead of raising."""
    exe = which(cmd[0])
    if exe is None:
        return Result(127, "", f"{cmd[0]}: command not found")
    full_env = None if env is None else {**os.environ, **env}
    try:
        proc = subprocess.run([exe, *cmd[1:]], input=input.encode() if input is not None else None,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env, cwd=cwd,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{cmd[0]}: timed out")
    except OSError as e:
        return Result(126, "", f"{cmd[0]}: {e}")
    return Result(proc.returncode, _decode(proc.stdout), _decode(proc.stderr))


def run_live(cmd: Sequence[str], *, env: dict[str, str] | None = None,
             cwd: str | os.PathLike | None = None) -> int:
    """Run a command inheriting the terminal (interactive commands, long output)."""
    exe = which(cmd[0])
    if exe is None:
        return 127
    sys.stdout.flush()
    full_env = None if env is None else {**os.environ, **env}
    try:
        return subprocess.call([exe, *cmd[1:]], env=full_env, cwd=cwd)
    except OSError:
        return 126


def query(cmd: Sequence[str]) -> str | None:
    """First non-empty line of a command's output; None on failure."""
    r = run(cmd, timeout=60)
    if not r.ok:
        return None
    for line in r.stdout.splitlines():
        if line.strip():
            return line.strip()
    return None


def version_of(tool: str) -> str | None:
    args = {"chezmoi": ["--version"], "go": ["version"]}.get(tool, ["--version"])
    line = query([tool, *args])
    return line[:80] if line else None


# ---------------------------------------------------------------- processes

@dataclass(frozen=True)
class Proc:
    pid: int
    name: str          # lowercase process name without .exe
    exe: str = ""      # Linux: target of /proc/<pid>/exe
    argv: tuple[str, ...] = ()


@cache
def processes() -> tuple[Proc, ...]:
    """Snapshot of the current user's processes (cached for one run)."""
    try:
        if IS_WINDOWS:
            from dotfiles.utils.platforms import windows as plat
        else:
            from dotfiles.utils.platforms import linux as plat
        return tuple(plat.list_processes())
    except OSError:
        return ()


def proc_running(names: Iterable[str]) -> bool:
    """Whether any process matches by name (case-insensitive, without .exe)."""
    wanted = {n.lower().removesuffix(".exe") for n in names}
    return any(p.name in wanted for p in processes())


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        return Path(f"/proc/{pid}").exists() if IS_LINUX else _posix_kill0(pid)
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _posix_kill0(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def file_locked(path: Path) -> bool:
    """Windows: whether another process has the file opened exclusively (Chromium's lockfile,
    Firefox's parent.lock). Always False on other platforms."""
    if not IS_WINDOWS or not path.is_file():
        return False
    import ctypes
    from ctypes import wintypes

    create = ctypes.windll.kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    GENERIC_READ_WRITE = 0x80000000 | 0x40000000
    handle = create(str(path), GENERIC_READ_WRITE, 0, None, 3, 0x80, None)  # OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL
    if handle == wintypes.HANDLE(-1).value:
        return True
    ctypes.windll.kernel32.CloseHandle(handle)
    return False


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0

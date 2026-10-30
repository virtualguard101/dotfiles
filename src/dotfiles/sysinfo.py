"""平台检测、标准目录、外部命令与进程查询。Windows 专属的调用都在运行时按平台分支。"""
from __future__ import annotations

import csv
import io
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


# ---------------------------------------------------------------- 目录

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
    """放临时明文的目录：Linux 用 tmpfs 上仅本人可访问的 $XDG_RUNTIME_DIR。"""
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value and Path(value).is_dir():
        return Path(value)
    import tempfile

    return Path(tempfile.gettempdir())


# ---------------------------------------------------------------- 外部命令

def which(name: str) -> str | None:
    """解析可执行文件的完整路径。Windows 上依赖 PATHEXT，因此能找到 npm.cmd 一类 shim，而不会选中 .ps1。"""
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
    """运行命令并捕获输出（按 UTF-8 解码，失败时退回本地编码）。命令不存在时返回 127，不抛异常。"""
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
    """运行命令并直接继承终端（交互式命令、长时间输出的命令）。"""
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
    """取命令输出的第一行非空内容；失败返回 None。"""
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


# ---------------------------------------------------------------- 进程

@dataclass(frozen=True)
class Proc:
    pid: int
    name: str          # 不含 .exe 的小写进程名
    exe: str = ""      # Linux：/proc/<pid>/exe 的目标
    argv: tuple[str, ...] = ()


def _linux_processes() -> list[Proc]:
    uid = os.getuid()
    out = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            base = f"/proc/{entry.name}"
            with open(f"{base}/comm", encoding="utf-8", errors="replace") as fh:
                name = fh.read().strip().lower()
            try:
                exe = os.readlink(f"{base}/exe")
            except OSError:
                exe = ""
            with open(f"{base}/cmdline", "rb") as fh:
                argv = tuple(_decode(a) for a in fh.read().split(b"\0") if a)
            out.append(Proc(int(entry.name), name, exe, argv))
        except OSError:
            continue
    return out


def _windows_processes() -> list[Proc]:
    r = run(["tasklist", "/fo", "csv", "/nh"], timeout=60)
    out = []
    for row in csv.reader(io.StringIO(r.stdout)):
        if len(row) >= 2 and row[1].isdigit():
            name = row[0].lower()
            out.append(Proc(int(row[1]), name[:-4] if name.endswith(".exe") else name))
    return out


@cache
def processes() -> tuple[Proc, ...]:
    """当前用户的进程快照（一次运行内缓存）。"""
    try:
        return tuple(_windows_processes() if IS_WINDOWS else _linux_processes())
    except OSError:
        return ()


def proc_running(names: Iterable[str]) -> bool:
    """按进程名（不区分大小写，不含 .exe）判断是否在运行。"""
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
    """Windows：文件是否被其他进程独占打开（Chromium 的 lockfile、Firefox 的 parent.lock）。其他平台恒为 False。"""
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


def powershell(script: str, timeout: float = 120) -> Result:
    """Windows：以 -NoProfile 运行一段 PowerShell，输出强制 UTF-8。"""
    exe = "pwsh" if have("pwsh") else "powershell"
    prefix = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
    return run([exe, "-NoProfile", "-NonInteractive", "-Command", prefix + script], timeout=timeout)


# ---------------------------------------------------------------- 系统信息

@cache
def os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    key, sep, value = line.strip().partition("=")
                    if sep:
                        data[key] = value.strip().strip('"')
            break
        except OSError:
            continue
    return data


def is_arch_like() -> bool:
    rel = os_release()
    return rel.get("ID") == "arch" or "arch" in rel.get("ID_LIKE", "").split()

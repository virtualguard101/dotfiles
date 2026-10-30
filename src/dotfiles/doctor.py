"""Environment detection (read-only): system, package managers, toolchain, network, chezmoi
encryption config, repo and hooks, packages. Each item is ok / warn / fail; fail means a required
condition is missing and the exit code is 1."""
from __future__ import annotations

import json
import os
import platform as pyplatform
import re
import socket
import stat
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from dotfiles import chez, sysinfo, ui
from dotfiles.sysinfo import IS_LINUX, IS_WINDOWS, have, home

REQUIRED_TOOLS = ("git", "chezmoi", "uv")
RECOMMENDED_TOOLS = ("gh", "pre-commit", "ssh-keygen", "just") + (("pwsh",) if IS_WINDOWS else ("sudo", "zsh"))
GPU_VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}


@dataclass
class Item:
    section: str
    name: str
    level: str      # ok | warn | fail | info
    detail: str = ""
    hint: str = ""


class Report:
    def __init__(self):
        self.items: list[Item] = []

    def add(self, section: str, name: str, level: str, detail: str = "", hint: str = "") -> None:
        self.items.append(Item(section, name, level, detail, hint))


# ---------------------------------------------------------------- system

def _gpus() -> list[str]:
    if IS_WINDOWS:
        r = sysinfo.powershell("Get-CimInstance Win32_VideoController | ForEach-Object Name", timeout=30)
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    out = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        try:
            vendor = (card / "device" / "vendor").read_text().strip()
            driver = Path(os.readlink(card / "device" / "driver")).name
        except OSError:
            continue
        out.append(f"{GPU_VENDORS.get(vendor, vendor)} (driver {driver})")
    return out


def check_system(rep: Report) -> None:
    s = "system"
    if IS_LINUX:
        rel = sysinfo.os_release()
        rep.add(s, "distribution", "ok" if sysinfo.is_arch_like() else "warn",
                f"{rel.get('PRETTY_NAME', 'unknown')}, kernel {pyplatform.release()}",
                "" if sysinfo.is_arch_like() else "package restore supports Arch-like distros only (pacman)")
        session = os.environ.get("XDG_SESSION_TYPE", "unknown")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        rep.add(s, "desktop session", "info", f"{session} {desktop}".strip())
        rep.add(s, "login shell", "info", os.environ.get("SHELL", "unknown"))
    elif IS_WINDOWS:
        rep.add(s, "Windows", "ok", f"{pyplatform.version()} ({pyplatform.machine()})")
        ps = sysinfo.query(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
        rep.add(s, "PowerShell 7", "ok" if ps else "warn", ps or "not installed (only Windows PowerShell 5.1)",
                "" if ps else "winget install Microsoft.PowerShell")
    rep.add(s, "architecture", "info", pyplatform.machine())
    gpus = _gpus()
    rep.add(s, "GPU", "info", "; ".join(gpus) or "none detected")
    rep.add(s, "Python", "info", f"{sys.version.split()[0]} ({sys.executable})")


# ---------------------------------------------------------------- toolchain

def check_tools(rep: Report) -> None:
    s = "tools"
    managers = ("winget", "scoop", "choco") if IS_WINDOWS else ("pacman", "yay", "paru", "nix", "flatpak")
    found = [m for m in managers if have(m)]
    rep.add("package managers", "available", "ok" if found else "fail", ", ".join(found) or "none",
            "" if found else ("install App Installer (winget)" if IS_WINDOWS else "pacman is required"))
    if IS_WINDOWS and not have("scoop"):
        rep.add("package managers", "scoop", "warn", "not installed",
                "see https://scoop.sh; the restore packages step needs it")
    for t in REQUIRED_TOOLS:
        v = sysinfo.version_of(t)
        rep.add(s, t, "ok" if v else "fail", v or "not installed", "" if v else "run bootstrap to install")
    for t in RECOMMENDED_TOOLS:
        path = sysinfo.which(t)
        rep.add(s, t, "ok" if path else "warn", path or "not installed",
                "" if path else {"pre-commit": "uv tool install pre-commit"}.get(t, ""))
    if not have("age"):
        rep.add(s, "age", "info", "not installed (chezmoi bundles age, so encryption still works)")


# ---------------------------------------------------------------- network

def _proxy_reachable(value: str) -> bool:
    m = re.match(r"^(?:[a-z0-9+]+://)?(?:[^@/]+@)?([^:/]+):(\d+)", value.strip())
    if not m:
        return False
    try:
        with socket.create_connection((m.group(1), int(m.group(2))), timeout=2):
            return True
    except OSError:
        return False


def git_proxy() -> str:
    return sysinfo.query(["git", "config", "--global", "--get", "http.proxy"]) or ""


def check_network(rep: Report) -> None:
    s = "network"
    proxy = git_proxy()
    if proxy:
        up = _proxy_reachable(proxy)
        rep.add(s, "git http.proxy", "ok" if up else "warn", f"{proxy} ({'reachable' if up else 'unreachable'})",
                "" if up else "the proxy app is not running; restore fetches externals directly as a fallback")
    else:
        rep.add(s, "git http.proxy", "info", "not set")
    env = [k for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY") if os.environ.get(k)]
    rep.add(s, "proxy env vars", "info", ", ".join(env) or "not set")


# ---------------------------------------------------------------- chezmoi

def expected_config() -> dict | None:
    r = chez.cz("execute-template", "--init", "--file", str(chez.source_state_dir() / ".chezmoi.toml.tmpl"))
    if not r.ok:
        return None
    try:
        return tomllib.loads(r.stdout)
    except tomllib.TOMLDecodeError:
        return None


def current_config() -> dict | None:
    try:
        return tomllib.loads(chez.config_file().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def default_source_dir() -> Path:
    return sysinfo.xdg_data() / "chezmoi" if not IS_WINDOWS else home() / ".local" / "share" / "chezmoi"


def check_chezmoi(rep: Report) -> None:
    s = "chezmoi"
    if not have("chezmoi"):
        rep.add(s, "chezmoi", "fail", "not installed", "run bootstrap")
        return
    configured = sysinfo.query(["chezmoi", "source-path"]) or ""
    want = str(chez.source_state_dir())
    same = os.path.normcase(configured) == os.path.normcase(want)
    rep.add(s, "source dir", "ok" if same else "fail", configured or "unknown",
            "" if same else f"the repo should be at {default_source_dir()} (bootstrap clones it there)")
    cur, exp = current_config(), expected_config()
    if cur is None:
        rep.add(s, "config file", "fail", f"{chez.config_file()} does not exist",
                "dotfiles restore --apply --only config")
    else:
        rep.add(s, "config file", "ok" if cur == exp else "warn", str(chez.config_file()),
                "" if cur == exp else "differs from the template output: dotfiles restore --apply --only config")
    cfg = cur or {}
    rep.add(s, "encryption", "ok" if cfg.get("encryption") == "age" else "fail", cfg.get("encryption", "not set"))
    identity = Path((cfg.get("age") or {}).get("identity") or (exp or {}).get("age", {}).get("identity", ""))
    if not identity.name:
        rep.add(s, "age private key", "fail", "not configured", "dotfiles restore --apply --only key")
        return
    if not identity.is_file():
        rep.add(s, "age private key", "fail", f"{identity} does not exist", "dotfiles restore --apply --only key")
        return
    recipient = chez.recipient_of(identity)
    match = recipient in chez.template_recipients()
    rep.add(s, "age private key", "ok" if match else "fail",
            f"{identity} ({'matches the repo public key' if match else 'does not match the repo public key'})",
            "" if match else "wrong key: use the private key matching the repo's recipient")
    if not IS_WINDOWS:
        mode = stat.S_IMODE(identity.stat().st_mode)
        rep.add(s, "key permissions", "ok" if not mode & 0o077 else "warn", f"{mode:o}",
                "" if not mode & 0o077 else "dotfiles audit --apply")
    if same and cur is not None:
        r = chez.cz("status", "--exclude", "externals")
        if r.ok:
            pending = [line for line in r.stdout.splitlines() if line[1:2] in "AMDR" and line[1:2] != " "]
            rep.add(s, "pending changes", "ok" if not pending else "warn", f"{len(pending)} items",
                    "" if not pending else "see chezmoi diff; deploy with dotfiles restore --apply --only dotfiles")
        else:
            rep.add(s, "pending changes", "fail", r.stderr.strip()[:120], "decryption failed: check the key and config")


# ---------------------------------------------------------------- repo

def check_repo(rep: Report) -> None:
    s = "repo"
    g = chez.git
    branch = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    rep.add(s, "branch", "info", branch)
    counts = g("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if counts.ok:
        ahead, behind = counts.stdout.split()
        rep.add(s, "vs upstream", "ok" if behind == "0" else "warn", f"ahead {ahead}, behind {behind}",
                "" if behind == "0" else "git pull --ff-only")
    dirty = [line for line in g("status", "--porcelain").stdout.splitlines() if line]
    rep.add(s, "uncommitted changes", "ok" if not dirty else "info", f"{len(dirty)} items")
    hooks = Path(g("rev-parse", "--git-path", "hooks").stdout.strip() or ".git/hooks")
    if not hooks.is_absolute():
        hooks = chez.repo() / hooks
    installed = all((hooks / h).is_file() and "pre-commit" in (hooks / h).read_text(errors="ignore")
                    for h in ("pre-commit", "pre-push"))
    rep.add(s, "commit hooks", "ok" if installed else "warn", "installed" if installed else "not installed",
            "" if installed else "dotfiles restore --apply --only hooks")
    origin = g("remote", "get-url", "origin").stdout.strip()
    push = g("remote", "get-url", "--push", "origin").stdout.strip()
    rep.add(s, "remote", "info", origin if origin == push else f"{origin} (push: {push})")


# ---------------------------------------------------------------- packages

def missing_arch_packages() -> list[str]:
    path = chez.packages_dir() / "archpkg.txt"
    if not path.is_file() or not have("pacman"):
        return []
    installed = set(sysinfo.run(["pacman", "-Qq"]).stdout.split())
    return [p for p in path.read_text(encoding="utf-8").split() if p not in installed]


def check_packages(rep: Report) -> None:
    s = "packages"
    if IS_LINUX:
        missing = missing_arch_packages()
        rep.add(s, "archpkg.txt", "ok" if not missing else "warn", f"{len(missing)} missing",
                "" if not missing else "dotfiles restore --apply --only packages")
    elif IS_WINDOWS:
        f = chez.packages_dir() / "scoopfile.json"
        if f.is_file() and have("scoop"):
            from dotfiles.clean.windows import scoop_dir
            apps = json.loads(f.read_text(encoding="utf-8")).get("apps", [])
            missing = [a["Name"] for a in apps if not (scoop_dir() / "apps" / a["Name"]).is_dir()]
            rep.add(s, "scoopfile.json", "ok" if not missing else "warn", f"{len(missing)} missing",
                    "" if not missing else "dotfiles restore --apply --only packages")
        unavailable = chez.packages_dir() / "winget-unavailable.txt"
        if unavailable.is_file():
            n = sum(1 for line in unavailable.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
            rep.add(s, "manual installs", "info", f"{n} programs (see packages/winget-unavailable.txt)")


# ---------------------------------------------------------------- output

def collect() -> Report:
    rep = Report()
    for check in (check_system, check_tools, check_network, check_chezmoi, check_repo, check_packages):
        try:
            check(rep)
        except ui.Abort as e:
            rep.add(check.__name__.removeprefix("check_"), "detection failed", "fail", str(e))
        except Exception as e:   # one failing check must not affect the others
            rep.add(check.__name__.removeprefix("check_"), "detection error", "warn", f"{type(e).__name__}: {e}")
    return rep


def run(as_json: bool) -> int:
    rep = collect()
    failed = any(i.level == "fail" for i in rep.items)
    if as_json:
        print(json.dumps({"ok": not failed, "items": [asdict(i) for i in rep.items]}, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    section = None
    for i in rep.items:
        if i.section != section:
            section = i.section
            ui.section(section)
        ui.status_line(i.level, f"{ui.pad(i.name, 16)}{i.detail}")
    issues = [i for i in rep.items if i.hint and i.level in ("fail", "warn")]
    ui.section("Summary")
    if not issues:
        ui.ok("environment complete, nothing to address")
    for i in issues:
        ui.status_line(i.level, f"{i.section}/{i.name}: {i.hint}", indent=0)
    return 1 if failed else 0

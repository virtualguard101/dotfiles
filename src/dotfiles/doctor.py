"""环境检测（只读）：系统、包管理器、工具链、网络、chezmoi 加密配置、仓库与 hooks、软件包。
每项给出 ok / warn / fail；fail 表示缺少必需条件，退出码为 1。"""
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


# ---------------------------------------------------------------- 系统

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
        out.append(f"{GPU_VENDORS.get(vendor, vendor)}（驱动 {driver}）")
    return out


def check_system(rep: Report) -> None:
    s = "系统"
    if IS_LINUX:
        rel = sysinfo.os_release()
        rep.add(s, "发行版", "ok" if sysinfo.is_arch_like() else "warn",
                f"{rel.get('PRETTY_NAME', '未知')}，内核 {pyplatform.release()}",
                "" if sysinfo.is_arch_like() else "软件包恢复只支持 Arch 系（pacman）")
        session = os.environ.get("XDG_SESSION_TYPE", "未知")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        rep.add(s, "桌面会话", "info", f"{session} {desktop}".strip())
        rep.add(s, "登录 shell", "info", os.environ.get("SHELL", "未知"))
    elif IS_WINDOWS:
        rep.add(s, "Windows", "ok", f"{pyplatform.version()}（{pyplatform.machine()}）")
        ps = sysinfo.query(["pwsh", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
        rep.add(s, "PowerShell 7", "ok" if ps else "warn", ps or "未安装（只有 Windows PowerShell 5.1）",
                "" if ps else "winget install Microsoft.PowerShell")
    rep.add(s, "架构", "info", pyplatform.machine())
    gpus = _gpus()
    rep.add(s, "GPU", "info", "；".join(gpus) or "未检测到")
    rep.add(s, "Python", "info", f"{sys.version.split()[0]}（{sys.executable}）")


# ---------------------------------------------------------------- 工具链

def check_tools(rep: Report) -> None:
    s = "工具"
    managers = ("winget", "scoop", "choco") if IS_WINDOWS else ("pacman", "yay", "paru", "nix", "flatpak")
    found = [m for m in managers if have(m)]
    rep.add("包管理器", "可用", "ok" if found else "fail", ", ".join(found) or "无",
            "" if found else ("安装 App Installer（winget）" if IS_WINDOWS else "需要 pacman"))
    if IS_WINDOWS and not have("scoop"):
        rep.add("包管理器", "scoop", "warn", "未安装", "见 https://scoop.sh；restore 的 packages 步骤需要它")
    for t in REQUIRED_TOOLS:
        v = sysinfo.version_of(t)
        rep.add(s, t, "ok" if v else "fail", v or "未安装", "" if v else "运行 bootstrap 安装")
    for t in RECOMMENDED_TOOLS:
        path = sysinfo.which(t)
        rep.add(s, t, "ok" if path else "warn", path or "未安装",
                "" if path else {"pre-commit": "uv tool install pre-commit"}.get(t, ""))
    if not have("age"):
        rep.add(s, "age", "info", "未安装（chezmoi 内置 age，不影响加解密）")


# ---------------------------------------------------------------- 网络

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
    s = "网络"
    proxy = git_proxy()
    if proxy:
        up = _proxy_reachable(proxy)
        rep.add(s, "git http.proxy", "ok" if up else "warn", f"{proxy}（{'可连接' if up else '无法连接'}）",
                "" if up else "代理软件未运行；restore 拉取 externals 时会临时改为直连")
    else:
        rep.add(s, "git http.proxy", "info", "未设置")
    env = [k for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY") if os.environ.get(k)]
    rep.add(s, "代理环境变量", "info", ", ".join(env) or "未设置")


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
        rep.add(s, "chezmoi", "fail", "未安装", "运行 bootstrap")
        return
    configured = sysinfo.query(["chezmoi", "source-path"]) or ""
    want = str(chez.source_state_dir())
    same = os.path.normcase(configured) == os.path.normcase(want)
    rep.add(s, "源目录", "ok" if same else "fail", configured or "未知",
            "" if same else f"仓库应位于 {default_source_dir()}（bootstrap 会克隆到这里）")
    cur, exp = current_config(), expected_config()
    if cur is None:
        rep.add(s, "配置文件", "fail", f"{chez.config_file()} 不存在", "dotfiles restore --apply --only config")
    else:
        rep.add(s, "配置文件", "ok" if cur == exp else "warn", str(chez.config_file()),
                "" if cur == exp else "与模板生成的结果不同：dotfiles restore --apply --only config")
    cfg = cur or {}
    rep.add(s, "加密", "ok" if cfg.get("encryption") == "age" else "fail", cfg.get("encryption", "未设置"))
    identity = Path((cfg.get("age") or {}).get("identity") or (exp or {}).get("age", {}).get("identity", ""))
    if not identity.name:
        rep.add(s, "age 私钥", "fail", "未配置", "dotfiles restore --apply --only key")
        return
    if not identity.is_file():
        rep.add(s, "age 私钥", "fail", f"{identity} 不存在", "dotfiles restore --apply --only key")
        return
    recipient = chez.recipient_of(identity)
    match = recipient in chez.template_recipients()
    rep.add(s, "age 私钥", "ok" if match else "fail", f"{identity}（{'与仓库公钥匹配' if match else '与仓库公钥不匹配'}）",
            "" if match else "私钥不对：换成与仓库 recipient 对应的私钥")
    if not IS_WINDOWS:
        mode = stat.S_IMODE(identity.stat().st_mode)
        rep.add(s, "私钥权限", "ok" if not mode & 0o077 else "warn", f"{mode:o}",
                "" if not mode & 0o077 else "dotfiles audit --apply")
    if same and cur is not None:
        r = chez.cz("status", "--exclude", "externals")
        if r.ok:
            pending = [line for line in r.stdout.splitlines() if line[1:2] in "AMDR" and line[1:2] != " "]
            rep.add(s, "待部署变更", "ok" if not pending else "warn", f"{len(pending)} 项",
                    "" if not pending else "chezmoi diff 查看；dotfiles restore --apply --only dotfiles 部署")
        else:
            rep.add(s, "待部署变更", "fail", r.stderr.strip()[:120], "解密失败：检查私钥与配置")


# ---------------------------------------------------------------- 仓库

def check_repo(rep: Report) -> None:
    s = "仓库"
    g = chez.git
    branch = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    rep.add(s, "分支", "info", branch)
    counts = g("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if counts.ok:
        ahead, behind = counts.stdout.split()
        rep.add(s, "与上游", "ok" if behind == "0" else "warn", f"领先 {ahead}，落后 {behind}",
                "" if behind == "0" else "git pull --ff-only")
    dirty = [line for line in g("status", "--porcelain").stdout.splitlines() if line]
    rep.add(s, "未提交改动", "ok" if not dirty else "info", f"{len(dirty)} 项")
    hooks = Path(g("rev-parse", "--git-path", "hooks").stdout.strip() or ".git/hooks")
    if not hooks.is_absolute():
        hooks = chez.repo() / hooks
    installed = all((hooks / h).is_file() and "pre-commit" in (hooks / h).read_text(errors="ignore")
                    for h in ("pre-commit", "pre-push"))
    rep.add(s, "提交检查 hooks", "ok" if installed else "warn", "已安装" if installed else "未安装",
            "" if installed else "dotfiles restore --apply --only hooks")
    origin = g("remote", "get-url", "origin").stdout.strip()
    push = g("remote", "get-url", "--push", "origin").stdout.strip()
    rep.add(s, "远程", "info", origin if origin == push else f"{origin}（推送 {push}）")


# ---------------------------------------------------------------- 软件包

def missing_arch_packages() -> list[str]:
    path = chez.packages_dir() / "archpkg.txt"
    if not path.is_file() or not have("pacman"):
        return []
    installed = set(sysinfo.run(["pacman", "-Qq"]).stdout.split())
    return [p for p in path.read_text(encoding="utf-8").split() if p not in installed]


def check_packages(rep: Report) -> None:
    s = "软件包"
    if IS_LINUX:
        missing = missing_arch_packages()
        rep.add(s, "archpkg.txt", "ok" if not missing else "warn", f"缺少 {len(missing)} 个",
                "" if not missing else "dotfiles restore --apply --only packages")
    elif IS_WINDOWS:
        f = chez.packages_dir() / "scoopfile.json"
        if f.is_file() and have("scoop"):
            from dotfiles.clean.windows import scoop_dir
            apps = json.loads(f.read_text(encoding="utf-8")).get("apps", [])
            missing = [a["Name"] for a in apps if not (scoop_dir() / "apps" / a["Name"]).is_dir()]
            rep.add(s, "scoopfile.json", "ok" if not missing else "warn", f"缺少 {len(missing)} 个",
                    "" if not missing else "dotfiles restore --apply --only packages")
        unavailable = chez.packages_dir() / "winget-unavailable.txt"
        if unavailable.is_file():
            n = sum(1 for line in unavailable.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
            rep.add(s, "需手动安装", "info", f"{n} 个（见 packages/winget-unavailable.txt）")


# ---------------------------------------------------------------- 输出

def collect() -> Report:
    rep = Report()
    for check in (check_system, check_tools, check_network, check_chezmoi, check_repo, check_packages):
        try:
            check(rep)
        except ui.Abort as e:
            rep.add(check.__name__.removeprefix("check_"), "检测失败", "fail", str(e))
        except Exception as e:   # 单项检测出错不影响其他项
            rep.add(check.__name__.removeprefix("check_"), "检测出错", "warn", f"{type(e).__name__}: {e}")
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
    ui.section("结论")
    if not issues:
        ui.ok("环境完整，无待处理项")
    for i in issues:
        ui.status_line(i.level, f"{i.section}/{i.name}: {i.hint}", indent=0)
    return 1 if failed else 0

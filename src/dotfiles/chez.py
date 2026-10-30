"""仓库与 chezmoi 的封装：源目录定位、配置读取、托管清单、age 身份与平台归属。"""
from __future__ import annotations

import json
import os
import re
import tomllib
from functools import cache
from pathlib import Path

from dotfiles import sysinfo, ui

REPO = Path(__file__).resolve().parents[2]   # src/dotfiles/chez.py → 仓库根目录
PLATFORMS = ("linux", "windows")
DEFAULT_IDENTITY = Path(".config") / "chezmoi" / "key.txt"   # 相对 ~，新机器上私钥的默认位置


def repo() -> Path:
    if not (REPO / ".chezmoiroot").is_file():
        raise ui.Abort(f"{REPO} 不是 dotfiles 仓库（缺少 .chezmoiroot）", 2)
    return REPO


def source_state_dir() -> Path:
    return repo() / (REPO / ".chezmoiroot").read_text(encoding="utf-8").strip()


def system_source_dir() -> Path:
    return repo() / "system"


def packages_dir() -> Path:
    return repo() / "packages"


# ---------------------------------------------------------------- chezmoi 调用

def require_chezmoi() -> None:
    if not sysinfo.have("chezmoi"):
        raise ui.Abort("未找到 chezmoi（先运行 bootstrap）", 2)


def base_args(os_override: str | None = None) -> list[str]:
    args = ["chezmoi", "--source", str(repo())]
    if os_override:
        args += ["--override-data", json.dumps({"chezmoi": {"os": os_override}})]
    return args


def cz(*args: str, os_override: str | None = None, **kw) -> sysinfo.Result:
    return sysinfo.run([*base_args(os_override), *args], **kw)


def cz_live(*args: str, **kw) -> int:
    return sysinfo.run_live([*base_args(), *args], **kw)


def cz_lines(*args: str, os_override: str | None = None) -> list[str]:
    r = cz(*args, os_override=os_override)
    if not r.ok:
        raise ui.Abort(f"chezmoi {' '.join(args)} 失败: {r.stderr.strip()[:300]}")
    return [line for line in r.stdout.splitlines() if line.strip()]


@cache
def dump_config() -> dict:
    r = cz("dump-config", "--format", "json")
    if not r.ok:
        raise ui.Abort(f"chezmoi dump-config 失败: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def config_file() -> Path:
    return sysinfo.xdg_config() / "chezmoi" / "chezmoi.toml"


def age_identities(config: dict | None = None) -> list[Path]:
    age = (config or dump_config()).get("age") or {}
    ids = [age.get("identity")] + list(age.get("identities") or [])
    return [Path(i) for i in ids if i]


def age_recipients(config: dict | None = None) -> list[str]:
    age = (config or dump_config()).get("age") or {}
    return [r for r in [age.get("recipient")] + list(age.get("recipients") or []) if r]


def template_recipients() -> list[str]:
    """仓库 .chezmoi.toml.tmpl 中写死的接收者公钥：新机器上用它校验私钥。"""
    text = (source_state_dir() / ".chezmoi.toml.tmpl").read_text(encoding="utf-8")
    return re.findall(r'recipients?\s*=\s*"(age1[0-9a-z]+)"', text)


def recipient_of(identity: Path) -> str | None:
    """用 chezmoi 内置 age 从私钥推导公钥（不需要单独安装 age）。"""
    r = sysinfo.run(["chezmoi", "age-keygen", "-y", str(identity)], timeout=30)
    if not r.ok:
        return None
    m = re.search(r"age1[0-9a-z]+", r.stdout)
    return m.group(0) if m else None


def managed(include: str = "files", exclude: str = "externals", os_override: str | None = None,
            absolute: bool = False) -> list[str]:
    args = ["managed", "-i", include]
    if exclude:
        args += ["-x", exclude]
    if absolute:
        args += ["-p", "absolute"]
    return cz_lines(*args, os_override=os_override)


def is_managed(target: Path) -> bool:
    return cz("source-path", str(target)).ok


# ---------------------------------------------------------------- 平台归属

def platforms_file() -> Path:
    return source_state_dir() / ".chezmoidata" / "platforms.toml"


def load_platforms() -> dict[str, list[str]]:
    data = tomllib.loads(platforms_file().read_text(encoding="utf-8")).get("platforms", {})
    return {k: list(v) for k, v in data.items()}


def covering(target: str, entries: list[str]) -> str | None:
    """返回覆盖 target 的声明项（相同路径，或 target 位于其目录下）。"""
    for e in entries:
        if target == e or target.startswith(e.rstrip("/") + "/"):
            return e
    return None


def owner_of(target: str, platforms: dict[str, list[str]] | None = None) -> str | None:
    platforms = platforms or load_platforms()
    for name in ("shared", *PLATFORMS):
        if covering(target, platforms.get(name, [])):
            return name
    return None


def register_platform(target: str, platform: str, dry: bool) -> bool:
    """把新托管的目标登记到当前平台。只改写数组内容，保留文件头注释。"""
    platforms = load_platforms()
    if owner_of(target, platforms):
        return False
    platforms.setdefault(platform, []).append(target)
    if dry:
        return True
    text = platforms_file().read_text(encoding="utf-8")
    head = text[: text.index("[platforms]")]
    body = ["[platforms]"]
    for name in ("shared", *PLATFORMS, *sorted(set(platforms) - {"shared", *PLATFORMS})):
        if name not in platforms:
            continue
        body.append(f"{name} = [")
        body += [f"  {json.dumps(t, ensure_ascii=False)}," for t in sorted(set(platforms[name]))]
        body.append("]")
    with open(platforms_file(), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + "\n".join(body) + "\n")
    return True


def check_platforms() -> list[str]:
    """每个平台视角下的托管目标都必须在且只在一个归属列表中。返回问题描述。"""
    platforms = load_platforms()
    problems = []
    seen: dict[str, str] = {}
    for name, entries in platforms.items():
        for e in entries:
            if e in seen:
                problems.append(f"{e} 同时登记在 {seen[e]} 与 {name}")
            seen[e] = name
    for os_name in PLATFORMS:
        for target in managed(include="files,symlinks,externals", exclude="", os_override=os_name):
            owner = owner_of(target, platforms)
            if owner is None:
                problems.append(f"{target}（{os_name} 视角下被托管）未在 platforms.toml 中声明归属")
            elif owner not in ("shared", os_name):
                problems.append(f"{target} 归属 {owner}，却在 {os_name} 视角下被托管（检查 .chezmoiignore）")
    return problems


# ---------------------------------------------------------------- git

def git(*args: str) -> sysinfo.Result:
    return sysinfo.run(["git", "-C", str(repo()), *args])


def tracked(path: Path) -> bool:
    try:
        rel = os.path.relpath(path, repo())
    except ValueError:      # Windows：不同盘符
        return False
    if rel.startswith(".."):
        return False
    return git("ls-files", "--error-unmatch", "--", rel).ok

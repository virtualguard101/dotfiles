"""重装后恢复：在 bootstrap 装好 git / chezmoi / uv 并克隆仓库之后运行。

每一步先检测（只读，基于当前系统状态，不依赖记录文件），再按需执行；
中途失败后重跑即可从未完成的步骤继续。默认只显示计划，--apply 才执行。
"""
from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotfiles import chez, sysinfo, ui
from dotfiles.sysinfo import IS_LINUX, IS_WINDOWS, have, home

DONE, TODO, SKIP, BLOCKED = "done", "todo", "skip", "blocked"
LABEL = {DONE: "已完成", TODO: "待执行", SKIP: "跳过", BLOCKED: "阻塞"}
LEVEL = {DONE: "ok", TODO: "todo", SKIP: "skip", BLOCKED: "fail"}


@dataclass
class Options:
    apply: bool
    key_file: str | None
    assume_yes: bool


@dataclass
class Step:
    id: str
    title: str
    check: Callable[[Options], tuple[str, str]]     # (状态, 说明)
    run: Callable[[Options], None]
    confirm: str = ""                                # 非空时执行前需要确认
    platforms: tuple[str, ...] = ("linux", "windows")
    always: bool = False                             # 校验类步骤：检测即执行


class StepFailed(Exception):
    pass


# ---------------------------------------------------------------- age 私钥

def generated_config() -> dict | None:
    from dotfiles.doctor import expected_config
    return expected_config()


def target_identity() -> Path:
    """配置模板将选用的私钥路径（已有机器沿用旧位置，新机器为 ~/.config/chezmoi/key.txt）。"""
    cfg = generated_config() or {}
    value = (cfg.get("age") or {}).get("identity")
    return Path(value) if value else home() / chez.DEFAULT_IDENTITY


def check_key(o: Options) -> tuple[str, str]:
    path = target_identity()
    if not path.is_file():
        return TODO, f"{path} 不存在；需要提供离线保存的私钥"
    recipient = chez.recipient_of(path)
    if recipient not in chez.template_recipients():
        return BLOCKED, f"{path} 与仓库公钥不匹配（换成正确的私钥后重试）"
    return DONE, f"{path}（与仓库公钥匹配）"


_KEY_RX = re.compile(r"^AGE-SECRET-KEY-(PQ-)?1[0-9A-Z]+$")


def _read_key_material(o: Options) -> str:
    if o.key_file:
        return Path(o.key_file).expanduser().read_text(encoding="utf-8")
    if not os.isatty(0):
        raise StepFailed("非交互终端：用 --key-file 指定私钥文件")
    answer = input("私钥文件路径（例如 U 盘中的文件；留空则直接粘贴私钥）: ").strip().strip('"')
    if answer:
        return Path(answer).expanduser().read_text(encoding="utf-8")
    return getpass.getpass("粘贴以 AGE-SECRET-KEY- 开头的那一行（输入不回显）: ")


def write_private(path: Path, text: str) -> None:
    """以仅本人可读的权限写入：POSIX 600（目录 700），Windows 去掉继承、只允许本人/SYSTEM/Administrators。"""
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700)
    if IS_WINDOWS:
        with open(path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        from dotfiles.backup import restrict_acl
        if not restrict_acl(path, is_dir=False):
            ui.warn(f"未能收紧 {path} 的 ACL，请运行 dotfiles audit --apply")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def run_key(o: Options) -> None:
    path = target_identity()
    material = _read_key_material(o)
    lines = [line.strip() for line in material.splitlines() if line.strip()]
    secrets = [line for line in lines if _KEY_RX.match(line)]
    if not secrets:
        raise StepFailed("没有找到 AGE-SECRET-KEY- 开头的私钥行")
    comments = [line for line in lines if line.startswith("#")]
    write_private(path, "\n".join(comments + secrets) + "\n")
    recipient = chez.recipient_of(path)
    if recipient not in chez.template_recipients():
        path.unlink()
        raise StepFailed("私钥与仓库的 recipient 不匹配，已删除写入的文件")
    ui.ok(f"私钥已写入 {path}（与仓库公钥匹配）")


# ---------------------------------------------------------------- chezmoi 配置

def check_config(o: Options) -> tuple[str, str]:
    from dotfiles.doctor import current_config, default_source_dir
    configured = sysinfo.query(["chezmoi", "source-path"]) or ""
    if os.path.normcase(configured) != os.path.normcase(str(chez.source_state_dir())):
        return BLOCKED, f"chezmoi 源目录是 {configured or '未知'}；仓库应位于 {default_source_dir()}（bootstrap 会克隆到这里）"
    cur, exp = current_config(), generated_config()
    if exp is None:
        return BLOCKED, "无法渲染 .chezmoi.toml.tmpl"
    if cur == exp:
        return DONE, str(chez.config_file())
    return TODO, "配置不存在" if cur is None else "配置与模板生成的结果不同，将重新生成"


def run_config(o: Options) -> None:
    old = chez.config_file()
    if old.is_file():
        backup = old.with_name(f"chezmoi.toml.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(old, backup)
        ui.info(f"  原配置已备份到 {backup}")
    r = chez.cz("init", "--no-tty")
    if not r.ok:
        raise StepFailed(f"chezmoi init 失败: {r.stderr.strip()[:300]}")
    chez.dump_config.cache_clear()


# ---------------------------------------------------------------- 部署 dotfiles

def _status(*extra: str) -> list[tuple[str, str]]:
    r = chez.cz("status", *extra)
    if not r.ok:
        raise StepFailed(f"chezmoi status 失败（无法解密？）: {r.stderr.strip()[:300]}")
    return [(line[:2], line[3:]) for line in r.stdout.splitlines() if len(line) > 3]


def check_dotfiles(o: Options) -> tuple[str, str]:
    try:
        pending = [(c, p) for c, p in _status("--exclude", "externals") if c[1] != " "]
    except StepFailed as e:
        return BLOCKED, str(e)
    if not pending:
        return DONE, "目标状态与仓库一致"
    existing = sum(1 for c, p in pending if c[1] == "M")
    return TODO, f"{len(pending)} 项待部署（其中 {existing} 个已存在的文件会先备份再覆盖）"


def run_dotfiles(o: Options) -> None:
    dry = chez.cz("apply", "--dry-run", "--exclude", "externals", "--no-tty")
    if not dry.ok:   # 先完整预演：解密失败时一个文件都不写
        raise StepFailed(f"预演失败，未写入任何文件: {dry.stderr.strip()[:300]}")
    pending = [p for c, p in _status("--exclude", "externals") if c[1] == "M"]
    if pending:
        dest = sysinfo.xdg_state() / "dotfiles" / f"pre-apply-{datetime.now():%Y%m%d-%H%M%S}"
        dest.mkdir(parents=True, mode=0o700)
        for rel in pending:
            src = home() / rel
            if src.is_file() and not src.is_symlink():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)
        ui.info(f"  {len(pending)} 个将被覆盖的文件已备份到 {dest}")
    if chez.cz_live("apply", "--exclude", "externals", "--force", "--no-tty") != 0:
        raise StepFailed("chezmoi apply 失败")


# ---------------------------------------------------------------- 系统配置（Linux /etc）

def check_system(o: Options) -> tuple[str, str]:
    if not (chez.system_source_dir() / "etc").is_dir():
        return SKIP, "system/ 中还没有 /etc 备份（在 Linux 上运行一次 dotfiles backup 后生成）"
    return TODO, "对比 /etc 与备份；与硬件绑定的文件（fstab 等）只显示差异，不自动安装"


def run_system(o: Options) -> None:
    from dotfiles import system_etc
    system_etc.cmd_restore(apply=True, filters=[])


# ---------------------------------------------------------------- 软件包

def _arch_split(missing: list[str]) -> tuple[list[str], list[str]]:
    sync = set(sysinfo.run(["pacman", "-Slq"]).stdout.split())
    return [p for p in missing if p in sync], [p for p in missing if p not in sync]


def _winget_missing() -> list[str] | None:
    f = chez.packages_dir() / "winget.json"
    if not f.is_file() or not have("winget"):
        return None
    wanted = {p["PackageIdentifier"] for s in json.loads(f.read_text(encoding="utf-8")).get("Sources", [])
              for p in s.get("Packages", [])}
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "installed.json"
        sysinfo.run(["winget", "export", "-o", str(out), "--accept-source-agreements", "--disable-interactivity"], timeout=600)
        if not out.is_file():
            return None
        have_ids = {p["PackageIdentifier"] for s in json.loads(out.read_text(encoding="utf-8-sig")).get("Sources", [])
                    for p in s.get("Packages", [])}
    return sorted(wanted - have_ids)


def _scoop_missing() -> list[str]:
    f = chez.packages_dir() / "scoopfile.json"
    if not f.is_file():
        return []
    from dotfiles.clean.windows import scoop_dir
    return [a["Name"] for a in json.loads(f.read_text(encoding="utf-8")).get("apps", [])
            if not (scoop_dir() / "apps" / a["Name"]).is_dir()]


def check_packages(o: Options) -> tuple[str, str]:
    if IS_LINUX:
        if not sysinfo.is_arch_like() or not have("pacman"):
            return SKIP, "不是 Arch 系发行版"
        from dotfiles.doctor import missing_arch_packages
        missing = missing_arch_packages()
        if not missing:
            return DONE, "archpkg.txt 中的软件包均已安装"
        repo, foreign = _arch_split(missing)
        return TODO, f"缺少 {len(missing)} 个：官方/已配置仓库 {len(repo)} 个，AUR 或未知 {len(foreign)} 个"
    winget = _winget_missing()
    scoop = _scoop_missing()
    parts = []
    if winget:
        parts.append(f"winget 缺少 {len(winget)} 个")
    if scoop:
        parts.append(f"scoop 缺少 {len(scoop)} 个" + ("" if have("scoop") else "（scoop 未安装）"))
    if not parts:
        return DONE, "winget.json 与 scoopfile.json 中的软件包均已安装"
    if scoop and not have("scoop"):
        return BLOCKED, "；".join(parts) + "。先按 https://scoop.sh 安装 scoop（需要执行其官方安装脚本，本工具不代为执行）"
    return TODO, "；".join(parts)


def run_packages(o: Options) -> None:
    if IS_LINUX:
        from dotfiles.doctor import missing_arch_packages
        repo, foreign = _arch_split(missing_arch_packages())
        if repo:
            cmd = ["sudo", "pacman", "-S", "--needed", *(["--noconfirm"] if o.assume_yes else []), *repo]
            if sysinfo.run_live(cmd) != 0:
                raise StepFailed("pacman 安装失败（可重跑本步骤继续）")
        if foreign:
            helper = next((h for h in ("paru", "yay") if have(h)), None)
            if helper:
                ui.info(f"  通过 {helper} 安装 {len(foreign)} 个 AUR 软件包")
                if sysinfo.run_live([helper, "-S", "--needed", *(["--noconfirm"] if o.assume_yes else []), *foreign]) != 0:
                    ui.warn("部分 AUR 软件包安装失败")
            else:
                ui.warn(f"未安装 paru/yay，以下 {len(foreign)} 个软件包需要手动安装（可能来自 AUR 或 archlinuxcn）：")
                ui.info("  " + " ".join(foreign))
        return
    winget_json = chez.packages_dir() / "winget.json"
    if winget_json.is_file() and have("winget"):
        sysinfo.run_live(["winget", "import", "-i", str(winget_json), "--accept-package-agreements",
                          "--accept-source-agreements", "--ignore-unavailable", "--ignore-versions",
                          "--disable-interactivity"])
    scoop_json = chez.packages_dir() / "scoopfile.json"
    if scoop_json.is_file() and have("scoop"):
        sysinfo.run_live(["scoop", "import", str(scoop_json)])
    manual = chez.packages_dir() / "winget-unavailable.txt"
    if manual.is_file():
        names = [line for line in manual.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
        if names:
            ui.note(f"以下 {len(names)} 个程序不在 winget 源中，需要手动安装（packages/winget-unavailable.txt）")


# ---------------------------------------------------------------- externals

def proxy_override() -> dict[str, str]:
    """git 配置了代理但连不上时（新机器上代理软件还没装），临时改为直连。"""
    from dotfiles.doctor import _proxy_reachable, git_proxy
    proxy = git_proxy()
    if not proxy or _proxy_reachable(proxy):
        return {}
    ui.warn(f"git 代理 {proxy} 无法连接，本次改为直连")
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.proxy", "GIT_CONFIG_VALUE_0": ""}


def check_externals(o: Options) -> tuple[str, str]:
    try:
        externals = chez.managed(include="externals", exclude="")
    except ui.Abort as e:
        return BLOCKED, str(e)
    if not externals:
        return SKIP, "本平台没有 externals"
    missing = [e for e in externals if not (home() / e).exists()]
    if missing:
        return TODO, f"待拉取: {', '.join(missing)}"
    return DONE, f"{len(externals)} 个已就位（按 refreshPeriod 自动更新）"


def run_externals(o: Options) -> None:
    if chez.cz_live("apply", "--include", "externals", "--force", "--no-tty", env=proxy_override()) != 0:
        raise StepFailed("拉取 externals 失败（检查网络或代理后重跑）")


# ---------------------------------------------------------------- SSH 密钥

def _ssh_config_keys() -> tuple[list[Path], Path]:
    """~/.ssh/config 中引用的私钥，以及 github.com 使用的私钥（默认 id_ed25519）。"""
    ssh = home() / ".ssh"
    keys: list[Path] = [ssh / "id_ed25519"]
    github = ssh / "id_ed25519"
    cfg = ssh / "config"
    if cfg.is_file():
        hosts: list[str] = []
        for raw in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            line = re.sub(r"\s#.*$", "", raw).strip()     # 去掉行尾注释
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(\S+?)\s*(?:=|\s)\s*(?:"([^"]*)"|(\S+))', line)
            if not m:
                continue
            key, value = m.group(1).lower(), m.group(2) if m.group(2) is not None else m.group(3)
            if key == "host":
                hosts = value.split()
            elif key == "identityfile":
                p = Path(os.path.expanduser(value.replace("%d", str(home()))))
                if p not in keys:
                    keys.append(p)
                if "github.com" in hosts:
                    github = p
    return keys, github


def check_ssh(o: Options) -> tuple[str, str]:
    if not have("ssh-keygen"):
        return SKIP, "未安装 ssh-keygen"
    keys, _ = _ssh_config_keys()
    missing = [k for k in keys if not k.is_file()]
    if missing:
        return TODO, f"生成 {len(missing)} 把新密钥: {', '.join(k.name for k in missing)}（私钥不备份，每台机器重新生成）"
    return DONE, f"{len(keys)} 把密钥均已存在"


def run_ssh(o: Options) -> None:
    keys, github = _ssh_config_keys()
    host = socket.gethostname()
    created = []
    for k in keys:
        if k.is_file():
            continue
        k.parent.mkdir(parents=True, exist_ok=True)
        if not IS_WINDOWS:
            os.chmod(k.parent, 0o700)
        ui.info(f"  生成 {k}（会提示设置口令，建议设置）")
        if sysinfo.run_live(["ssh-keygen", "-t", "ed25519", "-f", str(k), "-C", f"{getpass.getuser()}@{host} {time.strftime('%Y-%m-%d')}"]) != 0:
            raise StepFailed(f"ssh-keygen 失败: {k}")
        created.append(k)
    if IS_WINDOWS and created:
        from dotfiles.backup import restrict_acl
        for k in created:
            restrict_acl(k, is_dir=False)
    if github in created and have("gh"):
        if sysinfo.run(["gh", "auth", "status"]).ok:
            if ui.confirm(f"把 {github.name}.pub 登记到 GitHub？", o.assume_yes):
                sysinfo.run_live(["gh", "ssh-key", "add", f"{github}.pub", "--title", f"{host} {time.strftime('%Y-%m-%d')}"])
        else:
            ui.note(f"先运行 gh auth login，再执行: gh ssh-key add {github}.pub --title {host}")
    others = [k for k in created if k != github]
    if others:
        ui.note("以下新公钥需要分别登记到对应服务器（ssh-copy-id 或手动追加到 authorized_keys）：")
        for k in others:
            ui.info(f"  {k}.pub")


# ---------------------------------------------------------------- 登录 shell（Linux）

def _login_shell() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_shell


def check_shell(o: Options) -> tuple[str, str]:
    zsh = sysinfo.which("zsh")
    if not zsh:
        return SKIP, "zsh 未安装（packages 步骤之后再检查）"
    current = _login_shell()
    if Path(current).name == "zsh":
        return DONE, current
    return TODO, f"当前 {current}，改为 {zsh}"


def run_shell(o: Options) -> None:
    if sysinfo.run_live(["chsh", "-s", sysinfo.which("zsh") or "/usr/bin/zsh"]) != 0:
        raise StepFailed("chsh 失败")


# ---------------------------------------------------------------- 提交检查与推送地址

_GITHUB_HTTPS = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def _ssh_pushurl(repo_dir: Path) -> tuple[str, str] | None:
    """origin 为 GitHub HTTPS 时的 (当前推送地址, 期望的 SSH 推送地址)。"""
    r = sysinfo.run(["git", "-C", str(repo_dir), "remote", "get-url", "origin"])
    m = _GITHUB_HTTPS.match(r.stdout.strip())
    if not m:
        return None
    push = sysinfo.run(["git", "-C", str(repo_dir), "remote", "get-url", "--push", "origin"]).stdout.strip()
    return push, f"git@github.com:{m.group(1)}/{m.group(2)}.git"


def _hook_dirs() -> list[Path]:
    dirs = [chez.repo()]
    for e in chez.managed(include="externals", exclude=""):
        if (home() / e / ".git").exists():
            dirs.append(home() / e)
    return dirs


def check_hooks(o: Options) -> tuple[str, str]:
    from dotfiles.doctor import Report, check_repo
    rep = Report()
    check_repo(rep)
    todo = []
    if any(i.name == "提交检查 hooks" and i.level != "ok" for i in rep.items):
        todo.append("安装 pre-commit / pre-push hook")
    for d in _hook_dirs():
        urls = _ssh_pushurl(d)
        if urls and urls[0] != urls[1]:
            todo.append(f"{d.name} 推送改用 SSH")
    return (TODO, "；".join(todo)) if todo else (DONE, "hooks 已安装，推送地址为 SSH")


def run_hooks(o: Options) -> None:
    if not have("pre-commit"):
        if sysinfo.run_live(["uv", "tool", "install", "pre-commit"]) != 0:
            raise StepFailed("uv tool install pre-commit 失败")
    if not have("pre-commit"):
        ui.warn("pre-commit 已安装但不在 PATH 中：运行 uv tool update-shell 后重开终端，再重跑本步骤")
    elif sysinfo.run_live(["pre-commit", "install"], cwd=chez.repo()) != 0:
        raise StepFailed("pre-commit install 失败")
    for d in _hook_dirs():
        urls = _ssh_pushurl(d)
        if urls and urls[0] != urls[1]:
            sysinfo.run(["git", "-C", str(d), "remote", "set-url", "--push", "origin", urls[1]])
            ui.info(f"  {d}: 拉取仍用 HTTPS，推送改用 {urls[1]}")


# ---------------------------------------------------------------- 校验

def check_verify(o: Options) -> tuple[str, str]:
    return TODO, "chezmoi verify + 安全检查 + 权限审计"


def run_verify(o: Options) -> None:
    from dotfiles import backup
    ok = chez.cz("verify", "--exclude", "externals").ok
    (ui.ok if ok else ui.warn)("chezmoi verify: " + ("目标状态与仓库一致" if ok else "仍有差异，见 chezmoi diff"))
    backup.audit(apply=False)
    if not backup.gate() or not ok:
        raise StepFailed("校验未通过")


# ---------------------------------------------------------------- 编排

STEPS = [
    Step("key", "age 私钥就位并与仓库公钥匹配", check_key, run_key),
    Step("config", "由模板生成 chezmoi 配置", check_config, run_config),
    Step("dotfiles", "部署 dotfiles（预演解密 → 备份将被覆盖的文件 → apply）", check_dotfiles, run_dotfiles),
    Step("system", "恢复 /etc（不含与硬件绑定的文件）", check_system, run_system,
         confirm="安装 /etc 配置文件（需要 sudo）？", platforms=("linux",)),
    Step("packages", "安装软件包清单", check_packages, run_packages, confirm="安装缺少的软件包？"),
    Step("externals", "拉取 externals（如 nvim 配置）", check_externals, run_externals),
    Step("ssh", "生成 SSH 密钥并登记 GitHub", check_ssh, run_ssh),
    Step("shell", "登录 shell 改为 zsh", check_shell, run_shell, confirm="把登录 shell 改为 zsh？",
         platforms=("linux",)),
    Step("hooks", "安装提交检查 hooks，推送改用 SSH", check_hooks, run_hooks),
    Step("verify", "最终校验", check_verify, run_verify, always=True),
]


def _safe_check(step: Step, o: Options) -> tuple[str, str]:
    try:
        return step.check(o)
    except (ui.Abort, StepFailed) as e:
        return BLOCKED, str(e)
    except Exception as e:
        return BLOCKED, f"检测出错: {type(e).__name__}: {e}"


def select(only: list[str], skip: list[str]) -> list[Step]:
    ids = {s.id for s in STEPS}
    for name in only + skip:
        if name not in ids:
            raise ui.Abort(f"未知步骤: {name}（可用: {', '.join(s.id for s in STEPS)}）", 2)
    return [s for s in STEPS if sysinfo.OS_NAME in s.platforms and (not only or s.id in only) and s.id not in skip]


def run(apply: bool, only: list[str], skip: list[str], key_file: str | None, assume_yes: bool) -> int:
    chez.require_chezmoi()
    o = Options(apply=apply, key_file=key_file, assume_yes=assume_yes)
    steps = select(only, skip)
    if not apply:
        ui.section("恢复计划（只读检测；加 --apply 执行）")
        for s in steps:
            state, detail = _safe_check(s, o)
            ui.status_line(LEVEL[state], f"{ui.pad(s.id, 10)}{ui.pad(LABEL[state], 8)}{s.title}")
            if detail:
                ui.dim(f"{' ' * 23}{detail}")
        ui.info()
        ui.info("后面的步骤依赖前面的结果（例如没有私钥就无法部署），执行时会按顺序重新检测。")
        return 0

    for s in steps:
        ui.section(f"{s.id}: {s.title}")
        state, detail = _safe_check(s, o)
        if state == DONE and not s.always:
            ui.ok(f"已完成，跳过（{detail}）")
            continue
        if state == SKIP:
            ui.info(f"  跳过：{detail}")
            continue
        if state == BLOCKED:
            ui.err(detail)
            ui.err(f"在 {s.id} 停止；解决后重新运行即可从这一步继续")
            return 1
        if detail:
            ui.info(f"  {detail}")
        if s.confirm and not ui.confirm(s.confirm, assume_yes):
            ui.info("  未确认，跳过")
            continue
        try:
            s.run(o)
        except (StepFailed, ui.Abort) as e:
            ui.err(str(e))
            ui.err(f"在 {s.id} 停止；解决后重新运行即可从这一步继续")
            return 1
        if not s.always:
            state, detail = _safe_check(s, o)
            if state not in (DONE, SKIP):
                ui.warn(f"执行后检测仍为“{LABEL[state]}”：{detail}")
                if state == BLOCKED:
                    return 1
    ui.section("完成")
    ui.ok("恢复流程结束。以后日常同步用 dotfiles backup，清理用 dotfiles clean。")
    return 0

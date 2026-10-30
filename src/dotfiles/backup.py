"""dotfiles 备份：基于 chezmoi，敏感文件用 age 加密；两端共用流程，候选清单与软件包导出按平台区分。

backup 流程：预检 → re-add 已托管文件（明文出现密钥则转为加密）→ 添加候选（登记平台归属）
          → 系统配置（Linux /etc）→ 软件包清单 → check → 可选提交
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dotfiles import chez, guard, sysinfo, ui
from dotfiles.sysinfo import IS_WINDOWS, home


@dataclass
class Profile:
    track: list[str]                    # 推荐托管的配置（相对 ~，用 /；目录展开为其中的普通文件）
    secret: list[str]                   # 一律加密的配置
    exclude: list[str] = field(default_factory=lambda: ["*.tbcache", "*.lock", "*.log", "*.bak", "*~", "*_history"])


LINUX = Profile(
    track=[
        ".zshenv", ".bash_profile", ".profile", ".tool-versions",
        ".config/user-dirs.dirs", ".config/mimeapps.list",
        ".config/kdeglobals", ".config/kwinrc", ".config/kglobalshortcutsrc", ".config/kxkbrc",
        ".config/konsolerc", ".config/dolphinrc", ".config/spectaclerc", ".local/share/konsole",
        ".config/ghostty", ".config/bottom", ".config/htop", ".config/gh/config.yml",
        ".config/systemd/user", ".config/autostart",
        ".local/share/fcitx5/rime/default.custom.yaml", ".aria2/aria2.conf",
    ],
    secret=[".wakatime.cfg", ".npmrc", ".config/gh/hosts.yml", ".docker/config.json"],
)

WINDOWS = Profile(
    track=[
        ".gitconfig", ".wslconfig", ".config/scoop/config.json",
        "Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
        "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
        "AppData/Local/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json",
        "AppData/Roaming/Code/User/settings.json", "AppData/Roaming/Code/User/keybindings.json",
        "Documents/PowerToys/Backup",
    ],
    secret=[".wakatime.cfg", ".docker/config.json", ".claude/settings.json", "AppData/Roaming/GitHub CLI/hosts.yml",
            "AppData/Roaming/rclone/rclone.conf", ".npmrc", ".pypirc", "_netrc", ".git-credentials"],
)


def profile() -> Profile:
    return WINDOWS if IS_WINDOWS else LINUX


# ---------------------------------------------------------------- 工具函数

def rel_home(path: Path) -> str:
    try:
        return path.relative_to(home()).as_posix()
    except ValueError:
        return str(path)


def expand(rel: str, prof: Profile) -> list[Path]:
    """相对 ~ 的文件或目录 → 其中的普通文件（跳过链接、大于 1MiB 的文件与 exclude 模式）。"""
    p = home() / rel
    if not os.path.lexists(p) or os.path.islink(p):
        return []
    if p.is_file():
        return [p]
    out = []
    for f in sorted(p.rglob("*")):
        try:
            if f.is_symlink() or not f.is_file() or f.stat().st_size >= 1024 * 1024:
                continue
        except OSError:
            continue
        if any(f.match(pat) for pat in prof.exclude):
            continue
        out.append(f)
    return out


def _home_path_rx() -> re.Pattern[str]:
    """绝对家目录路径：当前家目录的字面值，以及 Windows 的盘符形式、JSON 转义形式与 /c/Users 形式。"""
    leaf = re.escape(home().name)
    literal = re.escape(str(home()))
    return re.compile(rf"(?i)(?:{literal}(?![\w.-])|(?:[a-z]:|/[a-z])(?:\\\\|\\|/)users(?:\\\\|\\|/){leaf}(?![\w.-]))")


def has_home_path(path: Path) -> bool:
    try:
        return bool(_home_path_rx().search(path.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def has_secret(path: Path) -> bool:
    """出错时按“含密钥”处理：宁可多加密，不可漏加密。"""
    try:
        return guard.cmd_scan([str(path)], quiet=True) != 0
    except Exception:
        return True


def _write_utf8(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------- 预检

def preflight() -> dict:
    chez.require_chezmoi()
    config = chez.dump_config()
    if config.get("encryption") != "age":
        raise ui.Abort('chezmoi 未启用 age 加密（运行 dotfiles restore --only config）')
    if not chez.age_recipients(config):
        raise ui.Abort("chezmoi 配置缺少 age.recipient")
    ids = chez.age_identities(config)
    if not ids:
        raise ui.Abort("chezmoi 配置缺少 age.identity")
    repo = os.path.normcase(str(chez.repo()))
    for i in ids:
        if not i.is_file():
            raise ui.Abort(f"age 身份文件不存在: {i}（运行 dotfiles restore --only key）")
        if os.path.normcase(str(i.resolve())).startswith(repo + os.sep):
            raise ui.Abort(f"age 私钥位于仓库内，会被提交: {i}")
    if set(chez.age_recipients(config)) != set(chez.template_recipients()):
        ui.warn("chezmoi 配置中的 recipient 与仓库模板不一致，运行 dotfiles restore --only config 重新生成")
    if IS_WINDOWS:
        docs = sysinfo.powershell("[Environment]::GetFolderPath('MyDocuments')").stdout.strip()
        if docs and os.path.normcase(docs) != os.path.normcase(str(home() / "Documents")):
            ui.warn(f"“文档”被重定向到 {docs}，chezmoi 中 Documents/ 下的目标与实际位置不一致")
    return config


# ---------------------------------------------------------------- status

def template_drift() -> list[str]:
    """模板目标的本地修改：re-add 不处理模板，需要手动合并回 .tmpl。"""
    templates = set(chez.managed(include="templates"))
    out = []
    for line in chez.cz_lines("status", "--exclude", "externals"):
        path = line[3:]
        if line[:1] == "M" and path in templates:
            out.append(path)
    return out


def cmd_status() -> int:
    config = preflight()
    prof = profile()
    ui.section("加密配置")
    ui.info(f"仓库:     {chez.repo()}")
    ui.info(f"接收者:   {', '.join(chez.age_recipients(config))}")
    for i in chez.age_identities(config):
        ui.info(f"身份文件: {i}（{'存在' if i.is_file() else '不存在'}）")

    ui.section("chezmoi 漂移（chezmoi status）")
    r = chez.cz("status")
    ui.info(r.stdout.rstrip() or "（无）")
    for t in template_drift():
        ui.warn(f"模板目标有本地修改，re-add 不会处理，需手动合并到 .tmpl: {t}")

    ui.section("备份候选（未托管）")
    managed = {os.path.normcase(p) for p in chez.managed(include="files,symlinks", absolute=True)}
    platforms = chez.load_platforms()
    any_ = False
    for kind, rels in (("secret", prof.secret), ("track", prof.track)):
        for rel in rels:
            for f in expand(rel, prof):
                if os.path.normcase(str(f)) in managed:
                    continue
                target = rel_home(f)
                owner = chez.owner_of(target, platforms)
                if owner not in (None, "shared", sysinfo.OS_NAME):
                    flag = f"归属 {owner}→跳过"
                else:
                    flag = "encrypt" if kind == "secret" or has_secret(f) else "plain"
                    if has_home_path(f):
                        flag += ",含绝对 home 路径→跳过"
                ui.info(f"  {ui.pad(target, 60)}{flag}")
                any_ = True
    if not any_:
        ui.ok("无")

    ui.section("软件包清单")
    for name in package_files():
        p = chez.packages_dir() / name
        if p.is_file():
            ui.info(f"  {ui.pad(name, 24)}{len(p.read_text(encoding='utf-8').splitlines())} 行")
        else:
            ui.warn(f"{name} 尚未生成（backup 时生成）")

    if sysinfo.IS_LINUX:
        from dotfiles import system_etc
        system_etc.status()

    ui.section("权限审计")
    audit(apply=False)
    ui.info()
    ui.info("下一步: dotfiles backup -n 预览备份；dotfiles audit --apply 收紧权限")
    return 0


# ---------------------------------------------------------------- backup

class Runner:
    """chezmoi 修改操作：dry-run 时加 --dry-run --verbose。"""

    def __init__(self, dry: bool):
        self.flags = ["--dry-run", "--verbose"] if dry else []
        self.dry = dry

    def cz(self, *args: str) -> int:
        return chez.cz_live(*self.flags, *args)

    def cz_quiet(self, *args: str) -> sysinfo.Result:
        return chez.cz(*self.flags, *args)


def make_private(path: Path, dry: bool) -> None:
    """加密托管前把文件收紧为 600，使 chezmoi 记录 private_ 属性，部署到新机器时同样是 600。"""
    if IS_WINDOWS:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        ui.info(f"        权限 {mode:o} → 600")
        if not dry:
            os.chmod(path, 0o600)


def backup_managed(run: Runner) -> None:
    ui.section("已托管文件 re-add")
    for f in chez.managed(include="files", exclude="externals,encrypted,templates", absolute=True):
        p = Path(f)
        if p.is_file() and has_secret(p):
            ui.warn(f"明文托管文件出现密钥特征，转为加密: {rel_home(p)}")
            make_private(p, run.dry)
            run.cz("add", "--encrypt", "--", str(p))
    if run.cz("re-add") != 0:
        raise ui.Abort("chezmoi re-add 失败")
    for t in template_drift():
        ui.warn(f"模板目标有本地修改，需手动合并到 .tmpl: {t}")
    ui.ok("re-add 完成")


def add_candidate(run: Runner, f: Path, kind: str, managed: set[str]) -> None:
    if os.path.normcase(str(f)) in managed:
        return
    target = rel_home(f)
    owner = chez.owner_of(target)
    if owner not in (None, "shared", sysinfo.OS_NAME):
        ui.warn(f"跳过（归属 {owner}；两端都需要时改为模板并登记到 shared）: {target}")
        return
    if has_home_path(f):
        ui.warn(f"跳过（含绝对 home 路径，建议改为模板 {{{{ .chezmoi.homeDir }}}}）: {target}")
        return
    if kind == "secret" or has_secret(f):
        ui.info(f"  加密  {target}")
        make_private(f, run.dry)
        rc = run.cz("add", "--encrypt", "--", str(f))
    else:
        r = run.cz_quiet("add", "--secrets", "error", "--", str(f))
        if not r.ok:
            ui.warn(f"chezmoi 密钥扫描命中，改为加密: {target}")
            make_private(f, run.dry)
            rc = run.cz("add", "--encrypt", "--", str(f))
        else:
            ui.info(f"  明文  {target}")
            rc = 0
    if rc != 0:
        ui.warn(f"chezmoi add 失败: {target}")
        return
    if not run.dry and not chez.is_managed(f):
        ui.warn(f"未被托管（.chezmoiignore 忽略了它？）: {target}")
        return
    if chez.register_platform(target, sysinfo.OS_NAME, run.dry):
        ui.info(f"        登记平台归属: {sysinfo.OS_NAME}")


def backup_candidates(run: Runner) -> None:
    ui.section("添加候选配置")
    prof = profile()
    managed = {os.path.normcase(p) for p in chez.managed(include="files,symlinks", absolute=True)}
    for kind, rels in (("secret", prof.secret), ("auto", prof.track)):
        for rel in rels:
            for f in expand(rel, prof):
                add_candidate(run, f, kind, managed)


# ---------------------------------------------------------------- 软件包清单

def package_files() -> list[str]:
    return ["scoopfile.json", "winget.json", "winget-unavailable.txt"] if IS_WINDOWS else ["archpkg.txt"]


def normalize_export(kind: str, data: dict) -> str:
    """归一化导出结果，使清单只在软件包增减时变化：scoop 去掉 Updated 等每次都变的字段，winget 去掉 CreationDate。"""
    key = str.lower
    if kind == "scoop":
        apps = []
        for a in sorted(data.get("apps") or [], key=lambda a: key(a["Name"])):
            e = {"Name": a["Name"], "Source": a["Source"], "Info": a.get("Info") or ""}
            if a["Source"] == "<auto-generated>":
                e["Version"] = a.get("Version")
            apps.append(e)
        out = {"buckets": [{"Name": b["Name"], "Source": b["Source"]}
                           for b in sorted(data.get("buckets") or [], key=lambda b: key(b["Name"]))],
               "apps": apps}
    else:
        out = {"$schema": data.get("$schema"),
               "Sources": [{"Packages": sorted(({"PackageIdentifier": p["PackageIdentifier"]} for p in s.get("Packages") or []),
                                               key=lambda p: key(p["PackageIdentifier"])),
                            "SourceDetails": s["SourceDetails"]} for s in data.get("Sources") or []]}
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def _names(text: str, kind: str) -> list[str]:
    if not text.strip():
        return []
    if kind == "arch":
        return text.split()
    o = json.loads(text)
    if kind == "scoop":
        return [a["Name"] for a in o.get("apps", [])]
    return [p["PackageIdentifier"] for s in o.get("Sources", []) for p in s.get("Packages", [])]


def update_pkg_file(name: str, kind: str, new: str, dry: bool) -> None:
    path = chez.packages_dir() / name
    old = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else ""
    if old == new:
        ui.ok(f"{name} 无变化")
        return
    before, after = _names(old, kind), _names(new, kind)
    diff = [f"  -{n}" for n in before if n not in after] + [f"  +{n}" for n in after if n not in before]
    if not old:
        diff = [f"  （新建，{len(after)} 项）"]
    ui.info(f"{name}:")
    for line in diff or ["  软件包不变，元数据有更新"]:
        ui.info(line)
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_utf8(path, new)


def backup_packages(dry: bool) -> None:
    ui.section("软件包清单")
    if not IS_WINDOWS:
        if sysinfo.have("pacman"):
            update_pkg_file("archpkg.txt", "arch", sysinfo.run(["pacman", "-Qqe"]).stdout, dry)
        return
    if sysinfo.have("scoop"):
        r = sysinfo.run(["scoop", "export"], timeout=300)
        try:
            update_pkg_file("scoopfile.json", "scoop", normalize_export("scoop", json.loads(r.stdout)), dry)
        except (ValueError, KeyError) as e:
            ui.warn(f"scoop export 失败（退出码 {r.returncode}）: {e}")
    if sysinfo.have("winget"):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "winget.json"
            r = sysinfo.run(["winget", "export", "-o", str(raw), "--accept-source-agreements",
                             "--disable-interactivity"], timeout=600)
            if not raw.is_file():
                ui.warn(f"winget export 失败（退出码 {r.returncode}）")
                return
            data = json.loads(raw.read_text(encoding="utf-8-sig"))
        update_pkg_file("winget.json", "winget", normalize_export("winget", data), dry)
        unavailable = sorted(set(re.findall(r"not available from any source:\s*(.+?)\s*$", r.stdout, re.M)))
        text = "# winget export 无法导出的已安装程序（不在任何 winget 源中），需手动安装\n" + "".join(f"{u}\n" for u in unavailable)
        path = chez.packages_dir() / "winget-unavailable.txt"
        if (path.read_text(encoding="utf-8") if path.is_file() else "") != text:
            ui.info(f"winget-unavailable.txt: {len(unavailable)} 项")
            if not dry:
                _write_utf8(path, text)


# ---------------------------------------------------------------- check

def gate() -> bool:
    ui.section("安全检查")
    bad = False
    for i in chez.age_identities():
        if chez.tracked(i):
            ui.err(f"age 私钥被 git 跟踪: {i}")
            bad = True
    cwd = os.getcwd()
    try:
        os.chdir(chez.repo())
        if guard.cmd_tree() != 0:
            bad = True
    finally:
        os.chdir(cwd)
    problems = chez.check_platforms()
    for p in problems:
        ui.err(f"平台归属: {p}")
    if problems:
        bad = True
    if bad:
        ui.err("安全检查未通过，不会提交")
        return False
    ui.ok("未发现明文密钥，.age 文件格式正确，敏感目标均已加密，平台归属完整")
    return True


def cmd_check() -> int:
    preflight()
    return 0 if gate() else 1


def cmd_backup(dry: bool, with_system: bool, commit: bool) -> int:
    preflight()
    run = Runner(dry)
    backup_managed(run)
    backup_candidates(run)
    if with_system and sysinfo.IS_LINUX:
        from dotfiles import system_etc
        system_etc.backup(run)
    backup_packages(dry)
    passed = gate()
    ui.section("仓库状态")
    ui.info(chez.git("status", "--short").stdout.rstrip() or "（无变更）")
    if not passed:
        return 1
    if commit and not dry:
        chez.git("add", "-A")
        if chez.git("diff", "--cached", "--quiet").ok:
            ui.ok("无变更可提交")
            return 0
        from datetime import date
        msg = f"backup: sync {sysinfo.OS_NAME} dotfiles ({date.today().isoformat()})"
        if sysinfo.run_live(["git", "-C", str(chez.repo()), "commit", "-q", "-m", msg]) != 0:
            raise ui.Abort("git commit 失败（pre-commit 检查未通过？）")
        ui.ok(f"已提交 {chez.git('log', '-1', '--format=%h').stdout.strip()}（未推送，确认后 git push）")
    ui.info()
    ui.info("提醒：age 私钥不在仓库中，请另行离线备份（密码管理器 / U 盘）：")
    for i in chez.age_identities():
        ui.info(f"  {i}")
    return 0


# ---------------------------------------------------------------- audit

class _Audit:
    hits = 0


def _audit_targets() -> list[tuple[Path, bool]]:
    """(路径, 是否目录)：一律加密的配置、凭据文件、~/.ssh、~/.gnupg、age 身份文件及其目录。"""
    h = home()
    out: list[tuple[Path, bool]] = []
    for rel in [*profile().secret, ".pypirc", ".netrc", ".git-credentials", ".ssh/config"]:
        out.append((h / rel, False))
    out += [(h / ".ssh", True), (h / ".gnupg", True)]
    ssh = h / ".ssh"
    if ssh.is_dir():
        for f in sorted(ssh.iterdir()):
            if f.is_file() and f.suffix != ".pub" and not f.name.startswith("known_hosts") and f.name != "config":
                out.append((f, False))
    for i in chez.age_identities():
        out += [(i, False), (i.parent, True)]
    seen, uniq = set(), []
    for p, d in out:
        if p not in seen and os.path.lexists(p) and not os.path.islink(p):
            seen.add(p)
            uniq.append((p, d))
    return uniq


def _audit_posix(path: Path, is_dir: bool, apply: bool) -> None:
    want = 0o700 if is_dir else 0o600
    cur = stat.S_IMODE(os.lstat(path).st_mode)
    if cur & ~want & 0o777:
        ui.info(f"  {ui.pad(fsutil_tilde(path), 50)}{cur:o} → {want:o}")
        _Audit.hits += 1
        if apply:
            os.chmod(path, want)


def fsutil_tilde(path: Path) -> str:
    from dotfiles.fsutil import tilde
    return tilde(path)


_ALLOWED_SIDS_EXTRA = ("S-1-5-18", "S-1-5-32-544")   # SYSTEM, Administrators


def current_sid() -> str:
    out = sysinfo.run(["whoami", "/user", "/fo", "csv", "/nh"]).stdout.strip()
    m = re.search(r'"(S-1-[\d-]+)"', out)
    return m.group(1) if m else ""


def restrict_acl(path: Path, is_dir: bool) -> bool:
    """Windows：去掉继承，只允许当前用户、SYSTEM、Administrators 访问。"""
    inh = "(OI)(CI)" if is_dir else ""
    sids = [current_sid(), *_ALLOWED_SIDS_EXTRA]
    args = ["icacls", str(path), "/inheritance:r"]
    for sid in sids:
        if sid:
            args += ["/grant:r", f"*{sid}:{inh}F"]
    return sysinfo.run([*args, "/q"]).ok


def _audit_windows(path: Path, is_dir: bool, apply: bool) -> None:
    script = ("(Get-Acl -LiteralPath $env:DOTFILES_AUDIT_PATH).Access | Where-Object AccessControlType -eq 'Allow' | "
              "ForEach-Object { try { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } "
              "catch { $_.IdentityReference.Value } }")
    os.environ["DOTFILES_AUDIT_PATH"] = str(path)
    try:
        sids = set(sysinfo.powershell(script).stdout.split())
    finally:
        os.environ.pop("DOTFILES_AUDIT_PATH", None)
    allowed = {current_sid(), *_ALLOWED_SIDS_EXTRA}
    extra = sorted(sids - allowed)
    if not extra:
        return
    ui.info(f"  {ui.pad(fsutil_tilde(path), 50)}额外可访问: {', '.join(extra)}")
    _Audit.hits += 1
    if apply and not restrict_acl(path, is_dir):
        ui.warn(f"icacls 失败: {path}")


def audit(apply: bool) -> None:
    _Audit.hits = 0
    for path, is_dir in _audit_targets():
        (_audit_windows if IS_WINDOWS else _audit_posix)(path, is_dir, apply)
    if _Audit.hits == 0:
        ui.ok("敏感文件权限正常")
    elif not apply:
        ui.info(f"共 {_Audit.hits} 项权限过宽，dotfiles audit --apply 收紧")
    else:
        ui.ok(f"已收紧 {_Audit.hits} 项权限")


def cmd_audit(apply: bool) -> int:
    ui.section("权限审计")
    audit(apply)
    return 0

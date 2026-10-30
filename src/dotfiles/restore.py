"""Post-reinstall recovery: run after bootstrap has installed git / chezmoi / uv and cloned the repo.

Each step is first detected (read-only, based on the current system state, no state files), then
executed as needed; if a run fails partway, rerun the same command to resume from the unfinished
step. Shows the plan by default; --apply executes.
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
LABEL = {DONE: "done", TODO: "todo", SKIP: "skip", BLOCKED: "blocked"}
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
    check: Callable[[Options], tuple[str, str]]     # (state, detail)
    run: Callable[[Options], None]
    confirm: str = ""                                # when set, confirm before running
    platforms: tuple[str, ...] = ("linux", "windows")
    always: bool = False                             # verification step: run every time


class StepFailed(Exception):
    pass


# ---------------------------------------------------------------- age private key

def generated_config() -> dict | None:
    from dotfiles.doctor import expected_config
    return expected_config()


def target_identity() -> Path:
    """The identity path the config template will pick (existing machines keep their old location;
    new machines use ~/.config/chezmoi/key.txt)."""
    cfg = generated_config() or {}
    value = (cfg.get("age") or {}).get("identity")
    return Path(value) if value else home() / chez.DEFAULT_IDENTITY


def check_key(o: Options) -> tuple[str, str]:
    path = target_identity()
    if not path.is_file():
        return TODO, f"{path} does not exist; the offline-stored private key is needed"
    recipient = chez.recipient_of(path)
    if recipient not in chez.template_recipients():
        return BLOCKED, f"{path} does not match the repo's public key (provide the right key and retry)"
    return DONE, f"{path} (matches the repo's public key)"


_KEY_RX = re.compile(r"^AGE-SECRET-KEY-(PQ-)?1[0-9A-Z]+$")


def _read_key_material(o: Options) -> str:
    if o.key_file:
        return Path(o.key_file).expanduser().read_text(encoding="utf-8")
    if not os.isatty(0):
        raise StepFailed("not a terminal: pass the key file with --key-file")
    answer = input("key file path (e.g. on a USB drive; leave empty to paste the key): ").strip().strip('"')
    if answer:
        return Path(answer).expanduser().read_text(encoding="utf-8")
    return getpass.getpass("paste the line starting with AGE-SECRET-KEY- (input hidden): ")


def write_private(path: Path, text: str) -> None:
    """Write with owner-only permissions: POSIX 600 (parent dir 700); on Windows drop inheritance and
    keep only the user / SYSTEM / Administrators."""
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700)
    if IS_WINDOWS:
        with open(path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        from dotfiles.backup import restrict_acl
        if not restrict_acl(path, is_dir=False):
            ui.warn(f"could not tighten the ACL of {path}; run dotfiles audit --apply")
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
        raise StepFailed("no line starting with AGE-SECRET-KEY- found")
    comments = [line for line in lines if line.startswith("#")]
    write_private(path, "\n".join(comments + secrets) + "\n")
    recipient = chez.recipient_of(path)
    if recipient not in chez.template_recipients():
        path.unlink()
        raise StepFailed("the key does not match the repo's recipient; the written file has been deleted")
    ui.ok(f"key written to {path} (matches the repo's public key)")


# ---------------------------------------------------------------- chezmoi config

def check_config(o: Options) -> tuple[str, str]:
    from dotfiles.doctor import current_config, default_source_dir
    configured = sysinfo.query(["chezmoi", "source-path"]) or ""
    if os.path.normcase(configured) != os.path.normcase(str(chez.source_state_dir())):
        return BLOCKED, (f"chezmoi source dir is {configured or 'unknown'}; the repo should be at "
                         f"{default_source_dir()} (bootstrap clones it there)")
    cur, exp = current_config(), generated_config()
    if exp is None:
        return BLOCKED, "cannot render .chezmoi.toml.tmpl"
    if cur == exp:
        return DONE, str(chez.config_file())
    return TODO, "config does not exist" if cur is None else "config differs from the template output; it will be regenerated"


def run_config(o: Options) -> None:
    old = chez.config_file()
    if old.is_file():
        backup = old.with_name(f"chezmoi.toml.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(old, backup)
        ui.info(f"  previous config backed up to {backup}")
    r = chez.cz("init", "--no-tty")
    if not r.ok:
        raise StepFailed(f"chezmoi init failed: {r.stderr.strip()[:300]}")
    chez.dump_config.cache_clear()


# ---------------------------------------------------------------- deploy dotfiles

def _status(*extra: str) -> list[tuple[str, str]]:
    r = chez.cz("status", *extra)
    if not r.ok:
        raise StepFailed(f"chezmoi status failed (cannot decrypt?): {r.stderr.strip()[:300]}")
    return [(line[:2], line[3:]) for line in r.stdout.splitlines() if len(line) > 3]


def check_dotfiles(o: Options) -> tuple[str, str]:
    try:
        pending = [(c, p) for c, p in _status("--exclude", "externals") if c[1] != " "]
    except StepFailed as e:
        return BLOCKED, str(e)
    if not pending:
        return DONE, "target state matches the repo"
    existing = sum(1 for c, p in pending if c[1] == "M")
    return TODO, f"{len(pending)} items to deploy ({existing} existing files will be backed up before being overwritten)"


def run_dotfiles(o: Options) -> None:
    dry = chez.cz("apply", "--dry-run", "--exclude", "externals", "--no-tty")
    if not dry.ok:   # full rehearsal first: if decryption fails, nothing is written
        raise StepFailed(f"rehearsal failed, nothing was written: {dry.stderr.strip()[:300]}")
    pending = [p for c, p in _status("--exclude", "externals") if c[1] == "M"]
    if pending:
        dest = sysinfo.xdg_state() / "dotfiles" / f"pre-apply-{datetime.now():%Y%m%d-%H%M%S}"
        dest.mkdir(parents=True, mode=0o700)
        for rel in pending:
            src = home() / rel
            if src.is_file() and not src.is_symlink():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)
        ui.info(f"  {len(pending)} files about to be overwritten were backed up to {dest}")
    if chez.cz_live("apply", "--exclude", "externals", "--force", "--no-tty") != 0:
        raise StepFailed("chezmoi apply failed")


# ---------------------------------------------------------------- system config (Linux /etc)

def check_system(o: Options) -> tuple[str, str]:
    if not (chez.system_source_dir() / "etc").is_dir():
        return SKIP, "no /etc backup in system/ yet (created by running dotfiles backup once on Linux)"
    return TODO, "compares /etc with the backup; hardware-bound files (fstab etc.) show diffs only and are never installed automatically"


def run_system(o: Options) -> None:
    from dotfiles import system_etc
    system_etc.cmd_restore(apply=True, filters=[])


# ---------------------------------------------------------------- packages

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
            return SKIP, "not an Arch-like distribution"
        from dotfiles.doctor import missing_arch_packages
        missing = missing_arch_packages()
        if not missing:
            return DONE, "all packages in archpkg.txt are installed"
        repo, foreign = _arch_split(missing)
        return TODO, f"{len(missing)} missing: {len(repo)} from configured repos, {len(foreign)} from AUR or unknown"
    winget = _winget_missing()
    scoop = _scoop_missing()
    parts = []
    if winget:
        parts.append(f"winget missing {len(winget)}")
    if scoop:
        parts.append(f"scoop missing {len(scoop)}" + ("" if have("scoop") else " (scoop not installed)"))
    if not parts:
        return DONE, "all packages in winget.json and scoopfile.json are installed"
    if scoop and not have("scoop"):
        return BLOCKED, ("; ".join(parts) + ". Install scoop first per https://scoop.sh "
                         "(its official install script must be run; this tool will not run it for you)")
    return TODO, "; ".join(parts)


def run_packages(o: Options) -> None:
    if IS_LINUX:
        from dotfiles.doctor import missing_arch_packages
        repo, foreign = _arch_split(missing_arch_packages())
        if repo:
            cmd = ["sudo", "pacman", "-S", "--needed", *(["--noconfirm"] if o.assume_yes else []), *repo]
            if sysinfo.run_live(cmd) != 0:
                raise StepFailed("pacman install failed (rerun this step to continue)")
        if foreign:
            helper = next((h for h in ("paru", "yay") if have(h)), None)
            if helper:
                ui.info(f"  installing {len(foreign)} AUR packages via {helper}")
                if sysinfo.run_live([helper, "-S", "--needed", *(["--noconfirm"] if o.assume_yes else []), *foreign]) != 0:
                    ui.warn("some AUR packages failed to install")
            else:
                ui.warn(f"paru/yay not installed; the following {len(foreign)} packages need manual install (AUR or archlinuxcn):")
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
            ui.note(f"{len(names)} programs are not in any winget source and need manual install (packages/winget-unavailable.txt)")


# ---------------------------------------------------------------- externals

def proxy_override() -> dict[str, str]:
    """When git has a proxy configured but it is unreachable (proxy app not installed on a new machine),
    temporarily switch to a direct connection."""
    from dotfiles.doctor import _proxy_reachable, git_proxy
    proxy = git_proxy()
    if not proxy or _proxy_reachable(proxy):
        return {}
    ui.warn(f"git proxy {proxy} is unreachable; connecting directly for this run")
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.proxy", "GIT_CONFIG_VALUE_0": ""}


def check_externals(o: Options) -> tuple[str, str]:
    try:
        externals = chez.managed(include="externals", exclude="")
    except ui.Abort as e:
        return BLOCKED, str(e)
    if not externals:
        return SKIP, "no externals on this platform"
    missing = [e for e in externals if not (home() / e).exists()]
    if missing:
        return TODO, f"to fetch: {', '.join(missing)}"
    return DONE, f"{len(externals)} in place (refreshed automatically per refreshPeriod)"


def run_externals(o: Options) -> None:
    if chez.cz_live("apply", "--include", "externals", "--force", "--no-tty", env=proxy_override()) != 0:
        raise StepFailed("failed to fetch externals (check network or proxy, then rerun)")


# ---------------------------------------------------------------- SSH keys

def _ssh_config_keys() -> tuple[list[Path], Path]:
    """Private keys referenced by ~/.ssh/config, plus the one used for github.com (default id_ed25519)."""
    ssh = home() / ".ssh"
    keys: list[Path] = [ssh / "id_ed25519"]
    github = ssh / "id_ed25519"
    cfg = ssh / "config"
    if cfg.is_file():
        hosts: list[str] = []
        for raw in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            line = re.sub(r"\s#.*$", "", raw).strip()     # strip trailing comment
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
        return SKIP, "ssh-keygen is not installed"
    keys, _ = _ssh_config_keys()
    missing = [k for k in keys if not k.is_file()]
    if missing:
        return TODO, (f"generate {len(missing)} new key(s): {', '.join(k.name for k in missing)} "
                      "(private keys are not backed up; each machine gets fresh ones)")
    return DONE, f"all {len(keys)} keys exist"


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
        ui.info(f"  generating {k} (prompts for a passphrase; setting one is recommended)")
        if sysinfo.run_live(["ssh-keygen", "-t", "ed25519", "-f", str(k), "-C", f"{getpass.getuser()}@{host} {time.strftime('%Y-%m-%d')}"]) != 0:
            raise StepFailed(f"ssh-keygen failed: {k}")
        created.append(k)
    if IS_WINDOWS and created:
        from dotfiles.backup import restrict_acl
        for k in created:
            restrict_acl(k, is_dir=False)
    if github in created and have("gh"):
        if sysinfo.run(["gh", "auth", "status"]).ok:
            if ui.confirm(f"register {github.name}.pub with GitHub?", o.assume_yes):
                sysinfo.run_live(["gh", "ssh-key", "add", f"{github}.pub", "--title", f"{host} {time.strftime('%Y-%m-%d')}"])
        else:
            ui.note(f"run gh auth login first, then: gh ssh-key add {github}.pub --title {host}")
    others = [k for k in created if k != github]
    if others:
        ui.note("the following new public keys need to be registered on their servers (ssh-copy-id or append to authorized_keys):")
        for k in others:
            ui.info(f"  {k}.pub")


# ---------------------------------------------------------------- login shell (Linux)

def _login_shell() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_shell


def check_shell(o: Options) -> tuple[str, str]:
    zsh = sysinfo.which("zsh")
    if not zsh:
        return SKIP, "zsh not installed (rechecked after the packages step)"
    current = _login_shell()
    if Path(current).name == "zsh":
        return DONE, current
    return TODO, f"currently {current}, will change to {zsh}"


def run_shell(o: Options) -> None:
    if sysinfo.run_live(["chsh", "-s", sysinfo.which("zsh") or "/usr/bin/zsh"]) != 0:
        raise StepFailed("chsh failed")


# ---------------------------------------------------------------- commit checks and push URL

_GITHUB_HTTPS = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def _ssh_pushurl(repo_dir: Path) -> tuple[str, str] | None:
    """When origin is a GitHub HTTPS URL: (current push URL, expected SSH push URL)."""
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
    if any(i.name == "commit hooks" and i.level != "ok" for i in rep.items):
        todo.append("install pre-commit / pre-push hooks")
    for d in _hook_dirs():
        urls = _ssh_pushurl(d)
        if urls and urls[0] != urls[1]:
            todo.append(f"{d.name}: switch push to SSH")
    return (TODO, "; ".join(todo)) if todo else (DONE, "hooks installed, push URL is SSH")


def run_hooks(o: Options) -> None:
    if not have("pre-commit"):
        if sysinfo.run_live(["uv", "tool", "install", "pre-commit"]) != 0:
            raise StepFailed("uv tool install pre-commit failed")
    if not have("pre-commit"):
        ui.warn("pre-commit installed but not on PATH: run uv tool update-shell, reopen the terminal, then rerun this step")
    elif sysinfo.run_live(["pre-commit", "install"], cwd=chez.repo()) != 0:
        raise StepFailed("pre-commit install failed")
    for d in _hook_dirs():
        urls = _ssh_pushurl(d)
        if urls and urls[0] != urls[1]:
            sysinfo.run(["git", "-C", str(d), "remote", "set-url", "--push", "origin", urls[1]])
            ui.info(f"  {d}: fetch stays on HTTPS, push switched to {urls[1]}")


# ---------------------------------------------------------------- verify

def check_verify(o: Options) -> tuple[str, str]:
    return TODO, "chezmoi verify + security check + permission audit"


def run_verify(o: Options) -> None:
    from dotfiles import backup
    ok = chez.cz("verify", "--exclude", "externals").ok
    (ui.ok if ok else ui.warn)("chezmoi verify: " + ("target state matches the repo" if ok else "still differs; see chezmoi diff"))
    backup.audit(apply=False)
    if not backup.gate() or not ok:
        raise StepFailed("verification failed")


# ---------------------------------------------------------------- orchestration

STEPS = [
    Step("key", "age private key in place and matching the repo's public key", check_key, run_key),
    Step("config", "generate the chezmoi config from the template", check_config, run_config),
    Step("dotfiles", "deploy dotfiles (rehearse decryption -> back up overwritten files -> apply)", check_dotfiles, run_dotfiles),
    Step("system", "restore /etc (excluding hardware-bound files)", check_system, run_system,
         confirm="install /etc config files (needs sudo)?", platforms=("linux",)),
    Step("packages", "install package manifests", check_packages, run_packages, confirm="install the missing packages?"),
    Step("externals", "fetch externals (e.g. the nvim config)", check_externals, run_externals),
    Step("ssh", "generate SSH keys and register GitHub", check_ssh, run_ssh),
    Step("shell", "change the login shell to zsh", check_shell, run_shell, confirm="change the login shell to zsh?",
         platforms=("linux",)),
    Step("hooks", "install commit-check hooks, switch push to SSH", check_hooks, run_hooks),
    Step("verify", "final verification", check_verify, run_verify, always=True),
]


def _safe_check(step: Step, o: Options) -> tuple[str, str]:
    try:
        return step.check(o)
    except (ui.Abort, StepFailed) as e:
        return BLOCKED, str(e)
    except Exception as e:
        return BLOCKED, f"detection error: {type(e).__name__}: {e}"


def select(only: list[str], skip: list[str]) -> list[Step]:
    ids = {s.id for s in STEPS}
    for name in only + skip:
        if name not in ids:
            raise ui.Abort(f"unknown step: {name} (available: {', '.join(s.id for s in STEPS)})", 2)
    return [s for s in STEPS if sysinfo.OS_NAME in s.platforms and (not only or s.id in only) and s.id not in skip]


def run(apply: bool, only: list[str], skip: list[str], key_file: str | None, assume_yes: bool) -> int:
    chez.require_chezmoi()
    o = Options(apply=apply, key_file=key_file, assume_yes=assume_yes)
    steps = select(only, skip)
    if not apply:
        ui.section("Recovery plan (read-only detection; add --apply to execute)")
        for s in steps:
            state, detail = _safe_check(s, o)
            ui.status_line(LEVEL[state], f"{ui.pad(s.id, 10)}{ui.pad(LABEL[state], 8)}{s.title}")
            if detail:
                ui.dim(f"{' ' * 23}{detail}")
        ui.info()
        ui.info("later steps depend on earlier results (nothing can deploy without the key, for example); each is re-detected at execution time.")
        return 0

    for s in steps:
        ui.section(f"{s.id}: {s.title}")
        state, detail = _safe_check(s, o)
        if state == DONE and not s.always:
            ui.ok(f"already done, skipping ({detail})")
            continue
        if state == SKIP:
            ui.info(f"  skip: {detail}")
            continue
        if state == BLOCKED:
            ui.err(detail)
            ui.err(f"stopped at {s.id}; fix the problem and rerun to continue from this step")
            return 1
        if detail:
            ui.info(f"  {detail}")
        if s.confirm and not ui.confirm(s.confirm, assume_yes):
            ui.info("  not confirmed, skipping")
            continue
        try:
            s.run(o)
        except (StepFailed, ui.Abort) as e:
            ui.err(str(e))
            ui.err(f"stopped at {s.id}; fix the problem and rerun to continue from this step")
            return 1
        if not s.always:
            state, detail = _safe_check(s, o)
            if state not in (DONE, SKIP):
                ui.warn(f"after running, the step is still \"{LABEL[state]}\": {detail}")
                if state == BLOCKED:
                    return 1
    ui.section("Done")
    ui.ok("recovery finished. For daily syncs use dotfiles backup, for cleanup dotfiles clean.")
    return 0

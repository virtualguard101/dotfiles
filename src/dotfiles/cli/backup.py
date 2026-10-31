"""dotfiles backup via chezmoi; sensitive files are age-encrypted. The flow is shared by both
platforms; candidate lists and package exports differ per platform.

backup flow: preflight -> re-add managed files (plaintext with secrets is switched to encrypted)
          -> add candidates (registered in platforms.toml) -> system config (Linux /etc)
          -> package manifests -> check -> optional commit
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotfiles.utils import platforms, ui
from dotfiles.utils.common import chez, guard
from dotfiles.utils.common import config as dotconf
from dotfiles.utils.platforms import IS_WINDOWS, home
from dotfiles.utils.platforms import windows as winplat


@dataclass
class Profile:
    track: list[str]       # configs recommended for tracking (relative to ~; directories expand to regular files)
    secret: list[str]      # always encrypted
    exclude: list[str]     # file-name patterns skipped when expanding directories
    ignore: list[str]      # candidates never added nor reported


def profile(os_name: str = platforms.OS_NAME) -> Profile:
    """This platform's lists from dotfiles.toml ([backup.<os>])."""
    return Profile(*(list(dotconf.get(f"backup.{os_name}.{k}")) for k in ("track", "secret", "exclude", "ignore")))


def is_ignored(target: str, patterns: list[str]) -> bool:
    """target (relative to ~, with /) equals a pattern, lies inside a directory pattern, or matches a glob."""
    for pat in patterns:
        pat = pat.rstrip("/")
        if target == pat or target.startswith(pat + "/") or fnmatch.fnmatch(target, pat):
            return True
    return False


# ---------------------------------------------------------------- helpers

def rel_home(path: Path) -> str:
    try:
        return path.relative_to(home()).as_posix()
    except ValueError:
        return str(path)


def expand(rel: str, prof: Profile) -> list[Path]:
    """File or directory relative to ~ -> the regular files inside (skips links, files >= 1MiB,
    exclude patterns and ignored targets)."""
    p = home() / rel
    if not os.path.lexists(p) or os.path.islink(p):
        return []
    out = []
    if p.is_file():
        out = [p]
    else:
        for f in sorted(p.rglob("*")):
            try:
                if f.is_symlink() or not f.is_file() or f.stat().st_size >= 1024 * 1024:
                    continue
            except OSError:
                continue
            if any(f.match(pat) for pat in prof.exclude):
                continue
            out.append(f)
    return [f for f in out if not is_ignored(rel_home(f), prof.ignore)]


def has_home_path(path: Path) -> bool:
    try:
        return bool(guard.home_path_rx(home()).search(path.read_bytes()))
    except OSError:
        return False


def has_secret(path: Path) -> bool:
    """Errors are treated as "contains secrets": prefer over-encrypting over missing one."""
    try:
        return guard.cmd_scan([str(path)], quiet=True) != 0
    except Exception:
        return True


def _write_utf8(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------- preflight

def preflight() -> dict:
    chez.require_chezmoi()
    config = chez.dump_config()
    if config.get("encryption") != "age":
        raise ui.Abort('chezmoi age encryption is not enabled (run dotfiles restore --only config)')
    if not chez.age_recipients(config):
        raise ui.Abort("chezmoi config is missing age.recipient")
    ids = chez.age_identities(config)
    if not ids:
        raise ui.Abort("chezmoi config is missing age.identity")
    repo = os.path.normcase(str(chez.repo()))
    for i in ids:
        if not i.is_file():
            raise ui.Abort(f"age identity file does not exist: {i} (run dotfiles restore --only key)")
        if os.path.normcase(str(i.resolve())).startswith(repo + os.sep):
            raise ui.Abort(f"age private key lives inside the repo and would be committed: {i}")
    if set(chez.age_recipients(config)) != set(chez.template_recipients()):
        ui.warn("recipient in the chezmoi config differs from the repo template; run dotfiles restore --only config")
    if IS_WINDOWS:
        docs = winplat.powershell("[Environment]::GetFolderPath('MyDocuments')").stdout.strip()
        if docs and os.path.normcase(docs) != os.path.normcase(str(home() / "Documents")):
            ui.warn(f"the Documents folder is redirected to {docs}; targets under Documents/ do not match the real location")
    return config


# ---------------------------------------------------------------- status

def template_drift() -> list[str]:
    """Local modifications to template targets: re-add does not handle templates, so merge them back into .tmpl by hand."""
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
    ui.section("Encryption config")
    ui.info(f"repo:       {chez.repo()}")
    ui.info(f"recipient:  {', '.join(chez.age_recipients(config))}")
    for i in chez.age_identities(config):
        ui.info(f"identity:   {i} ({'present' if i.is_file() else 'missing'})")

    ui.section("chezmoi drift (chezmoi status)")
    r = chez.cz("status")
    ui.info(r.stdout.rstrip() or "(none)")
    for t in template_drift():
        ui.warn(f"template target has local changes that re-add will not touch; merge them into the .tmpl by hand: {t}")

    ui.section("Backup candidates (unmanaged)")
    managed = {os.path.normcase(p) for p in chez.managed(include="files,symlinks", absolute=True)}
    owners = chez.load_platforms()
    any_ = False
    for kind, rels in (("secret", prof.secret), ("track", prof.track)):
        for rel in rels:
            for f in expand(rel, prof):
                if os.path.normcase(str(f)) in managed:
                    continue
                target = rel_home(f)
                owner = chez.owner_of(target, owners)
                if owner not in (None, "shared", platforms.OS_NAME):
                    flag = f"owned by {owner}->skip"
                else:
                    flag = "encrypt" if kind == "secret" or has_secret(f) else "plain"
                    if has_home_path(f):
                        flag += ",absolute home path->skip"
                ui.info(f"  {ui.pad(target, 60)}{flag}")
                any_ = True
    if not any_:
        ui.ok("none")

    ui.section("Package manifests")
    for name in package_files():
        p = chez.packages_dir() / name
        if p.is_file():
            ui.info(f"  {ui.pad(name, 24)}{len(p.read_text(encoding='utf-8').splitlines())} lines")
        else:
            ui.warn(f"{name} not generated yet (created by backup)")

    if platforms.IS_LINUX:
        from dotfiles.cli import etc
        etc.status()

    ui.section("Permission audit")
    from dotfiles.cli import audit
    audit.audit(apply=False)
    ui.info()
    ui.info("next: dotfiles backup -n to preview a backup; dotfiles audit --apply to tighten permissions")
    return 0


# ---------------------------------------------------------------- backup

class Runner:
    """chezmoi modifying operations. Dry-run adds --dry-run --verbose and captures the output, so
    chezmoi never takes over the terminal, and diffs of .age files are summarized: ciphertext is
    unreadable and differs on every encryption."""

    def __init__(self, dry: bool):
        self.flags = ["--dry-run", "--verbose"] if dry else []
        self.dry = dry

    def cz(self, *args: str) -> int:
        if not self.dry:
            return chez.cz_live(*args)
        r = chez.cz(*self.flags, *args)
        text = summarize_diff(r.stdout)
        if text.strip():
            print(text.rstrip("\n"))
        if r.stderr.strip():
            print(r.stderr.rstrip("\n"), file=sys.stderr)
        return r.returncode

    def cz_quiet(self, *args: str) -> platforms.Result:
        return chez.cz(*self.flags, *args)


_DIFF_HEAD = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


def summarize_diff(text: str) -> str:
    """Keep plaintext diff blocks; replace each .age block with a single line."""
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        m = _DIFF_HEAD.match(block[0])
        if m and (m.group(1).endswith(".age") or m.group(2).endswith(".age")):
            if any(line.startswith("new file mode") for line in block):
                kind, path = "new", m.group(2)
            elif any(line.startswith("deleted file mode") for line in block):
                kind, path = "deleted", m.group(1)
            else:
                kind, path = "modified", m.group(2)
            out.append(f"  {kind:<9}{path}  (encrypted; ciphertext not shown)")
        else:
            out.extend(block)
        block.clear()

    for line in text.splitlines():
        if _DIFF_HEAD.match(line):
            flush()
            block.append(line)
        elif block:
            block.append(line)
        else:
            out.append(line)
    flush()
    return "\n".join(out) + ("\n" if out else "")


def make_private(path: Path, dry: bool) -> None:
    """Tighten a file to 600 before encrypted tracking so chezmoi records the private_ attribute
    and deploys it as 600 on new machines too."""
    if IS_WINDOWS:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        ui.info(f"        mode {mode:o} -> 600")
        if not dry:
            os.chmod(path, 0o600)


_FILE_PREFIXES = ("create_", "modify_", "encrypted_", "private_", "readonly_", "empty_", "executable_")


def has_private_attr(source_rel: str) -> bool:
    """Whether a source file name carries chezmoi's private_ attribute (prefixes come in a fixed order)."""
    name = source_rel.rsplit("/", 1)[-1]
    for prefix in _FILE_PREFIXES:
        if name.startswith(prefix):
            if prefix == "private_":
                return True
            name = name[len(prefix):]
    return False


def private_drift() -> list[Path]:
    """Managed private_ files whose live mode is looser than 600. re-add copies the live mode back
    into the source, which would drop private_ and make new machines deploy the file as 644."""
    if IS_WINDOWS:
        return []
    r = chez.cz("managed", "-i", "files", "-x", "externals,templates", "-p", "all")
    if not r.ok:
        raise ui.Abort(f"chezmoi managed failed: {r.stderr.strip()[:300]}")
    out = []
    for info in json.loads(r.stdout or "{}").values():
        if not has_private_attr(info["sourceRelative"]):
            continue
        p = Path(info["absolute"])
        try:
            st = p.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o077:
            out.append(p)
    return out


def backup_managed(run: Runner) -> None:
    ui.section("re-add managed files")
    for f in chez.managed(include="files", exclude="externals,encrypted,templates", absolute=True):
        p = Path(f)
        if p.is_file() and has_secret(p):
            ui.warn(f"plaintext managed file now matches a secret pattern; switching to encrypted: {rel_home(p)}")
            make_private(p, run.dry)
            run.cz("add", "--encrypt", "--", str(p))
    loose = private_drift()
    for p in loose:
        ui.info(f"  keep private_: {rel_home(p)}")
        make_private(p, run.dry)
    if run.dry and loose:
        # nothing was tightened yet: leave these out so the preview shows re-add after tightening
        skip = {str(p) for p in loose}
        targets = [f for f in chez.managed(include="files", exclude="externals,templates", absolute=True)
                   if f not in skip]
        ui.dim("  (content changes of the files above are not previewed until their mode is fixed)")
        rc = run.cz("re-add", "--", *targets) if targets else 0
    else:
        rc = run.cz("re-add")
    if rc != 0:
        raise ui.Abort("chezmoi re-add failed")
    for t in template_drift():
        ui.warn(f"template target has local changes; merge them into the .tmpl by hand: {t}")
    ui.ok("re-add done")


def add_candidate(run: Runner, f: Path, kind: str, managed: set[str]) -> None:
    if os.path.normcase(str(f)) in managed:
        return
    target = rel_home(f)
    owner = chez.owner_of(target)
    if owner not in (None, "shared", platforms.OS_NAME):
        ui.warn(f"skip (owned by {owner}; if both platforms need it, make it a template and register it as shared): {target}")
        return
    if has_home_path(f):
        ui.warn(f"skip (contains an absolute home path; consider a template with {{{{ .chezmoi.homeDir }}}}): {target}")
        return
    if kind == "secret" or has_secret(f):
        ui.info(f"  encrypt  {target}")
        make_private(f, run.dry)
        rc = run.cz("add", "--encrypt", "--", str(f))
    else:
        r = run.cz_quiet("add", "--secrets", "error", "--", str(f))
        if not r.ok:
            ui.warn(f"chezmoi secret scan hit; switching to encrypted: {target}")
            make_private(f, run.dry)
            rc = run.cz("add", "--encrypt", "--", str(f))
        else:
            ui.info(f"  plain    {target}")
            rc = 0
    if rc != 0:
        ui.warn(f"chezmoi add failed: {target}")
        return
    if not run.dry and not chez.is_managed(f):
        ui.warn(f"not managed (ignored by .chezmoiignore?): {target}")
        return
    if chez.register_platform(target, platforms.OS_NAME, run.dry):
        ui.info(f"        registered platform owner: {platforms.OS_NAME}")


def backup_candidates(run: Runner) -> None:
    ui.section("Add candidate configs")
    prof = profile()
    managed = {os.path.normcase(p) for p in chez.managed(include="files,symlinks", absolute=True)}
    for kind, rels in (("secret", prof.secret), ("auto", prof.track)):
        for rel in rels:
            for f in expand(rel, prof):
                add_candidate(run, f, kind, managed)


# ---------------------------------------------------------------- package manifests

def package_files() -> list[str]:
    return ["scoopfile.json", "winget.json", "winget-unavailable.txt"] if IS_WINDOWS else ["archpkg.txt"]


def normalize_export(kind: str, data: dict) -> str:
    """Normalize exports so manifests only change when packages change: drop scoop fields that
    change every run (Updated, ...), drop winget's CreationDate."""
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
        ui.ok(f"{name}: no changes")
        return
    before, after = _names(old, kind), _names(new, kind)
    diff = [f"  -{n}" for n in before if n not in after] + [f"  +{n}" for n in after if n not in before]
    if not old:
        diff = [f"  (new file, {len(after)} packages)"]
    ui.info(f"{name}:")
    for line in diff or ["  package set unchanged, metadata updated"]:
        ui.info(line)
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_utf8(path, new)


def backup_packages(dry: bool) -> None:
    ui.section("Package manifests")
    if not IS_WINDOWS:
        if platforms.have("pacman"):
            update_pkg_file("archpkg.txt", "arch", platforms.run(["pacman", "-Qqe"]).stdout, dry)
        return
    if platforms.have("scoop"):
        r = platforms.run(["scoop", "export"], timeout=300)
        try:
            update_pkg_file("scoopfile.json", "scoop", normalize_export("scoop", json.loads(r.stdout)), dry)
        except (ValueError, KeyError) as e:
            ui.warn(f"scoop export failed (exit {r.returncode}): {e}")
    if platforms.have("winget"):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "winget.json"
            r = platforms.run(["winget", "export", "-o", str(raw), "--accept-source-agreements",
                             "--disable-interactivity"], timeout=600)
            if not raw.is_file():
                ui.warn(f"winget export failed (exit {r.returncode})")
                return
            data = json.loads(raw.read_text(encoding="utf-8-sig"))
        update_pkg_file("winget.json", "winget", normalize_export("winget", data), dry)
        unavailable = sorted(set(re.findall(r"not available from any source:\s*(.+?)\s*$", r.stdout, re.M)))
        text = ("# Installed programs winget export could not export (not in any winget source); install manually\n"
                + "".join(f"{u}\n" for u in unavailable))
        path = chez.packages_dir() / "winget-unavailable.txt"
        if (path.read_text(encoding="utf-8") if path.is_file() else "") != text:
            ui.info(f"winget-unavailable.txt: {len(unavailable)} entries")
            if not dry:
                _write_utf8(path, text)


# ---------------------------------------------------------------- check

def gate() -> bool:
    ui.section("Security check")
    bad = False
    for i in chez.age_identities():
        if chez.tracked(i):
            ui.err(f"age private key is tracked by git: {i}")
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
        ui.err(f"platform ownership: {p}")
    if problems:
        bad = True
    for p in dotconf.validate():
        ui.err(f"dotfiles.toml: {p}")
        bad = True
    if bad:
        ui.err("security check failed; nothing will be committed")
        return False
    ui.ok("no plaintext secrets, .age files are valid ciphertext, sensitive targets encrypted, platform ownership complete, dotfiles.toml valid")
    return True


def cmd_check() -> int:
    preflight()
    return 0 if gate() else 1


def cmd_backup(dry: bool, with_system: bool, commit: bool) -> int:
    preflight()
    run = Runner(dry)
    backup_managed(run)
    backup_candidates(run)
    if with_system and platforms.IS_LINUX:
        from dotfiles.cli import etc
        etc.backup(run)
    backup_packages(dry)
    passed = gate()
    ui.section("Repo status")
    ui.info(chez.git("status", "--short").stdout.rstrip() or "(no changes)")
    if not passed:
        return 1
    if commit and not dry:
        chez.git("add", "-A")
        if chez.git("diff", "--cached", "--quiet").ok:
            ui.ok("nothing to commit")
            return 0
        from datetime import date
        msg = f"backup: sync {platforms.OS_NAME} dotfiles ({date.today().isoformat()})"
        if platforms.run_live(["git", "-C", str(chez.repo()), "commit", "-q", "-m", msg]) != 0:
            raise ui.Abort("git commit failed (pre-commit check did not pass?)")
        ui.ok(f"committed {chez.git('log', '-1', '--format=%h').stdout.strip()} (not pushed; run git push when ready)")
    ui.info()
    ui.info("reminder: the age private key is not in the repo; back it up offline (password manager / USB drive):")
    for i in chez.age_identities():
        ui.info(f"  {i}")
    return 0

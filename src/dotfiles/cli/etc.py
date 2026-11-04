"""Backup and restore for Linux system config (/etc): a separate chezmoi instance whose source
dir is system/ at the repo top and whose destination is a staging area on tmpfs ($XDG_RUNTIME_DIR,
removed afterwards)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from dotfiles.utils import platforms, ui
from dotfiles.utils.common import chez
from dotfiles.utils.common import config as dotconf

# The lists (extra / secret / exclude / hardware_bound) live in dotfiles.toml [etc].
SUDOERS = ["/etc/sudoers", "/etc/sudoers.d/*"]   # validated with visudo before install (logic, not data)


def _list(name: str) -> list[str]:
    return list(dotconf.get(f"etc.{name}"))


def _match(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, p) for p in patterns)


class Sudo:
    ready = False
    prompt = False

    @classmethod
    def ensure(cls) -> bool:
        if cls.ready:
            return True
        if platforms.run(["sudo", "-n", "true"]).ok:
            cls.ready = True
        elif cls.prompt and os.isatty(0) and platforms.run_live(["sudo", "-v"]) == 0:
            cls.ready = True
        return cls.ready


DPKG_ADMIN = Path("/var/lib/dpkg")


def _owned_by_packages() -> set[str]:
    if platforms.have("pacman"):
        return {line for line in platforms.run(["pacman", "-Qlq"]).stdout.splitlines() if line.startswith("/etc/")}
    if platforms.have("dpkg-query"):
        return dpkg_owned(DPKG_ADMIN)
    return set()


def _modified_package_files() -> list[str]:
    """Package config files modified by the user (pacman's [modified] mark; on dpkg systems, a
    conffile whose checksum differs from the one dpkg recorded)."""
    if platforms.have("pacman"):
        r = platforms.run(["pacman", "-Qii"], env={"LC_ALL": "C"})
        return sorted(set(re.findall(r"(/etc/\S+) \[modified\]", r.stdout)))
    if platforms.have("dpkg-query"):
        return dpkg_modified(DPKG_ADMIN)
    return []


# ---------------------------------------------------------------- dpkg (Debian, Kali, ...)

def dpkg_owned(admin: Path) -> set[str]:
    """/etc paths listed by installed packages (<admin>/info/*.list, what dpkg -L prints), plus
    the diverted-to copies of stock files (e.g. /etc/zsh/zprofile.original), which no list names."""
    out: set[str] = set()
    for lst in (admin / "info").glob("*.list"):
        try:
            text = lst.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        out.update(line for line in text.splitlines() if line.startswith("/etc/"))
    try:
        div = dpkg_diversions((admin / "diversions").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        div = {}
    out.update(to for to, _ in div.values() if to.startswith("/etc/"))
    return out


def dpkg_diversions(text: str) -> dict[str, tuple[str, str]]:
    """<admin>/diversions (records of three lines: path, diverted-to path, diverting package)
    -> {path: (diverted-to path, diverting package)}."""
    lines = text.splitlines()
    return {lines[i]: (lines[i + 1], lines[i + 2]) for i in range(0, len(lines) - 2, 3)}


def dpkg_conffiles(status_text: str, diversions: dict[str, tuple[str, str]] | None = None) -> dict[str, str]:
    """Where each conffile of an installed package lives -> the md5 dpkg recorded for it. Skips
    obsolete and remove-on-upgrade entries, and packages removed but not purged (leftovers, not
    live configuration). A conffile another package diverted (Kali's kali-defaults and kali-themes
    replace stock files this way) lives at the diverted-to path; the file at the original path
    belongs to the diverting package and is not this conffile."""
    diversions = diversions or {}
    out: dict[str, str] = {}
    for stanza in re.split(r"\n\s*\n", status_text):
        status = re.search(r"^Status: (.*)$", stanza, re.M)
        if not status or not status.group(1).endswith(" installed"):
            continue
        package = re.search(r"^Package: (\S+)", stanza, re.M)
        m = re.search(r"^Conffiles:\n((?: .*\n?)*)", stanza, re.M)
        for line in (m.group(1).splitlines() if m else []):
            fields = line.split()
            if len(fields) != 2 or not fields[0].startswith("/etc/") or not re.fullmatch(r"[0-9a-f]{32}", fields[1]):
                continue
            path = fields[0]
            to, by = diversions.get(path, (path, ""))
            out[to if package and by != package.group(1) else path] = fields[1]
    return out


def _md5(path: Path) -> str | None:
    """md5 of a file; None when missing; "" when unreadable without sudo."""
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return ""


def dpkg_modified(admin: Path, root: Path = Path("/")) -> list[str]:
    """Conffiles that exist and differ from the packaged version (what debsums -ce reports).
    Root-only files are hashed with sudo when available, otherwise skipped with a warning."""
    try:
        div = (admin / "diversions").read_text(encoding="utf-8", errors="replace") if (admin / "diversions").is_file() else ""
        recorded = dpkg_conffiles((admin / "status").read_text(encoding="utf-8", errors="replace"), dpkg_diversions(div))
    except OSError:
        return []
    modified, unreadable = [], []
    for path, md5 in recorded.items():
        actual = _md5(root / path.lstrip("/"))
        if actual == "":
            unreadable.append(path)
        elif actual is not None and actual != md5:
            modified.append(path)
    if unreadable and root == Path("/") and Sudo.ensure():
        r = platforms.run(["sudo", "md5sum", "--", *unreadable])
        for line in r.stdout.splitlines():
            digest, _, path = line.partition("  ")
            if path in recorded and digest != recorded[path]:
                modified.append(path)
    elif unreadable:
        ui.warn(f"{len(unreadable)} root-only conffiles were not checked (needs sudo)")
    return sorted(modified)


# ---------------------------------------------------------------- files identical to the package's

PACMAN_DB = Path("/var/lib/pacman")


def pacman_backups(db: Path) -> dict[str, str]:
    """The md5 pacman recorded for every backup file (its conffiles): <db>/local/*/files, section
    %BACKUP%, lines `etc/foo.conf<TAB><md5>`. This is what pacman -Qii compares `[modified]` with."""
    out: dict[str, str] = {}
    for files in (db / "local").glob("*/files"):
        try:
            lines = files.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
        except OSError:
            continue
        if "%BACKUP%" not in lines:
            continue
        for line in lines[lines.index("%BACKUP%") + 1:]:
            if not line.strip():
                break
            name, _, digest = line.partition("\t")
            if re.fullmatch(r"[0-9a-f]{32}", digest):
                out["/" + name] = digest
    return out


def dpkg_digests(admin: Path, wanted: set[str]) -> dict[str, str]:
    """The md5 dpkg recorded for these files: conffiles from <admin>/status, others from
    <admin>/info/<package>.md5sums (`<md5>  <path without the leading />`)."""
    try:
        div = (admin / "diversions").read_text(encoding="utf-8", errors="replace") if (admin / "diversions").is_file() else ""
        out = {p: d for p, d in dpkg_conffiles((admin / "status").read_text(encoding="utf-8", errors="replace"),
                                               dpkg_diversions(div)).items() if p in wanted}
    except OSError:
        out = {}
    rest = {p[1:] for p in wanted - out.keys()}
    if rest:
        for sums in (admin / "info").glob("*.md5sums"):
            try:
                text = sums.read_text(encoding="utf-8", errors="surrogateescape")
            except OSError:
                continue
            for line in text.splitlines():
                digest, _, name = line.partition("  ")
                if name in rest and re.fullmatch(r"[0-9a-f]{32}", digest):
                    out["/" + name] = digest
    return out


def package_original(paths, root: Path = Path("/")) -> set[str]:
    """Of these /etc files, the ones verified to be exactly what their package installed. Only a
    verified match counts: no record (not a package file, or pacman does not track it), a missing
    file, or a root-only file that cannot be hashed (no sudo) all leave a file out."""
    wanted = set(paths)
    if not wanted:
        return set()
    if platforms.have("pacman"):
        recorded = {p: d for p, d in pacman_backups(PACMAN_DB).items() if p in wanted}
    elif platforms.have("dpkg-query"):
        recorded = dpkg_digests(DPKG_ADMIN, wanted)
    else:
        return set()
    original, unreadable = set(), []
    for path, digest in recorded.items():
        actual = _md5(root / path.lstrip("/"))
        if actual == "":
            unreadable.append(path)
        elif actual == digest:
            original.add(path)
    if unreadable and root == Path("/") and Sudo.ensure():
        for line in platforms.run(["sudo", "md5sum", "--", *unreadable]).stdout.splitlines():
            digest, _, path = line.partition("  ")
            if recorded.get(path) == digest:
                original.add(path)
    return original


def _files_under(path: str) -> list[str]:
    p = Path(path)
    if p.is_file() and not p.is_symlink():
        return [path]
    if p.is_dir():
        if os.access(p, os.R_OK | os.X_OK):
            return sorted(str(f) for f in p.rglob("*") if f.is_file() and not f.is_symlink() and f.stat().st_size < 1024 * 1024)
        if Sudo.ensure():
            return sorted(platforms.run(["sudo", "find", path, "-type", "f", "-size", "-1024k"]).stdout.split("\n")[:-1])
        ui.warn(f"cannot list {path} (needs sudo)")
        return []
    if not p.exists() and Sudo.ensure() and platforms.run(["sudo", "test", "-f", path]).ok:
        return [path]
    return []


def collect_targets() -> dict[str, str]:
    """path -> plain | secret. Files identical to their package's version are left out."""
    owned = _owned_by_packages()
    exclude = _list("exclude")
    out: dict[str, str] = {}
    for f in _modified_package_files():
        if not _match(f, exclude):
            out.setdefault(f, "plain")
    for p in _list("extra"):
        is_dir = Path(p).is_dir()
        for f in _files_under(p):
            if (is_dir and f in owned) or _match(f, exclude):
                continue
            out.setdefault(f, "plain")
    for p in _list("secret"):
        for f in _files_under(p):
            if not _match(f, exclude):
                out[f] = "secret"
    # a listed file that is exactly the package's own is restored by the package: nothing to back up
    for f in package_original(out):
        del out[f]
    return dict(sorted(out.items()))


DPKG_LEFTOVERS = (".dpkg-dist", ".dpkg-new", ".dpkg-old", ".ucf-dist", ".ucf-new", ".ucf-old")
# rewritten by update-ca-certificates on every run: its .dpkg-old is an old generated copy, nothing to merge
TOOL_GENERATED = {"/etc/ca-certificates.conf"}


def find_leftovers(root: Path, suffixes: tuple[str, ...], skip_bases: set[str]) -> tuple[list[str], int]:
    """Files under root ending in one of the suffixes -> (the ones worth a look, how many were
    left out). Left out: leftovers of a path in skip_bases (stock files another package diverted:
    Kali's kali-themes / kali-menu keep the original there; files a tool regenerates), which are
    never your edit to merge."""
    found, hidden = [], 0
    for dirpath, _, files in sorted(os.walk(root, onerror=lambda e: None)):
        for name in sorted(files):
            if not name.endswith(suffixes):
                continue
            path = os.path.join(dirpath, name)
            if path.rsplit(".", 1)[0] in skip_bases:
                hidden += 1
            else:
                found.append(path)
    return found, hidden


def status() -> None:
    ui.section("/etc backup targets")
    for path, kind in collect_targets().items():
        ui.info(f"  {ui.pad(path, 56)}{kind}")
    ui.section("/etc pending items")
    skip: set[str] = set()
    if platforms.have("pacman"):
        ui.info("pacnew / pacsave (merge with pacdiff):")
        suffixes: tuple[str, ...] = (".pacnew", ".pacsave")
    else:
        ui.info("dpkg / ucf leftovers (new or old package versions of a conffile you changed; merge by hand):")
        suffixes = DPKG_LEFTOVERS
        try:
            skip = set(dpkg_diversions((DPKG_ADMIN / "diversions").read_text(encoding="utf-8", errors="replace"))) | TOOL_GENERATED
        except OSError:
            skip = set(TOOL_GENERATED)
    found, hidden = find_leftovers(Path("/etc"), suffixes, skip)
    for path in found:
        ui.info(f"  {path}")
    if not found:
        ui.info("  none")
    if hidden:
        ui.dim(f"  ({hidden} more not listed: stock files another package diverted, or copies of a file a tool regenerates)")
    if platforms.have("etckeeper") and not Path("/etc/.git").exists():
        ui.info("etckeeper is installed but not initialized: /etc is backed up in system/; consider removing etckeeper to avoid duplication")


class _Stage:
    def __init__(self, source: Path | None = None):
        self.source = source or chez.system_source_dir()
        self.dir = Path(tempfile.mkdtemp(prefix="chezmoi-etc.", dir=platforms.runtime_dir()))
        os.chmod(self.dir, 0o700)
        self.state = self.dir.with_name(self.dir.name + ".boltdb")

    def cz(self, *args: str) -> platforms.Result:
        return platforms.run(["chezmoi", "--source", str(self.source), "--destination", str(self.dir),
                            "--persistent-state", str(self.state), *args])

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        try:
            self.state.unlink()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def mode_attrs(mode: int) -> set[str]:
    """The attributes `chezmoi add` records for a file with this mode."""
    attrs = set()
    if not mode & 0o077:
        attrs.add("private")
    if not mode & 0o222:
        attrs.add("readonly")
    if mode & 0o111:
        attrs.add("executable")
    return attrs


def unchanged(stage: _Stage, dst: Path, encrypt: bool) -> bool:
    """The staged copy already matches system/<distro>: same content (decrypted) and the same
    attributes `chezmoi add` would record now (encryption, private, readonly, executable).
    Re-adding it anyway would only churn the repo: age output differs on every encryption.

    `chezmoi status` is not used for this: it also compares modes the attributes cannot express
    (0440, 0640), and modes derived from the umask (0644 is 0664 under Kali's umask 002), so it
    reported such files as changed on every backup."""
    from dotfiles.cli.backup import source_attrs

    src = stage.cz("source-path", "--", str(dst))
    if not src.ok:
        return False
    want = mode_attrs(stat.S_IMODE(dst.stat().st_mode)) | ({"encrypted"} if encrypt else set())
    have = source_attrs(Path(src.stdout.strip()).name) & {"encrypted", "private", "readonly", "executable"}
    if want != have:
        return False
    r = stage.cz("cat", "--", str(dst))
    return r.ok and r.stdout == platforms._decode(dst.read_bytes())


def backup(run) -> None:
    ui.section("/etc -> system/ (separate chezmoi instance)")
    Sudo.prompt = True
    chez.system_source_dir().mkdir(parents=True, exist_ok=True)
    from dotfiles.cli.backup import has_secret

    n = changed = 0
    with _Stage() as stage:
        targets = collect_targets()
        for path, kind in targets.items():
            dst = stage.dir / path.lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if os.access(path, os.R_OK):
                mode = stat.S_IMODE(os.stat(path).st_mode)
                shutil.copyfile(path, dst)
            elif Sudo.ensure():
                mode = int(platforms.run(["sudo", "stat", "-c", "%a", path]).stdout.strip() or "600", 8)
                old = os.umask(0o077)
                try:
                    data = platforms.run(["sudo", "cat", "--", path])
                    if not data.ok:
                        ui.warn(f"failed to read: {path}")
                        continue
                    dst.write_text(data.stdout, encoding="utf-8")
                finally:
                    os.umask(old)
            else:
                ui.warn(f"skipped (no read permission and no sudo): {path}")
                continue
            os.chmod(dst, mode)
            # /etc files that are not world-readable count as sensitive too
            encrypt = kind == "secret" or not (mode & 0o004) or has_secret(dst)
            n += 1
            if unchanged(stage, dst, encrypt):
                continue
            flags = run.flags
            if encrypt:
                ui.info(f"  encrypt  {path}")
                stage.cz(*flags, "add", "--encrypt", "--", str(dst))
            else:
                r = stage.cz(*flags, "add", "--secrets", "error", "--", str(dst))
                if r.ok:
                    ui.info(f"  plain    {path}")
                else:
                    ui.warn(f"chezmoi secret scan hit; switching to encrypted: {path}")
                    stage.cz(*flags, "add", "--encrypt", "--", str(dst))
            changed += 1
        dropped = _drop_stale(stage, run, targets)
    ui.ok(f"processed {n} /etc files, {changed} new or changed, {dropped} dropped "
          "(staging area was on tmpfs; now removed)")


def _drop_stale(stage: _Stage, run, targets: dict[str, str]) -> int:
    """Entries in system/<distro> that this run no longer collects. A file that is now exactly its
    package's own is removed (the package restores it); any other entry stays, with a note: it may
    be excluded or deleted on purpose, or just unreadable without sudo."""
    r = stage.cz("managed", "--include", "files", "--path-style", "absolute")
    prefix = str(stage.dir)
    stale = sorted("/" + t[len(prefix):].lstrip("/") for t in r.stdout.splitlines() if t.startswith(prefix))
    stale = [t for t in stale if t not in targets]
    original = package_original(stale)
    dropped = 0
    for path in stale:
        if path not in original:
            ui.note(f"in system/ but not collected this run (excluded, deleted, or unreadable without sudo): {path}")
            continue
        ui.info(f"  drop     {path} (identical to the package's version)")
        dst = stage.dir / path.lstrip("/")
        if stage.cz(*run.flags, "forget", "--force", "--", str(dst)).ok:
            dropped += 1
            src = stage.source
            for parent in sorted({p for p in src.rglob("*") if p.is_dir()}, key=lambda p: len(p.parts), reverse=True):
                if not any(parent.iterdir()):
                    parent.rmdir()
    return dropped


def available_distros() -> list[str]:
    """Distros that have an /etc backup under system/."""
    root = chez.system_root()
    return sorted(d.name for d in root.iterdir() if (d / "etc").is_dir()) if root.is_dir() else []


def cmd_restore(apply: bool, filters: list[str], distro: str | None = None) -> int:
    source = chez.system_source_dir(distro)
    if not (source / "etc").is_dir():
        have = ", ".join(available_distros()) or "none"
        raise ui.Abort(f"no /etc backup for {source.name} in system/ (available: {have}); "
                       "pick one with --distro, or run dotfiles backup on this distro first")
    from dotfiles.cli.backup import preflight

    preflight()
    Sudo.prompt = True
    rsudo = ["sudo"] if Sudo.ensure() else []
    if apply and not rsudo:
        raise ui.Abort("installing into /etc needs sudo")
    changed = held = 0
    ui.info(f"source: system/{source.name}")
    with _Stage(source) as stage:
        r = stage.cz("apply", "--force", "--no-tty")
        if not r.ok:
            raise ui.Abort(f"failed to decrypt system/: {r.stderr.strip()[:300]}")
        encrypted = {p for p in stage.cz("managed", "-i", "encrypted", "-p", "absolute").stdout.splitlines()}
        for f in sorted(p for p in stage.dir.rglob("*") if p.is_file()):
            target = "/" + f.relative_to(stage.dir).as_posix()
            if filters and not any(target == p or target.startswith(p.rstrip("/") + "/") for p in filters):
                continue
            if platforms.run([*rsudo, "cmp", "-s", "--", str(f), target]).ok:
                continue
            changed += 1
            ui.section(target)
            if str(f) in encrypted:
                ui.info("(encrypted file; the content diff is not shown in the terminal)")
            else:
                ui.info(platforms.run([*rsudo, "diff", "-u", "--label", target, "--label", "backup", "--", target, str(f)]).stdout.rstrip())
            if _match(target, _list("hardware_bound")) and target not in filters:
                ui.note("bound to hardware/disk layout: diff only; to install, run dotfiles system-restore --apply " + target)
                held += 1
                continue
            if _match(target, SUDOERS) and not platforms.run([*rsudo, "visudo", "-cf", str(f)]).ok:
                ui.err("visudo validation failed; not installing (a broken sudoers would lock you out of sudo)")
                continue
            mode = platforms.run([*rsudo, "stat", "-c", "%a", "--", target]).stdout.strip() or f"{stat.S_IMODE(f.stat().st_mode):o}"
            cmd = ["sudo", "install", "-D", "-o", "root", "-g", "root", "-m", mode, "--", str(f), target]
            if not apply:
                ui.dim("  + " + " ".join(cmd))
            elif platforms.run_live(cmd) != 0:
                ui.warn(f"install failed: {target}")
    if not changed:
        ui.ok("/etc matches the backup")
    elif not apply:
        regen = "grub-mkconfig / mkinitcpio -P" if platforms.have("pacman") else "update-grub / update-initramfs -u"
        ui.info(f"this was a preview; add --apply to install. After changing boot/initramfs files, run {regen}")
    if held:
        ui.note(f"{held} hardware-bound files were not installed")
    return 0

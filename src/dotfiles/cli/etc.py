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
    """path -> plain | secret."""
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
    return dict(sorted(out.items()))


def status() -> None:
    ui.section("/etc backup targets")
    for path, kind in collect_targets().items():
        ui.info(f"  {ui.pad(path, 56)}{kind}")
    ui.section("/etc pending items")
    if platforms.have("pacman"):
        ui.info("pacnew / pacsave (merge with pacdiff):")
        suffixes: tuple[str, ...] = (".pacnew", ".pacsave")
    else:
        ui.info("dpkg / ucf leftovers (new or old package versions of a conffile you changed; merge by hand):")
        suffixes = (".dpkg-dist", ".dpkg-new", ".dpkg-old", ".ucf-dist", ".ucf-new", ".ucf-old")
    for dirpath, _, files in sorted(os.walk("/etc", onerror=lambda e: None)):
        for name in sorted(files):
            if name.endswith(suffixes):
                ui.info(f"  {os.path.join(dirpath, name)}")
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
        for path, kind in collect_targets().items():
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
    ui.ok(f"processed {n} /etc files, {changed} new or changed (staging area was on tmpfs; now removed)")


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

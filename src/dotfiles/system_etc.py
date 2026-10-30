"""Backup and restore for Linux system config (/etc): a separate chezmoi instance whose source
dir is system/ at the repo top and whose destination is a staging area on tmpfs ($XDG_RUNTIME_DIR,
removed afterwards)."""
from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from dotfiles import chez, sysinfo, ui

# /etc files or directories included explicitly (directories contribute only files not owned by any package)
ETC_EXTRA = [
    "/etc/docker/daemon.json", "/etc/timeshift/timeshift.json",
    "/etc/sddm.conf", "/etc/sddm.conf.d", "/etc/mkinitcpio.d/linux-zen.preset",
    "/etc/locale.conf", "/etc/vconsole.conf", "/etc/hostname", "/etc/zsh/zshrc", "/etc/environment",
    "/etc/pacman.d/hooks", "/etc/systemd/system", "/etc/systemd/journald.conf.d",
    "/etc/systemd/logind.conf.d", "/etc/modprobe.d", "/etc/sysctl.d", "/etc/udev/rules.d",
    "/etc/X11/xorg.conf.d", "/etc/NetworkManager/conf.d", "/etc/environment.d",
]
# /etc paths that are always encrypted (usually root-readable, needs sudo)
ETC_SECRET = [
    "/etc/NetworkManager/system-connections", "/etc/crypttab", "/etc/sudoers", "/etc/sudoers.d",
    "/etc/mihomo/config.yaml", "/etc/wireguard",
]
# Never backed up: account databases, host keys, generated files; restoring them does more harm than good
ETC_EXCLUDE = [
    "/etc/passwd", "/etc/group", "/etc/shadow", "/etc/gshadow", "/etc/subuid", "/etc/subgid",
    "/etc/machine-id", "/etc/resolv.conf", "/etc/ssh/ssh_host_*",
    "*.pacnew", "*.pacsave", "*.bak", "*.bak.*", "*~",
]
# Bound to hardware / disk layout on restore: show the diff only, install only when named explicitly
HARDWARE_BOUND = [
    "/etc/fstab", "/etc/crypttab", "/etc/default/grub", "/etc/mkinitcpio.conf", "/etc/mkinitcpio.d/*",
    "/etc/X11/xorg.conf.d/*", "/etc/modprobe.d/*",
]
SUDOERS = ["/etc/sudoers", "/etc/sudoers.d/*"]


def _match(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, p) for p in patterns)


class Sudo:
    ready = False
    prompt = False

    @classmethod
    def ensure(cls) -> bool:
        if cls.ready:
            return True
        if sysinfo.run(["sudo", "-n", "true"]).ok:
            cls.ready = True
        elif cls.prompt and os.isatty(0) and sysinfo.run_live(["sudo", "-v"]) == 0:
            cls.ready = True
        return cls.ready


def _owned_by_packages() -> set[str]:
    if not sysinfo.have("pacman"):
        return set()
    return {line for line in sysinfo.run(["pacman", "-Qlq"]).stdout.splitlines() if line.startswith("/etc/")}


def _modified_package_files() -> list[str]:
    """Package config files modified by the user (pacman's [modified] mark)."""
    if not sysinfo.have("pacman"):
        return []
    r = sysinfo.run(["pacman", "-Qii"], env={"LC_ALL": "C"})
    return sorted(set(re.findall(r"(/etc/\S+) \[modified\]", r.stdout)))


def _files_under(path: str) -> list[str]:
    p = Path(path)
    if p.is_file() and not p.is_symlink():
        return [path]
    if p.is_dir():
        if os.access(p, os.R_OK | os.X_OK):
            return sorted(str(f) for f in p.rglob("*") if f.is_file() and not f.is_symlink() and f.stat().st_size < 1024 * 1024)
        if Sudo.ensure():
            return sorted(sysinfo.run(["sudo", "find", path, "-type", "f", "-size", "-1024k"]).stdout.split("\n")[:-1])
        ui.warn(f"cannot list {path} (needs sudo)")
        return []
    if not p.exists() and Sudo.ensure() and sysinfo.run(["sudo", "test", "-f", path]).ok:
        return [path]
    return []


def collect_targets() -> dict[str, str]:
    """path -> plain | secret."""
    owned = _owned_by_packages()
    out: dict[str, str] = {}
    for f in _modified_package_files():
        if not _match(f, ETC_EXCLUDE):
            out.setdefault(f, "plain")
    for p in ETC_EXTRA:
        is_dir = Path(p).is_dir()
        for f in _files_under(p):
            if (is_dir and f in owned) or _match(f, ETC_EXCLUDE):
                continue
            out.setdefault(f, "plain")
    for p in ETC_SECRET:
        for f in _files_under(p):
            if not _match(f, ETC_EXCLUDE):
                out[f] = "secret"
    return dict(sorted(out.items()))


def status() -> None:
    ui.section("/etc backup targets")
    for path, kind in collect_targets().items():
        ui.info(f"  {ui.pad(path, 56)}{kind}")
    ui.section("/etc pending items")
    ui.info("pacnew / pacsave (merge with pacdiff):")
    for dirpath, _, files in sorted(os.walk("/etc", onerror=lambda e: None)):
        for name in sorted(files):
            if name.endswith((".pacnew", ".pacsave")):
                ui.info(f"  {os.path.join(dirpath, name)}")
    if sysinfo.have("etckeeper") and not Path("/etc/.git").exists():
        ui.info("etckeeper is installed but not initialized: /etc is backed up in system/; consider removing etckeeper to avoid duplication")


class _Stage:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="chezmoi-etc.", dir=sysinfo.runtime_dir()))
        os.chmod(self.dir, 0o700)
        self.state = self.dir.with_name(self.dir.name + ".boltdb")

    def cz(self, *args: str) -> sysinfo.Result:
        return sysinfo.run(["chezmoi", "--source", str(chez.system_source_dir()), "--destination", str(self.dir),
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


def backup(run) -> None:
    ui.section("/etc -> system/ (separate chezmoi instance)")
    Sudo.prompt = True
    chez.system_source_dir().mkdir(exist_ok=True)
    from dotfiles.backup import has_secret

    n = 0
    with _Stage() as stage:
        for path, kind in collect_targets().items():
            dst = stage.dir / path.lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if os.access(path, os.R_OK):
                mode = stat.S_IMODE(os.stat(path).st_mode)
                shutil.copyfile(path, dst)
            elif Sudo.ensure():
                mode = int(sysinfo.run(["sudo", "stat", "-c", "%a", path]).stdout.strip() or "600", 8)
                old = os.umask(0o077)
                try:
                    data = sysinfo.run(["sudo", "cat", "--", path])
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
            n += 1
    ui.ok(f"processed {n} /etc files (staging area was on tmpfs; now removed)")


def cmd_restore(apply: bool, filters: list[str]) -> int:
    src = chez.system_source_dir() / "etc"
    if not src.is_dir():
        raise ui.Abort("system/etc does not exist: dotfiles backup (without --no-system) has not run on Linux yet")
    from dotfiles.backup import preflight

    preflight()
    Sudo.prompt = True
    rsudo = ["sudo"] if Sudo.ensure() else []
    if apply and not rsudo:
        raise ui.Abort("installing into /etc needs sudo")
    changed = held = 0
    with _Stage() as stage:
        r = stage.cz("apply", "--force", "--no-tty")
        if not r.ok:
            raise ui.Abort(f"failed to decrypt system/: {r.stderr.strip()[:300]}")
        encrypted = {p for p in stage.cz("managed", "-i", "encrypted", "-p", "absolute").stdout.splitlines()}
        for f in sorted(p for p in stage.dir.rglob("*") if p.is_file()):
            target = "/" + f.relative_to(stage.dir).as_posix()
            if filters and not any(target == p or target.startswith(p.rstrip("/") + "/") for p in filters):
                continue
            if sysinfo.run([*rsudo, "cmp", "-s", "--", str(f), target]).ok:
                continue
            changed += 1
            ui.section(target)
            if str(f) in encrypted:
                ui.info("(encrypted file; the content diff is not shown in the terminal)")
            else:
                ui.info(sysinfo.run([*rsudo, "diff", "-u", "--label", target, "--label", "backup", "--", target, str(f)]).stdout.rstrip())
            if _match(target, HARDWARE_BOUND) and target not in filters:
                ui.note("bound to hardware/disk layout: diff only; to install, run dotfiles system-restore --apply " + target)
                held += 1
                continue
            if _match(target, SUDOERS) and not sysinfo.run([*rsudo, "visudo", "-cf", str(f)]).ok:
                ui.err("visudo validation failed; not installing (a broken sudoers would lock you out of sudo)")
                continue
            mode = sysinfo.run([*rsudo, "stat", "-c", "%a", "--", target]).stdout.strip() or f"{stat.S_IMODE(f.stat().st_mode):o}"
            cmd = ["sudo", "install", "-D", "-o", "root", "-g", "root", "-m", mode, "--", str(f), target]
            if not apply:
                ui.dim("  + " + " ".join(cmd))
            elif sysinfo.run_live(cmd) != 0:
                ui.warn(f"install failed: {target}")
    if not changed:
        ui.ok("/etc matches the backup")
    elif not apply:
        ui.info("this was a preview; add --apply to install. After changing boot/initramfs files, run grub-mkconfig / mkinitcpio -P")
    if held:
        ui.note(f"{held} hardware-bound files were not installed")
    return 0

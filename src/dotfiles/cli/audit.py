"""dotfiles audit: permissions of sensitive files (Linux 600/700, Windows ACL limited to the user,
SYSTEM and Administrators)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from dotfiles.utils import platforms, ui
from dotfiles.utils.common import chez
from dotfiles.utils.common import config as dotconf
from dotfiles.utils.fsutil import tilde
from dotfiles.utils.platforms import IS_WINDOWS, home
from dotfiles.utils.platforms import windows as win


def _secret_list() -> list[str]:
    return list(dotconf.get(f"backup.{platforms.OS_NAME}.secret"))



class _Audit:
    hits = 0


def _audit_targets() -> list[tuple[Path, bool]]:
    """(path, is_dir): always-encrypted configs, credential files, ~/.ssh, ~/.gnupg, age identity files and their dirs."""
    h = home()
    out: list[tuple[Path, bool]] = []
    for rel in [*_secret_list(), ".pypirc", ".netrc", ".git-credentials", ".ssh/config"]:
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
        ui.info(f"  {ui.pad(tilde(path), 50)}{cur:o} -> {want:o}")
        _Audit.hits += 1
        if apply:
            os.chmod(path, want)


def _audit_windows(path: Path, is_dir: bool, apply: bool) -> None:
    script = ("(Get-Acl -LiteralPath $env:DOTFILES_AUDIT_PATH).Access | Where-Object AccessControlType -eq 'Allow' | "
              "ForEach-Object { try { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } "
              "catch { $_.IdentityReference.Value } }")
    os.environ["DOTFILES_AUDIT_PATH"] = str(path)
    try:
        sids = set(win.powershell(script).stdout.split())
    finally:
        os.environ.pop("DOTFILES_AUDIT_PATH", None)
    allowed = {win.current_sid(), *win.ALLOWED_SIDS_EXTRA}
    extra = sorted(sids - allowed)
    if not extra:
        return
    ui.info(f"  {ui.pad(tilde(path), 50)}also accessible by: {', '.join(extra)}")
    _Audit.hits += 1
    if apply and not win.restrict_acl(path, is_dir):
        ui.warn(f"icacls failed: {path}")


def audit(apply: bool) -> None:
    _Audit.hits = 0
    for path, is_dir in _audit_targets():
        (_audit_windows if IS_WINDOWS else _audit_posix)(path, is_dir, apply)
    if _Audit.hits == 0:
        ui.ok("sensitive file permissions are fine")
    elif not apply:
        ui.info(f"{_Audit.hits} items have overly wide permissions; run dotfiles audit --apply to tighten them")
    else:
        ui.ok(f"tightened {_Audit.hits} items")


def cmd_audit(apply: bool) -> int:
    ui.section("Permission audit")
    audit(apply)
    return 0

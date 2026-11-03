"""Deployment primitives (repo -> ~) shared by `dotfiles apply` and the dotfiles step of `dotfiles
restore`: pending changes, a safe preview, a full rehearsal, a backup of files about to be
overwritten or removed (.chezmoiremove), and the apply itself. Externals are never part of it (restore has a step for them).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotfiles.utils import platforms, ui
from dotfiles.utils.common import chez
from dotfiles.utils.platforms import home


class DeployError(ui.Abort):
    pass


@dataclass(frozen=True)
class Change:
    local: str     # first column of chezmoi status: the live file changed since chezmoi last wrote it
    action: str    # second column: what apply will do (A added, D deleted, M modified, R script runs)
    target: str    # relative to ~, with /

    @property
    def overwrites_local_edit(self) -> bool:
        return self.local != " " and self.action == "M"


def pending(targets: list[Path] | None = None) -> list[Change]:
    """What `chezmoi apply` would change, optionally limited to some targets."""
    r = chez.cz("status", "--exclude", "externals", *([] if not targets else ["--", *map(str, targets)]))
    if not r.ok:
        raise DeployError(f"chezmoi status failed (cannot decrypt?): {r.stderr.strip()[:300]}")
    changes = []
    for line in r.stdout.splitlines():
        if len(line) > 3 and line[1] != " ":
            changes.append(Change(line[0], line[1], line[3:]))
    return changes


def resolve(args: list[str]) -> list[Path]:
    """Command-line targets -> absolute paths inside ~ that chezmoi manages."""
    out = []
    for a in args:
        p = Path(a).expanduser()
        p = p if p.is_absolute() else Path.cwd() / p
        try:
            p.relative_to(home())
        except ValueError:
            raise DeployError(f"not under the home directory: {a}", 2) from None
        if not chez.is_managed(p):
            raise DeployError(f"not managed by chezmoi: {a}", 2)
        out.append(p)
    return out


def preview(changes: list[Change]) -> None:
    """Plaintext targets show their diff; encrypted targets show one line, never decrypted content."""
    encrypted = set(chez.managed(include="encrypted", exclude="externals"))
    plain = []
    for c in changes:
        mark = "!" if c.overwrites_local_edit else " "
        kind = "encrypted" if c.target in encrypted else ""
        ui.info(f"  {c.action}{mark} {ui.pad(c.target, 56)}{kind}")
        if c.target not in encrypted and c.action == "M":
            plain.append(c.target)
    if any(c.overwrites_local_edit for c in changes):
        ui.warn("! = the local file was edited since the last apply; applying overwrites it (a copy is kept)."
                " To keep the edit instead: dotfiles backup, or chezmoi merge <target>")
    if plain:
        r = chez.cz("diff", "--exclude", "externals", "--", *(str(home() / t) for t in plain))
        if r.stdout.strip():
            ui.section("Diff of plaintext targets (- local, + after apply)")
            print(r.stdout.rstrip("\n"))


def rehearse(targets: list[Path] | None = None) -> None:
    """A full dry run first: if decryption or a template fails, nothing has been written yet.

    --force matches the real apply below. Without it, chezmoi asks before overwriting a file edited
    since the last apply, and under --no-tty that prompt reads EOF and fails the rehearsal, so any
    locally edited target would block the apply (and its pre-apply backup)."""
    r = chez.cz("apply", "--dry-run", "--force", "--exclude", "externals", "--no-tty", *([] if not targets else ["--", *map(str, targets)]))
    if not r.ok:
        raise DeployError(f"rehearsal failed, nothing was written: {r.stderr.strip()[:300]}")


def save_overwritten(changes: list[Change]) -> Path | None:
    """Copy live files that apply will modify or remove (M / D) to
    ~/.local/state/dotfiles/pre-apply-<time>/. A removed directory is copied whole (symlinks kept)."""
    paths = [home() / c.target for c in changes if c.action in ("M", "D")]
    paths = [p for p in paths if not p.is_symlink() and (p.is_file() or p.is_dir())]
    if not paths:
        return None
    dest = platforms.xdg_state() / "dotfiles" / f"pre-apply-{datetime.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, mode=0o700)
    for p in paths:
        rel = p.relative_to(home())
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        if p.is_dir():
            shutil.copytree(p, dest / rel, symlinks=True)
        else:
            shutil.copy2(p, dest / rel)
    return dest


def deploy(targets: list[Path] | None = None, changes: list[Change] | None = None) -> Path | None:
    """rehearse -> save files about to be overwritten or removed -> apply. Returns the backup dir, if any."""
    changes = pending(targets) if changes is None else changes
    rehearse(targets)
    saved = save_overwritten(changes)
    if saved:
        n = sum(1 for c in changes if c.action in ("M", "D"))
        ui.info(f"  {n} items about to be overwritten or removed were backed up to {saved}")
    args = ["apply", "--exclude", "externals", "--force", "--no-tty"]
    if chez.cz_live(*args, *([] if not targets else ["--", *map(str, targets)])) != 0:
        raise DeployError("chezmoi apply failed")
    return saved

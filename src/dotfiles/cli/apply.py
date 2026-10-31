"""dotfiles apply: deploy the repo's source state into ~ (the reverse of `dotfiles backup`).

Preview by default: pending changes, plaintext diffs, and which local edits would be overwritten
(encrypted targets are listed, never decrypted on screen). --apply rehearses the whole apply first,
copies files about to be overwritten to ~/.local/state/dotfiles/pre-apply-*, then applies.
Targets may be limited to some paths; externals and /etc are separate (restore / system-restore).
"""
from __future__ import annotations

from dotfiles.utils import ui
from dotfiles.utils.common import chez, deploy


def cmd_apply(targets: list[str], apply: bool) -> int:
    chez.require_chezmoi()
    paths = deploy.resolve(targets) if targets else None
    changes = deploy.pending(paths)
    ui.section("Pending changes (repo -> ~)")
    if not changes:
        ui.ok("nothing to deploy: the target state matches the repo")
        return 0
    deploy.preview(changes)
    if not apply:
        ui.info()
        ui.info(f"{len(changes)} pending; this was a preview. Add --apply to deploy.")
        return 0
    ui.section("Deploy")
    deploy.deploy(paths, changes)
    left = deploy.pending(paths)
    if left:
        ui.warn(f"{len(left)} items still differ after apply (scripts, or files changed meanwhile): "
                + ", ".join(c.target for c in left[:5]))
        return 1
    ui.ok(f"deployed {len(changes)} items")
    return 0

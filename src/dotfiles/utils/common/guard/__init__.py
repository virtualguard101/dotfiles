"""Commit/push checks for the chezmoi source repository (cross-platform; needs only the Python 3.9+
standard library and git). pre-commit runs this package directly:
`python src/dotfiles/utils/common/guard <subcommand>`; dotfiles backup / check import it.

Subcommands:
  staged [paths...]    pre-commit stage: check the index (staged content); all staged files when no paths given
  push                 pre-push stage: check files added/modified by each commit about to be pushed
  tree                 check every tracked and unignored new file in the worktree (used by dotfiles check)
  scan [-q] files...   secret-pattern scan only; exit 1 on a hit (dotfiles backup uses this to decide on encryption)
  prune-stash          delete leftover pre-commit stash patches and tighten the cache directory permissions

Checks (checks.CHECKS, in report order):
  age       *.age must be real age ciphertext; chezmoi's encrypted_ prefix and .age suffix must come in pairs
  policy    sensitive targets (SSH, credential files, /etc/sudoers, ...) must be stored as encrypted_*.age
  secret    plaintext files must not contain secret patterns
  warn      this machine's home directory written out (does not block the commit)

Layout: base.py (Finding, FileContext: the extension interface), checks.py (rule data, built-in
checks, the CHECKS registry), paths.py (source path -> deployment target), git.py (index / pushed
commits / worktree), cli.py (subcommands, report). Extensions follow two rules: stdlib and this
package only (pre-commit runs before uv or a venv exist), and checks are registered in code only, so
nothing outside the code can relax them. Output contains only file:line and the rule name; the
matched content is never printed. A line containing "gitleaks:allow" or "pragma: allowlist secret"
is skipped by the secret check.
"""
from .base import MAX_BYTES, Check, FileContext, Finding, is_binary
from .checks import (
    CHECKS,
    ENCRYPT_EXEMPT,
    ENCRYPT_REQUIRED,
    SECRET_RULES,
    check_content,
    home_path_rx,
    requires_encryption,
    secret_lines,
)
from .cli import cmd_prune_stash, cmd_push, cmd_scan, cmd_staged, cmd_tree, main, report
from .paths import is_encrypted_source, source_root, target_of

__all__ = [
    "CHECKS", "ENCRYPT_EXEMPT", "ENCRYPT_REQUIRED", "MAX_BYTES", "SECRET_RULES", "Check", "FileContext",
    "Finding", "check_content", "cmd_prune_stash", "cmd_push", "cmd_scan", "cmd_staged", "cmd_tree",
    "home_path_rx", "is_binary", "is_encrypted_source", "main", "report", "requires_encryption",
    "secret_lines", "source_root", "target_of",
]

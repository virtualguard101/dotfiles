"""guard's command line: staged / push / tree / scan / prune-stash, and the report format."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import git
from .base import MAX_BYTES, Finding, is_binary
from .checks import check_content, secret_lines
from .paths import load_owners, owners_path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")   # don't crash on characters the console can't encode
    except (AttributeError, ValueError):
        pass


def report(findings: List[Finding], hint: bool = True) -> int:
    errors = [x for x in findings if x.level == "error"]
    warns = [x for x in findings if x.level == "warn"]
    for x in warns:
        print(f"warning: {x}")
    for x in errors:
        print(f"error:   {x}")
    owner = [x for x in errors if x.rule.startswith(("no-platform-owner", "platforms-toml-invalid"))]
    if owner and hint:
        print(
            "\nTargets without a platform owner would deploy on every platform. How to resolve:\n"
            "  - run dotfiles backup (registers them under this platform), or add them to\n"
            f"    {owners_path()} by hand (shared / linux / windows)\n"
            "  - commit the target and the updated platforms.toml together"
        )
    if len(owner) < len(errors) and hint:
        print(
            "\nUnencrypted sensitive content detected (values are not printed). How to resolve:\n"
            "  - store the sensitive file encrypted: chezmoi forget <target>, then chezmoi add --encrypt <target>\n"
            "  - false positive: add a gitleaks:allow comment on the line, or adjust the rules in "
            "src/dotfiles/utils/common/guard/checks.py\n"
            "  - committed but not pushed: git reset --soft HEAD~1 and redo; already pushed: rotate that credential now"
        )
    return 1 if errors else 0


def owners_from(data: Optional[bytes], findings: List[Finding], where: str = "") -> Optional[dict]:
    """Parse platforms.toml content from one snapshot; None (ownership check off) when absent.
    A file that does not parse is itself an error."""
    if data is None:
        return None
    try:
        return load_owners(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):     # tomllib.TOMLDecodeError is a ValueError
        findings.append(Finding("error", "platforms-toml-invalid", owners_path(), where=where))
        return None


def cmd_staged(paths: List[str]) -> int:
    entries = git.index_entries()
    wanted = [p.replace("\\", "/") for p in paths] if paths else list(entries)
    wanted = [p for p in wanted if p in entries]
    own = entries.get(owners_path())
    blobs = git.read_blobs([entries[p][1] for p in wanted] + ([own[1]] if own else []))
    findings: List[Finding] = []
    owners = owners_from(blobs.get(own[1]) if own else None, findings)
    for p in wanted:
        mode, sha = entries[p]
        findings += check_content(p, blobs.get(sha, b""), mode, owners=owners)
    return report(findings)


def cmd_push() -> int:
    findings: List[Finding] = []
    for commit in git.push_commits():
        changes = git.commit_changes(commit)
        blobs = git.read_blobs([sha for _, _, sha in changes])
        owners = owners_from(git.blob_at(commit, owners_path()), findings, where=commit[:8])
        for path, mode, sha in changes:
            for x in check_content(path, blobs.get(sha, b""), mode, owners=owners):
                x.where = commit[:8]
                findings.append(x)
    return report([x for x in findings if x.level == "error"])


def cmd_tree() -> int:
    findings: List[Finding] = []
    p_own = Path(owners_path())
    owners = owners_from(p_own.read_bytes() if p_own.is_file() else None, findings)
    for p in git.worktree_files():
        with open(p, "rb") as fh:
            findings += check_content(p, fh.read(), owners=owners)
    return report(findings, hint=False)


def cmd_scan(files: List[str], quiet: bool) -> int:
    hit = False
    for p in files:
        try:
            with open(p, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_BYTES or is_binary(data):
            continue
        for no, rule in secret_lines(data):
            hit = True
            if quiet:
                return 1
            print(f"{p}:{no}: [{rule}]")
    return 1 if hit else 0


def precommit_home() -> Path:
    env = os.environ.get("PRE_COMMIT_HOME")
    if env:
        return Path(env)
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(cache) / "pre-commit"


def cmd_prune_stash(max_age_minutes: int) -> int:
    """pre-commit saves unstaged changes as patch* and never deletes them; the patches may contain plaintext."""
    home = precommit_home()
    if not home.is_dir():
        return 0
    if os.name == "posix":
        try:
            os.chmod(home, 0o700)
        except OSError:
            pass
    cutoff = time.time() - max_age_minutes * 60
    for p in home.glob("patch*"):
        try:
            # only delete old patches: a running pre-commit (including this repo's own) still needs its patch to restore
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    from . import __doc__ as doc
    ap = argparse.ArgumentParser(prog="guard", description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("staged"); s.add_argument("paths", nargs="*")
    sub.add_parser("push")
    sub.add_parser("tree")
    s = sub.add_parser("scan"); s.add_argument("-q", "--quiet", action="store_true"); s.add_argument("files", nargs="+")
    s = sub.add_parser("prune-stash"); s.add_argument("--max-age-minutes", type=int, default=60)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "staged":
            return cmd_staged(a.paths)
        if a.cmd == "push":
            return cmd_push()
        if a.cmd == "tree":
            return cmd_tree()
        if a.cmd == "scan":
            return cmd_scan(a.files, a.quiet)
        return cmd_prune_stash(a.max_age_minutes)
    except subprocess.CalledProcessError as e:
        print(f"error: git command failed: {' '.join(map(str, e.cmd))}", file=sys.stderr)
        return 2

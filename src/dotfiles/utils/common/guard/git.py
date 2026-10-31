"""git plumbing: where the content to check comes from (the index, the commits about to be pushed,
the worktree). This is the second extension axis: a new stage (e.g. commit-msg) adds a source here
and reuses the same checks."""
from __future__ import annotations

import os
import subprocess
from typing import Dict, Iterator, List, Optional, Tuple

ZERO = "0" * 40


def git(*args: str, input_: Optional[bytes] = None) -> bytes:
    return subprocess.run(["git", *args], input=input_, check=True, stdout=subprocess.PIPE).stdout


def read_blobs(shas: List[str]) -> Dict[str, bytes]:
    """Read many blobs with one `git cat-file --batch`; contents stay in memory only."""
    if not shas:
        return {}
    uniq = list(dict.fromkeys(shas))
    out = git("cat-file", "--batch", input_=("\n".join(uniq) + "\n").encode())
    blobs: Dict[str, bytes] = {}
    pos = 0
    for sha in uniq:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        pos = nl + 1
        if len(header) < 3 or header[1] != b"blob":
            continue
        size = int(header[2])
        blobs[sha] = out[pos:pos + size]
        pos += size + 1
    return blobs


def index_entries() -> Dict[str, Tuple[str, str]]:
    """path -> (mode, blob sha), from the index."""
    entries = {}
    for rec in git("ls-files", "-s", "-z").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, sha, _stage = meta.decode().split()
        entries[path.decode("utf-8", "surrogateescape")] = (mode, sha)
    return entries


def push_commits() -> List[str]:
    to_ref = os.environ.get("PRE_COMMIT_TO_REF") or "HEAD"
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    if set(to_ref) == {"0"}:   # remote branch deletion
        return []
    if from_ref and set(from_ref) != {"0"}:
        rng = [f"{from_ref}..{to_ref}"]
    else:   # new branch or first push: every commit not on any remote
        rng = [to_ref, "--not", "--remotes"]
    return git("rev-list", "--reverse", *rng).decode().split()


def commit_changes(commit: str) -> List[Tuple[str, str, str]]:
    """(path, mode, blob sha) of files added, modified or type-changed by one commit."""
    raw = git("diff-tree", "-r", "-z", "--root", "--no-commit-id", "--no-renames", "--diff-filter=AMT", commit)
    fields = raw.split(b"\0")
    changes: List[Tuple[str, str, str]] = []
    for i in range(0, len(fields) - 1, 2):
        meta = fields[i].decode().lstrip(":").split()
        if len(meta) < 5:
            continue
        changes.append((fields[i + 1].decode("utf-8", "surrogateescape"), meta[1], meta[3]))
    return changes


def worktree_files() -> Iterator[str]:
    """Tracked and unignored new files that exist as regular files."""
    raw = git("ls-files", "-z", "-co", "--exclude-standard")
    for p in raw.decode("utf-8", "surrogateescape").split("\0"):
        if p and os.path.isfile(p) and not os.path.islink(p):
            yield p

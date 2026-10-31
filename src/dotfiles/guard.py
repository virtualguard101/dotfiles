#!/usr/bin/env python3
"""Commit/push checks for the chezmoi source repository (cross-platform; needs only the
Python 3.9+ standard library and git).

Subcommands:
  staged [paths...]    pre-commit stage: check the index (staged content); all staged files when no paths given
  push                 pre-push stage: check files added/modified by each commit about to be pushed
  tree                 check every tracked and unignored new file in the worktree (used by dotfiles check)
  scan [-q] files...   secret-pattern scan only; exit 1 on a hit (dotfiles backup uses this to decide on encryption)
  prune-stash          delete leftover pre-commit stash patches and tighten the cache directory permissions

Checks:
  age       *.age must be real age ciphertext; chezmoi's encrypted_ prefix and .age suffix must come in pairs
  secret    plaintext files must not contain secret patterns
  policy    sensitive targets (SSH, credential files, /etc/sudoers, ...) must be stored as encrypted_*.age
  warn      absolute home directory paths (does not block the commit)

Deployment targets follow chezmoi rules: files under the directory named by .chezmoiroot
(e.g. home/) map to ~ targets, system/ maps to system paths (/etc/...), and everything
else (tool code, package manifests) is not a deployment target but is still secret-scanned.

This file is both a dotfiles package module and executed directly by pre-commit, so it may
only depend on the standard library. Output contains only file:line and the rule name; the
matched content is never printed. A line containing "gitleaks:allow" or
"pragma: allowlist secret" is skipped by the secret check.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------- rules

# Secret patterns in plaintext: (rule name, regex). dotfiles backup reuses these via `scan`.
SECRET_RULES: list[tuple[str, re.Pattern[bytes]]] = [
    (name, re.compile(rx.encode()))
    for name, rx in [
        ("private-key", r"-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY( BLOCK)?-----"),
        ("age-secret-key", r"AGE-SECRET-KEY-1[0-9A-Z]{20,}"),
        ("github-token", r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,}"),
        ("github-pat", r"github_pat_[A-Za-z0-9_]{40,}"),
        ("openai-anthropic-key", r"(?<![A-Za-z0-9_])sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}"),
        ("aws-access-key", r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"),
        ("slack-token", r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}"),
        ("pypi-token", r"pypi-[A-Za-z0-9_-]{50,}"),
        ("npm-token", r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}"),
        ("npmrc-auth", r"_auth(Token)?\s*=\s*[^\s$]{8,}"),
        ("wakatime-key", r"waka_[0-9a-f-]{36}"),
        ("google-api-key", r"AIza[0-9A-Za-z_-]{35}"),
        ("generic-assignment",
         r"(?i)(api[_-]?key|secret|token|pass(word|wd)?)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_.~-]{12,}"),
    ]
]
ALLOW_MARKERS = (b"gitleaks:allow", b"pragma: allowlist secret")

# chezmoi target paths that must be encrypted (relative to ~, or a system path under
# system/ written with a leading /). fnmatch semantics: * may cross /.
ENCRYPT_REQUIRED = [
    ".ssh/*", ".gnupg/*",
    ".pypirc", ".npmrc", ".netrc", "_netrc", ".git-credentials",
    ".docker/config.json", ".config/gh/hosts.yml", "AppData/Roaming/GitHub CLI/hosts.yml",
    ".wakatime.cfg", ".aws/credentials", ".kube/config",
    ".config/rclone/rclone.conf", "AppData/Roaming/rclone/rclone.conf",
    "*.pem", "*.key", "*.p12", "*.pfx", "*id_rsa", "*id_ecdsa", "*id_ed25519",
    ".env", ".env.*", "*/.env", "*/.env.*",
    "/etc/shadow*", "/etc/gshadow*", "/etc/sudoers", "/etc/sudoers.d/*", "/etc/crypttab",
    "/etc/NetworkManager/system-connections/*", "/etc/wireguard/*", "/etc/ssh/ssh_host_*_key",
    "/etc/mihomo/config.yaml",
]
ENCRYPT_EXEMPT = ["*.pub", ".ssh/known_hosts*", ".env.example", "*/.env.example"]

def home_path_rx(home: Path | None = None) -> re.Pattern[bytes]:
    """This machine's home directory as a file may spell it: the literal path, plus the Windows
    drive-letter (plain or JSON-escaped) and MSYS (/c/Users/...) forms of the same user name.
    Only the current user counts: generic examples such as /home/username or C:\\Users\\alice in
    comments and tests say nothing about this machine and are not reported."""
    home = home or Path.home()
    if not home.name:                       # home is a filesystem root: nothing meaningful to match
        return re.compile(rb"(?!)")
    leaf = re.escape(os.fsencode(home.name))
    literal = re.escape(os.fsencode(str(home)))
    sep = rb"(?:\\\\|\\|/)"
    return re.compile(rb"(?i)(?:" + literal + rb"|(?:[a-z]:|/[a-z])" + sep + rb"users" + sep + leaf
                      + rb")(?![\w.-])")


@cache
def _this_home_rx() -> re.Pattern[bytes]:
    return home_path_rx()

AGE_ARMOR_BEGIN = b"-----BEGIN AGE ENCRYPTED FILE-----"
AGE_ARMOR_END = b"-----END AGE ENCRYPTED FILE-----"
AGE_BINARY = b"age-encryption.org/v1\n"

MAX_BYTES = 5 * 1024 * 1024
FILE_PREFIXES = ("encrypted_", "private_", "readonly_", "empty_", "executable_", "create_",
                 "modify_", "remove_", "symlink_", "run_", "once_", "onchange_", "before_",
                 "after_", "literal_")
DIR_PREFIXES = ("remove_", "external_", "exact_", "private_", "readonly_", "literal_")
LEGACY_NON_TARGET_TOP = ("scripts",)   # tool directory ignored in the old layout without .chezmoiroot
SYSTEM_TOP = "system"                   # source dir for system config like /etc (separate chezmoi instance)

# ---------------------------------------------------------------- output

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")   # don't crash on characters the console can't encode
    except (AttributeError, ValueError):
        pass


@dataclass
class Finding:
    level: str      # error | warn
    rule: str
    path: str
    line: int = 0
    where: str = ""  # short commit SHA (push mode)

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        prefix = f"{self.where} " if self.where else ""
        return f"{prefix}{loc}: [{self.rule}]"


# ---------------------------------------------------------------- git helpers

def git(*args: str, input_: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], input=input_, check=True,
                          stdout=subprocess.PIPE).stdout


def read_blobs(shas: list[str]) -> dict[str, bytes]:
    """Read many blobs with one `git cat-file --batch`; contents stay in memory only."""
    if not shas:
        return {}
    uniq = list(dict.fromkeys(shas))
    out = git("cat-file", "--batch", input_=("\n".join(uniq) + "\n").encode())
    blobs: dict[str, bytes] = {}
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


def index_entries() -> dict[str, tuple[str, str]]:
    """path -> (mode, blob sha), from the index."""
    entries = {}
    for rec in git("ls-files", "-s", "-z").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, sha, _stage = meta.decode().split()
        entries[path.decode("utf-8", "surrogateescape")] = (mode, sha)
    return entries


# ---------------------------------------------------------------- chezmoi paths

def _strip_attrs(comp: str, prefixes: tuple[str, ...]) -> tuple[str, bool]:
    """Strip chezmoi attribute prefixes; stop at literal_ and keep the rest verbatim."""
    while True:
        if comp.startswith("literal_"):
            return comp[len("literal_"):], True
        for p in prefixes:
            if comp.startswith(p):
                comp = comp[len(p):]
                break
        else:
            return comp, False


_SOURCE_ROOT: str | None = None


def source_root() -> str:
    """The value of .chezmoiroot at the repo top (e.g. "home"); empty when absent (legacy layout)."""
    global _SOURCE_ROOT
    if _SOURCE_ROOT is None:
        try:
            top = git("rev-parse", "--show-toplevel").decode().strip()
            _SOURCE_ROOT = Path(top, ".chezmoiroot").read_text(encoding="utf-8").strip().strip("/")
        except (OSError, subprocess.CalledProcessError):
            _SOURCE_ROOT = ""
    return _SOURCE_ROOT


def target_of(source: str, root: str | None = None) -> str | None:
    """chezmoi source path (relative to the repo top) -> target path (relative to ~, or /... under
    system/); None when the file is not a deployment target."""
    root = source_root() if root is None else root
    parts = source.split("/")
    system = parts[0] == SYSTEM_TOP
    if system:
        parts = parts[1:]
    elif root:
        root_parts = root.split("/")
        if parts[:len(root_parts)] != root_parts:
            return None
        parts = parts[len(root_parts):]
    elif parts[0] in LEGACY_NON_TARGET_TOP:
        return None
    # chezmoi ignores source-state entries starting with "." (.chezmoiignore, .chezmoidata etc. are its own config)
    if not parts or any(p.startswith(".") for p in parts):
        return None
    out = []
    for i, comp in enumerate(parts):
        last = i == len(parts) - 1
        comp, literal = _strip_attrs(comp, FILE_PREFIXES if last else DIR_PREFIXES)
        if not literal and comp.startswith("dot_"):
            comp = "." + comp[4:]
        if last:
            for suffix in (".tmpl", ".literal", ".age"):
                if comp.endswith(suffix):
                    comp = comp[: -len(suffix)]
        out.append(comp)
    target = "/".join(out)
    return "/" + target if system else target


def is_encrypted_source(source: str) -> bool:
    return Path(source).name.startswith("encrypted_") and source.endswith(".age")


def requires_encryption(target: str) -> bool:
    if any(fnmatch.fnmatchcase(target, p) for p in ENCRYPT_EXEMPT):
        return False
    return any(fnmatch.fnmatchcase(target, p) for p in ENCRYPT_REQUIRED)


# ---------------------------------------------------------------- checks

def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def secret_lines(data: bytes) -> Iterator[tuple[int, str]]:
    for no, line in enumerate(data.splitlines(), 1):
        if any(m in line for m in ALLOW_MARKERS):
            continue
        for name, rx in SECRET_RULES:
            if rx.search(line):
                yield no, name
                break


def check_content(path: str, data: bytes, mode: str = "100644") -> list[Finding]:
    f: list[Finding] = []
    if mode in ("120000", "160000"):   # symlink / submodule
        return f
    name = Path(path).name
    is_age = path.endswith(".age")

    if is_age:
        if not (data.startswith(AGE_ARMOR_BEGIN) or data.startswith(AGE_BINARY)):
            f.append(Finding("error", "age-not-ciphertext", path))
        elif data.startswith(AGE_ARMOR_BEGIN) and AGE_ARMOR_END not in data[-200:]:
            f.append(Finding("error", "age-armor-truncated", path))
        if not name.startswith("encrypted_") and target_of(path) is not None:
            f.append(Finding("error", "age-without-encrypted-prefix", path))
        return f
    if name.startswith("encrypted_") and target_of(path) is not None:
        f.append(Finding("error", "encrypted-prefix-without-age", path))

    target = target_of(path)
    if target is not None and requires_encryption(target):
        f.append(Finding("error", f"must-encrypt({target})", path))

    if len(data) > MAX_BYTES or is_binary(data):
        return f
    for no, rule in secret_lines(data):
        f.append(Finding("error", rule, path, no))
    m = _this_home_rx().search(data)
    if m:
        no = data.count(b"\n", 0, m.start()) + 1
        f.append(Finding("warn", "absolute-home-path", path, no))
    return f


def report(findings: list[Finding], hint: bool = True) -> int:
    errors = [x for x in findings if x.level == "error"]
    warns = [x for x in findings if x.level == "warn"]
    for x in warns:
        print(f"warning: {x}")
    for x in errors:
        print(f"error:   {x}")
    if errors and hint:
        print(
            "\nUnencrypted sensitive content detected (values are not printed). How to resolve:\n"
            "  - store the sensitive file encrypted: chezmoi forget <target>, then chezmoi add --encrypt <target>\n"
            "  - false positive: add a gitleaks:allow comment on the line, or adjust the rules in src/dotfiles/guard.py\n"
            "  - committed but not pushed: git reset --soft HEAD~1 and redo; already pushed: rotate that credential now"
        )
    return 1 if errors else 0


# ---------------------------------------------------------------- subcommands

def cmd_staged(paths: list[str]) -> int:
    entries = index_entries()
    wanted = [p.replace("\\", "/") for p in paths] if paths else list(entries)
    wanted = [p for p in wanted if p in entries]
    blobs = read_blobs([entries[p][1] for p in wanted])
    findings: list[Finding] = []
    for p in wanted:
        mode, sha = entries[p]
        findings += check_content(p, blobs.get(sha, b""), mode)
    return report(findings)


ZERO = "0" * 40


def push_commits() -> list[str]:
    to_ref = os.environ.get("PRE_COMMIT_TO_REF") or "HEAD"
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    if set(to_ref) == {"0"}:   # remote branch deletion
        return []
    if from_ref and set(from_ref) != {"0"}:
        rng = [f"{from_ref}..{to_ref}"]
    else:   # new branch or first push: every commit not on any remote
        rng = [to_ref, "--not", "--remotes"]
    return git("rev-list", "--reverse", *rng).decode().split()


def cmd_push() -> int:
    findings: list[Finding] = []
    for commit in push_commits():
        raw = git("diff-tree", "-r", "-z", "--root", "--no-commit-id", "--no-renames",
                  "--diff-filter=AMT", commit)
        fields = raw.split(b"\0")
        changes: list[tuple[str, str, str]] = []
        for i in range(0, len(fields) - 1, 2):
            meta = fields[i].decode().lstrip(":").split()
            if len(meta) < 5:
                continue
            changes.append((fields[i + 1].decode("utf-8", "surrogateescape"), meta[1], meta[3]))
        blobs = read_blobs([sha for _, _, sha in changes])
        for path, mode, sha in changes:
            for x in check_content(path, blobs.get(sha, b""), mode):
                x.where = commit[:8]
                findings.append(x)
    return report([x for x in findings if x.level == "error"])


def cmd_tree() -> int:
    raw = git("ls-files", "-z", "-co", "--exclude-standard")
    findings: list[Finding] = []
    for p in raw.decode("utf-8", "surrogateescape").split("\0"):
        if not p or not os.path.isfile(p) or os.path.islink(p):
            continue
        with open(p, "rb") as fh:
            findings += check_content(p, fh.read())
    return report(findings, hint=False)


def cmd_scan(files: list[str], quiet: bool) -> int:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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


if __name__ == "__main__":
    sys.exit(main())

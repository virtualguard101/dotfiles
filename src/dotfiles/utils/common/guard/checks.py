"""Built-in checks and the registry. To add a check: write a function `(FileContext) ->
Iterable[Finding]`, append it to CHECKS, and test it in tests/test_guard.py. Checks are registered
in code only — nothing outside the code (data files, plugin dirs) can add, remove or relax one."""
from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

from .base import SKIPPED_MODES, Check, FileContext, Finding

# ---------------------------------------------------------------- rule data

# Secret patterns in plaintext: (rule name, regex). dotfiles backup reuses these via `scan`.
SECRET_RULES: List[Tuple[str, "re.Pattern[bytes]"]] = [
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

AGE_ARMOR_BEGIN = b"-----BEGIN AGE ENCRYPTED FILE-----"
AGE_ARMOR_END = b"-----END AGE ENCRYPTED FILE-----"
AGE_BINARY = b"age-encryption.org/v1\n"


# ---------------------------------------------------------------- helpers (also used by dotfiles backup)

def secret_lines(data: bytes) -> Iterator[Tuple[int, str]]:
    for no, line in enumerate(data.splitlines(), 1):
        if any(m in line for m in ALLOW_MARKERS):
            continue
        for name, rx in SECRET_RULES:
            if rx.search(line):
                yield no, name
                break


def requires_encryption(target: str) -> bool:
    if any(fnmatch.fnmatchcase(target, p) for p in ENCRYPT_EXEMPT):
        return False
    return any(fnmatch.fnmatchcase(target, p) for p in ENCRYPT_REQUIRED)


def home_path_rx(home: Optional[Path] = None) -> "re.Pattern[bytes]":
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


@lru_cache(maxsize=None)
def _this_home_rx() -> "re.Pattern[bytes]":
    return home_path_rx()


# ---------------------------------------------------------------- built-in checks

def check_age(ctx: FileContext) -> Iterable[Finding]:
    """*.age must be real age ciphertext; chezmoi's encrypted_ prefix and .age suffix come in pairs."""
    if ctx.is_age:
        if not (ctx.data.startswith(AGE_ARMOR_BEGIN) or ctx.data.startswith(AGE_BINARY)):
            yield Finding("error", "age-not-ciphertext", ctx.path)
        elif ctx.data.startswith(AGE_ARMOR_BEGIN) and AGE_ARMOR_END not in ctx.data[-200:]:
            yield Finding("error", "age-armor-truncated", ctx.path)
        if not ctx.name.startswith("encrypted_") and ctx.target is not None:
            yield Finding("error", "age-without-encrypted-prefix", ctx.path)
    elif ctx.name.startswith("encrypted_") and ctx.target is not None:
        yield Finding("error", "encrypted-prefix-without-age", ctx.path)


def check_policy(ctx: FileContext) -> Iterable[Finding]:
    """Sensitive targets (SSH, credential files, /etc/sudoers, ...) must be stored as encrypted_*.age."""
    if not ctx.is_age and ctx.target is not None and requires_encryption(ctx.target):
        yield Finding("error", f"must-encrypt({ctx.target})", ctx.path)


def check_secrets(ctx: FileContext) -> Iterable[Finding]:
    """Plaintext must not contain secret patterns (a gitleaks:allow line is skipped)."""
    if ctx.text is not None:
        for no, rule in secret_lines(ctx.text):
            yield Finding("error", rule, ctx.path, no)


def check_home_path(ctx: FileContext) -> Iterable[Finding]:
    """This machine's home directory written out literally (warning only)."""
    if ctx.text is not None:
        m = _this_home_rx().search(ctx.text)
        if m:
            yield Finding("warn", "absolute-home-path", ctx.path, ctx.text.count(b"\n", 0, m.start()) + 1)


# The registry, in report order. Append new checks here.
CHECKS: List[Check] = [check_age, check_policy, check_secrets, check_home_path]


def check_content(path: str, data: bytes, mode: str = "100644",
                  checks: Optional[List[Check]] = None) -> List[Finding]:
    """Run the registered checks (or the given ones) on one file."""
    if mode in SKIPPED_MODES:
        return []
    ctx = FileContext(path, data, mode)
    return [f for check in (CHECKS if checks is None else checks) for f in check(ctx)]

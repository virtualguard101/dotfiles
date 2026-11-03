"""chezmoi source path -> deployment target: files under the directory named by .chezmoiroot
(e.g. home/) map to ~ targets, system/ maps to system paths (/etc/...), and everything else (tool
code, package manifests) is not a deployment target."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .git import git

FILE_PREFIXES = ("encrypted_", "private_", "readonly_", "empty_", "executable_", "create_",
                 "modify_", "remove_", "symlink_", "run_", "once_", "onchange_", "before_",
                 "after_", "literal_")
DIR_PREFIXES = ("remove_", "external_", "exact_", "private_", "readonly_", "literal_")
LEGACY_NON_TARGET_TOP = ("scripts",)   # tool directory ignored in the old layout without .chezmoiroot
SYSTEM_TOP = "system"                   # system/<distro>/etc: /etc of one distro (separate chezmoi instance)


def _strip_attrs(comp: str, prefixes: tuple) -> tuple:
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


_SOURCE_ROOT: Optional[str] = None


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


def target_of(source: str, root: Optional[str] = None) -> Optional[str]:
    """chezmoi source path (relative to the repo top) -> target path (relative to ~, or /... under
    system/); None when the file is not a deployment target."""
    root = source_root() if root is None else root
    parts = source.split("/")
    system = parts[0] == SYSTEM_TOP
    if system:
        # system/<distro>/<path> -> /<path>; the legacy layout system/etc/... had no distro component
        parts = parts[1:] if len(parts) > 1 and parts[1] == "etc" else parts[2:]
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
            # the encryption suffix is outermost (chezmoi writes encrypted_x.tmpl.age); .literal
            # ends suffix parsing, so x.tmpl.literal keeps its .tmpl
            comp = comp.removesuffix(".age")
            if comp.endswith(".literal"):
                comp = comp.removesuffix(".literal")
            else:
                comp = comp.removesuffix(".tmpl")
        out.append(comp)
    target = "/".join(out)
    return "/" + target if system else target


def is_encrypted_source(source: str) -> bool:
    return Path(source).name.startswith("encrypted_") and source.endswith(".age")


# ---------------------------------------------------------------- platform ownership (platforms.toml)

OWNERS_FILE = ".chezmoidata/platforms.toml"   # relative to the source state (the .chezmoiroot dir)


def owners_path(root: Optional[str] = None) -> str:
    """Repo-relative path of platforms.toml."""
    root = source_root() if root is None else root
    return f"{root}/{OWNERS_FILE}" if root else OWNERS_FILE


def covering(target: str, entries) -> Optional[str]:
    """The declaration covering target (the same path, or a directory target containing it)."""
    for e in entries:
        if target == e or target.startswith(e.rstrip("/") + "/"):
            return e
    return None


_TABLE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|#.*|\[|\]')


def parse_tables(text: str) -> Dict[str, Dict[str, List[str]]]:
    """table -> key -> list of strings. Uses tomllib when present (Python 3.11+); otherwise a small
    parser for the format platforms.toml is written in (tables whose values are string arrays)."""
    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib is not None:
        data = tomllib.loads(text)
        return {t: {k: list(v) for k, v in body.items() if isinstance(v, list)}
                for t, body in data.items() if isinstance(body, dict)}
    out: Dict[str, Dict[str, List[str]]] = {}
    table: Optional[str] = None
    key: Optional[str] = None
    items: List[str] = []
    for line in text.splitlines():
        rest = line
        if key is None:
            m = _TABLE.match(line)
            if m:
                table = m.group(1).strip()
                continue
            m = _KEY.match(line)
            if not m or table is None:
                continue
            key, items, rest = m.group(1), [], line[m.end():]
            if "[" not in rest:            # a scalar value: not ours
                key = None
                continue
        for tok in _TOKEN.findall(rest):
            if tok.startswith("#"):
                break
            if tok.startswith('"'):
                items.append(json.loads(tok))
            elif tok == "]":
                out.setdefault(table or "", {})[key or ""] = items
                key = None
                break
    return out


def load_owners(text: str) -> Dict[str, List[str]]:
    """The [platforms] table of platforms.toml: owner (shared / linux / windows) -> targets."""
    return parse_tables(text).get("platforms", {})

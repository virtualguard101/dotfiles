"""chezmoi source path -> deployment target: files under the directory named by .chezmoiroot
(e.g. home/) map to ~ targets, system/ maps to system paths (/etc/...), and everything else (tool
code, package manifests) is not a deployment target."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .git import git

FILE_PREFIXES = ("encrypted_", "private_", "readonly_", "empty_", "executable_", "create_",
                 "modify_", "remove_", "symlink_", "run_", "once_", "onchange_", "before_",
                 "after_", "literal_")
DIR_PREFIXES = ("remove_", "external_", "exact_", "private_", "readonly_", "literal_")
LEGACY_NON_TARGET_TOP = ("scripts",)   # tool directory ignored in the old layout without .chezmoiroot
SYSTEM_TOP = "system"                   # source dir for system config like /etc (separate chezmoi instance)


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

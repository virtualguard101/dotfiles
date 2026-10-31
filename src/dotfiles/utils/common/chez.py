"""Repo and chezmoi wrapper: locating the source, config parsing, the managed set, age identities
and platform ownership."""
from __future__ import annotations

import json
import os
import re
import tomllib
from functools import cache
from pathlib import Path

from dotfiles.utils import platforms, ui

REPO = Path(__file__).resolve().parents[4]   # src/dotfiles/utils/common/chez.py -> repo top
PLATFORMS = ("linux", "windows")
DEFAULT_IDENTITY = Path(".config") / "chezmoi" / "key.txt"   # relative to ~; where new machines put the key


def repo() -> Path:
    if not (REPO / ".chezmoiroot").is_file():
        raise ui.Abort(f"{REPO} is not the dotfiles repo (missing .chezmoiroot)", 2)
    return REPO


def source_state_dir() -> Path:
    return repo() / (REPO / ".chezmoiroot").read_text(encoding="utf-8").strip()


def system_source_dir() -> Path:
    return repo() / "system"


def packages_dir() -> Path:
    return repo() / "packages"


# ---------------------------------------------------------------- chezmoi calls

def require_chezmoi() -> None:
    if not platforms.have("chezmoi"):
        raise ui.Abort("chezmoi not found (run bootstrap first)", 2)


def base_args(os_override: str | None = None) -> list[str]:
    # --no-pager: chezmoi blocks forever when its pager quits before reading all output
    args = ["chezmoi", "--no-pager", "--source", str(repo())]
    if os_override:
        args += ["--override-data", json.dumps({"chezmoi": {"os": os_override}})]
    return args


def cz(*args: str, os_override: str | None = None, **kw) -> platforms.Result:
    return platforms.run([*base_args(os_override), *args], **kw)


def cz_live(*args: str, **kw) -> int:
    return platforms.run_live([*base_args(), *args], **kw)


def cz_lines(*args: str, os_override: str | None = None) -> list[str]:
    r = cz(*args, os_override=os_override)
    if not r.ok:
        raise ui.Abort(f"chezmoi {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return [line for line in r.stdout.splitlines() if line.strip()]


@cache
def dump_config() -> dict:
    r = cz("dump-config", "--format", "json")
    if not r.ok:
        raise ui.Abort(f"chezmoi dump-config failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def config_file() -> Path:
    return platforms.xdg_config() / "chezmoi" / "chezmoi.toml"


def age_identities(config: dict | None = None) -> list[Path]:
    age = (config or dump_config()).get("age") or {}
    ids = [age.get("identity")] + list(age.get("identities") or [])
    return [Path(i) for i in ids if i]


def age_recipients(config: dict | None = None) -> list[str]:
    age = (config or dump_config()).get("age") or {}
    return [r for r in [age.get("recipient")] + list(age.get("recipients") or []) if r]


def template_recipients() -> list[str]:
    """Recipient public keys hardcoded in the repo's .chezmoi.toml.tmpl; used to validate the key on a new machine."""
    text = (source_state_dir() / ".chezmoi.toml.tmpl").read_text(encoding="utf-8")
    return re.findall(r'recipients?\s*=\s*"(age1[0-9a-z]+)"', text)


def recipient_of(identity: Path) -> str | None:
    """Derive the public key from a private key via chezmoi's bundled age (no standalone age needed)."""
    r = platforms.run(["chezmoi", "age-keygen", "-y", str(identity)], timeout=30)
    if not r.ok:
        return None
    m = re.search(r"age1[0-9a-z]+", r.stdout)
    return m.group(0) if m else None


def managed(include: str = "files", exclude: str = "externals", os_override: str | None = None,
            absolute: bool = False) -> list[str]:
    args = ["managed", "-i", include]
    if exclude:
        args += ["-x", exclude]
    if absolute:
        args += ["-p", "absolute"]
    return cz_lines(*args, os_override=os_override)


def is_managed(target: Path) -> bool:
    return cz("source-path", str(target)).ok


# ---------------------------------------------------------------- platform ownership

def platforms_file() -> Path:
    return source_state_dir() / ".chezmoidata" / "platforms.toml"


def load_platforms() -> dict[str, list[str]]:
    data = tomllib.loads(platforms_file().read_text(encoding="utf-8")).get("platforms", {})
    return {k: list(v) for k, v in data.items()}


def covering(target: str, entries: list[str]) -> str | None:
    """The declaration covering target (the same path, or a directory target containing it)."""
    for e in entries:
        if target == e or target.startswith(e.rstrip("/") + "/"):
            return e
    return None


def owner_of(target: str, owners: dict[str, list[str]] | None = None) -> str | None:
    owners = owners or load_platforms()
    for name in ("shared", *PLATFORMS):
        if covering(target, owners.get(name, [])):
            return name
    return None


def register_platform(target: str, platform: str, dry: bool) -> bool:
    """Register a newly tracked target under the current platform. Rewrites only the arrays, keeping
    the file's header comment."""
    owners = load_platforms()
    if owner_of(target, owners):
        return False
    owners.setdefault(platform, []).append(target)
    if dry:
        return True
    text = platforms_file().read_text(encoding="utf-8")
    head = text[: text.index("[platforms]")]
    body = ["[platforms]"]
    for name in ("shared", *PLATFORMS, *sorted(set(owners) - {"shared", *PLATFORMS})):
        if name not in owners:
            continue
        body.append(f"{name} = [")
        body += [f"  {json.dumps(t, ensure_ascii=False)}," for t in sorted(set(owners[name]))]
        body.append("]")
    with open(platforms_file(), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + "\n".join(body) + "\n")
    return True


def check_platforms() -> list[str]:
    """Every target managed in a platform's view must be declared in exactly one ownership list."""
    owners = load_platforms()
    problems = []
    seen: dict[str, str] = {}
    for name, entries in owners.items():
        for e in entries:
            if e in seen:
                problems.append(f"{e} is registered in both {seen[e]} and {name}")
            seen[e] = name
    for os_name in PLATFORMS:
        for target in managed(include="files,symlinks,externals", exclude="", os_override=os_name):
            owner = owner_of(target, owners)
            if owner is None:
                problems.append(f"{target} (managed in the {os_name} view) has no entry in platforms.toml")
            elif owner not in ("shared", os_name):
                problems.append(f"{target} is owned by {owner} but managed in the {os_name} view (check .chezmoiignore)")
    return problems


# ---------------------------------------------------------------- git

def git(*args: str) -> platforms.Result:
    return platforms.run(["git", "-C", str(repo()), *args])


def tracked(path: Path) -> bool:
    try:
        rel = os.path.relpath(path, repo())
    except ValueError:      # Windows: different drive
        return False
    if rel.startswith(".."):
        return False
    return git("ls-files", "--error-unmatch", "--", rel).ok

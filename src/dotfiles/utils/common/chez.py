"""Repo and chezmoi wrapper: locating the source, config parsing, the managed set, age identities
and platform ownership."""
from __future__ import annotations

import json
import os
import re
from functools import cache
from pathlib import Path

from dotfiles.utils import platforms, ui
from dotfiles.utils.common.guard.paths import covering, parse_tables

REPO = Path(__file__).resolve().parents[4]   # src/dotfiles/utils/common/chez.py -> repo top
PLATFORMS = ("linux", "windows")
DEFAULT_IDENTITY = Path(".config") / "chezmoi" / "key.txt"   # relative to ~; where new machines put the key


def repo() -> Path:
    if not (REPO / ".chezmoiroot").is_file():
        raise ui.Abort(f"{REPO} is not the dotfiles repo (missing .chezmoiroot)", 2)
    return REPO


def source_state_dir() -> Path:
    return repo() / (REPO / ".chezmoiroot").read_text(encoding="utf-8").strip()


def system_root() -> Path:
    """system/: system configuration outside ~, one subdirectory per distro (system/<distro>/etc);
    system/windows/ holds Windows system settings (registry keys)."""
    return repo() / "system"


def system_source_dir(distro: str | None = None) -> Path:
    """Source dir of the separate chezmoi instance for system config: /etc on this distro (or the
    given one), or the registry keys on Windows."""
    if distro is None:
        if platforms.IS_WINDOWS:
            return system_root() / "windows"
        from dotfiles.utils.platforms import linux as linplat
        distro = linplat.distro()
    return system_root() / distro


def packages_dir() -> Path:
    return repo() / "packages"


# ---------------------------------------------------------------- chezmoi calls

def require_chezmoi() -> None:
    if not platforms.have("chezmoi"):
        raise ui.Abort("chezmoi not found (run bootstrap first)", 2)


def base_args(os_override: str | None = None, tags: list[str] | None = None) -> list[str]:
    """chezmoi with this repo as the source; os_override / tags render another machine's view."""
    # --no-pager: chezmoi blocks forever when its pager quits before reading all output
    args = ["chezmoi", "--no-pager", "--source", str(repo())]
    data: dict = {}
    if os_override:
        data["chezmoi"] = {"os": os_override}
    if tags is not None:
        data["tags"] = list(tags)
    if data:
        args += ["--override-data", json.dumps(data)]
    return args


def cz(*args: str, os_override: str | None = None, tags: list[str] | None = None, **kw) -> platforms.Result:
    return platforms.run([*base_args(os_override, tags), *args], **kw)


def cz_live(*args: str, **kw) -> int:
    return platforms.run_live([*base_args(), *args], **kw)


def cz_lines(*args: str, os_override: str | None = None, tags: list[str] | None = None) -> list[str]:
    r = cz(*args, os_override=os_override, tags=tags)
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
            absolute: bool = False, tags: list[str] | None = None) -> list[str]:
    args = ["managed", "-i", include]
    if exclude:
        args += ["-x", exclude]
    if absolute:
        args += ["-p", "absolute"]
    return cz_lines(*args, os_override=os_override, tags=tags)


def is_managed(target: Path) -> bool:
    return cz("source-path", str(target)).ok


# ---------------------------------------------------------------- platform ownership

def platforms_file() -> Path:
    return source_state_dir() / ".chezmoidata" / "platforms.toml"


def _tables() -> dict[str, dict[str, list[str]]]:
    return parse_tables(platforms_file().read_text(encoding="utf-8"))


def load_platforms() -> dict[str, list[str]]:
    """[platforms]: owner (shared / linux / windows) -> targets."""
    return _tables().get("platforms", {})


def load_requires() -> dict[str, list[str]]:
    """[requires]: environment tag -> targets deployed only on machines that have the tag."""
    return _tables().get("requires", {})


def machine_tags() -> list[str] | None:
    """Tags of this machine from the chezmoi config ([data] tags, written by `chezmoi init`);
    None when the config predates tags."""
    tags = (dump_config().get("data") or {}).get("tags")
    return [str(t) for t in tags] if isinstance(tags, list) else None


def owner_of(target: str, owners: dict[str, list[str]] | None = None) -> str | None:
    owners = owners or load_platforms()
    for name in ("shared", *PLATFORMS):
        if covering(target, owners.get(name, [])):
            return name
    return None


def _render_table(name: str, table: dict[str, list[str]], order: tuple[str, ...]) -> list[str]:
    lines = [f"[{name}]"]
    for key in (*[k for k in order if k in table], *sorted(set(table) - set(order))):
        lines.append(f"{key} = [")
        lines += [f"  {json.dumps(t, ensure_ascii=False)}," for t in sorted(set(table[key]))]
        lines.append("]")
    return lines


def register_platform(target: str, platform: str, dry: bool) -> bool:
    """Register a newly tracked target under the given platform. Rewrites only the [platforms]
    table; the header comment and every other table ([requires], ...) are kept as written."""
    owners = load_platforms()
    if owner_of(target, owners):
        return False
    owners.setdefault(platform, []).append(target)
    if dry:
        return True
    lines = platforms_file().read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "[platforms]")
    # array items never start with "[", so the next line that does is the next table header
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():   # keep the blank line before the next table
        end -= 1
    new = lines[:start] + _render_table("platforms", owners, ("shared", *PLATFORMS)) + lines[end:]
    with open(platforms_file(), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(new) + "\n")
    return True


_TAG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def local_tags_file() -> Path:
    """Tags set by hand for this machine; read by .chezmoi.toml.tmpl at `chezmoi init`."""
    return platforms.home() / ".config" / "chezmoi" / "tags"


def read_local_tags(path: Path | None = None) -> tuple[list[str], list[str]]:
    """The local tags file -> (tags the template accepts, lines it ignores). The same rules as the
    template: trimmed, lowercased, blank lines and # comments skipped."""
    path = path or local_tags_file()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], []
    tags, ignored = [], []
    for line in lines:
        line = line.strip().lower()
        if not line or line.startswith("#"):
            continue
        (tags if _TAG.match(line) else ignored).append(line)
    return tags, ignored


def check_platforms() -> list[str]:
    """Every target managed in a platform's view must be declared in exactly one ownership list;
    every [requires] entry must use a well-formed tag and overlap an owned entry."""
    owners = load_platforms()
    requires = load_requires()
    problems = []
    seen: dict[str, str] = {}
    for name, entries in owners.items():
        for e in entries:
            if e in seen:
                problems.append(f"{e} is registered in both {seen[e]} and {name}")
            seen[e] = name
    owned = [e for entries in owners.values() for e in entries]
    for tag, entries in requires.items():
        if not _TAG.match(tag):
            problems.append(f"[requires] tag {tag!r} must match {_TAG.pattern}")
        for e in entries:
            if not any(covering(e, [o]) or covering(o, [e]) for o in owned):
                problems.append(f"[requires] {tag}: {e} matches no entry in [platforms]")
    every_tag = sorted(requires)   # render each OS view with all tags so every target is visible
    for os_name in PLATFORMS:
        for target in managed(include="files,symlinks,externals", exclude="", os_override=os_name, tags=every_tag):
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

"""dotfiles.toml: the backup and cleanup lists and tunables, kept as data at the repo top.

The file is the single source of truth: the code holds only the schema (keys, types, descriptions).
Both platforms share the file; per-platform lists live in [backup.linux] / [backup.windows] etc.

Writes (`dotfiles config add/remove/set`) edit the file line by line, so hand-written comments and
ordering survive: an array is kept one item per line, a scalar is replaced in place. Arrays of tables
([[...]]) are edited by hand. Every write is re-parsed and validated before it replaces the file.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from dotfiles.utils import ui
from dotfiles.utils.common import chez

FILE_NAME = "dotfiles.toml"


@dataclass(frozen=True)
class Key:
    section: str            # dotted table name, e.g. "backup.linux"
    name: str               # key inside the table
    kind: str               # "list" (of strings) | "int" | "str" | "tables" (array of tables)
    desc: str
    default: Any = None     # used only when the key is missing; lists default to []
    env: str | None = None  # environment variable that overrides the file
    fields: tuple[str, ...] = ()   # "tables": required fields (all string or list-of-string valued)

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.name}"


def _backup(os_name: str) -> list[Key]:
    s = f"backup.{os_name}"
    return [
        Key(s, "track", "list", "configs recommended for tracking (relative to ~; directories expand to regular files < 1 MiB)"),
        Key(s, "secret", "list", "always tracked encrypted (also checked by `dotfiles audit`)"),
        Key(s, "exclude", "list", "file-name patterns skipped when a tracked directory is expanded"),
        Key(s, "ignore", "list", "candidates never added and never reported (exact path, directory, or glob relative to ~)"),
    ]


def _clean(os_name: str, days: list[tuple[str, int, str, str]], uncovered_mb: int) -> list[Key]:
    s = f"clean.{os_name}"
    keys = [Key(s, name, "int", desc, default, env) for name, default, env, desc in days]
    keys += [
        Key(s, "uncovered_min_mb", "int", "minimum size for `clean scan` to list a cache dir no target covers",
            uncovered_mb, "UNCOVERED_MIN_MB"),
        Key(s, "uncovered_always", "list", "cache dir names never reported as uncovered (handled elsewhere)"),
        Key(s, "leftovers", "tables", "leftover dirs of uninstalled apps: detect (package / command / app names) -> paths (relative to ~)",
            fields=("detect", "paths")),
    ]
    return keys


SCHEMA: list[Key] = [
    *_backup("linux"),
    *_backup("windows"),
    Key("etc", "extra", "list", "/etc files or dirs backed up explicitly (dirs contribute only files no package owns)"),
    Key("etc", "secret", "list", "/etc paths always encrypted"),
    Key("etc", "exclude", "list", "/etc paths and globs never backed up (account databases, host keys, generated files)"),
    Key("etc", "hardware_bound", "list", "restore shows a diff only; installed only when named on the command line"),
    Key("clean", "disabled", "list", "cleanup target ids that are never run or listed"),
    Key("clean", "opt", "list", "target ids moved from the default tier to opt (run only when named)"),
    Key("clean", "paths", "tables", "extra path-only targets: id, tier (default|opt), desc, paths (~/ allowed), optional os",
        fields=("id", "tier", "desc", "paths")),
    *_clean("linux", [
        ("thumbnail_days", 90, "THUMBNAIL_DAYS", "thumbnails target: delete thumbnails not accessed for this many days"),
        ("stale_days", 180, "STALE_DAYS", "leftovers: Electron config dirs without writes for this many days"),
        ("paccache_keep", 1, "PACCACHE_KEEP", "pacman target: package versions kept per package"),
    ], 10) + [Key("clean.linux", "journal_max", "str", "journal target: size kept by journalctl --vacuum-size", "1G", "JOURNAL_MAX")],
    *_clean("windows", [
        ("temp_days", 7, "TEMP_DAYS", "temp target: top-level %TEMP% entries older than this many days"),
        ("stale_days", 180, "STALE_DAYS", "leftovers: Electron config dirs without writes for this many days"),
    ], 100),
]
_BY_DOTTED = {k.dotted: k for k in SCHEMA}
_SECTIONS = {k.section for k in SCHEMA}


class ConfigError(ui.Abort):
    pass


# ---------------------------------------------------------------- reading

def path() -> Path:
    return chez.repo() / FILE_NAME


@cache
def _load(p: Path) -> dict:
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{p} is missing (it holds the backup and cleanup lists)", 2) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: {e}", 2) from None


def reload() -> None:
    _load.cache_clear()


def data() -> dict:
    return _load(path())


def key(dotted: str) -> Key:
    try:
        return _BY_DOTTED[dotted]
    except KeyError:
        raise ConfigError(f"unknown config key: {dotted} (see dotfiles config list)", 2) from None


def _raw(k: Key, doc: dict | None = None) -> Any:
    node: Any = data() if doc is None else doc
    for part in k.section.split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node.get(k.name) if isinstance(node, dict) else None


def get(dotted: str) -> Any:
    """Effective value: environment override, then the file, then the schema default."""
    k = key(dotted)
    if k.env and os.environ.get(k.env):
        value = os.environ[k.env]
        if k.kind == "int":
            if value.isdigit():
                return int(value)
        else:
            return value
    value = _raw(k)
    if value is None:
        return [] if k.kind in ("list", "tables") else k.default
    return value


def validate(doc: dict | None = None) -> list[str]:
    """Problems with the file: unknown sections/keys (typos) and wrong value types."""
    doc = data() if doc is None else doc
    problems: list[str] = []

    def walk(node: dict, prefix: str) -> None:
        for name, value in node.items():
            dotted = f"{prefix}.{name}" if prefix else name
            if dotted in _BY_DOTTED:
                problems.extend(_type_problems(_BY_DOTTED[dotted], value))
            elif isinstance(value, dict) and any(s == dotted or s.startswith(dotted + ".") for s in _SECTIONS):
                walk(value, dotted)
            else:
                problems.append(f"unknown key {dotted}")

    walk(doc, "")
    return problems


def _type_problems(k: Key, value: Any) -> list[str]:
    def strs(v: Any) -> bool:
        return isinstance(v, list) and all(isinstance(x, str) for x in v)

    if k.kind == "list" and not strs(value):
        return [f"{k.dotted} must be a list of strings"]
    if k.kind == "int" and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        return [f"{k.dotted} must be a non-negative integer"]
    if k.kind == "str" and not isinstance(value, str):
        return [f"{k.dotted} must be a string"]
    if k.kind == "tables":
        if not isinstance(value, list) or not all(isinstance(t, dict) for t in value):
            return [f"{k.dotted} must be an array of tables ([[{k.dotted}]])"]
        out = []
        for i, t in enumerate(value):
            missing = [f for f in k.fields if f not in t]
            if missing:
                out.append(f"{k.dotted}[{i}] is missing {', '.join(missing)}")
            for f, v in t.items():
                if not (isinstance(v, str) or strs(v)):
                    out.append(f"{k.dotted}[{i}].{f} must be a string or a list of strings")
        return out
    return []


# ---------------------------------------------------------------- writing (line-based, keeps comments)

_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    """[start, end) of a table's body lines, or None when the table header is absent."""
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            if start is not None:
                return start, i
            m = _HEADER.match(line)
            if m and m.group(1).strip() == section:
                start = i + 1
    return (start, len(lines)) if start is not None else None


def _key_span(lines: list[str], start: int, end: int, name: str) -> tuple[int, int] | None:
    """[first, last] line indexes of `name = ...` inside a table body (multi-line arrays included)."""
    rx = re.compile(rf"^\s*{re.escape(name)}\s*=")
    for i in range(start, end):
        if not rx.match(lines[i]):
            continue
        rhs = _strip_comment(lines[i].split("=", 1)[1]).strip()
        if not (rhs.startswith("[") and not rhs.endswith("]")):
            return i, i                       # scalar or inline array
        j = i + 1
        while j < end - 1 and not _strip_comment(lines[j]).rstrip().endswith("]"):
            j += 1
        return i, j
    return None


def _strip_comment(line: str) -> str:
    """Line without a trailing # comment (ignores # inside double-quoted strings)."""
    out, quoted, escaped = [], False, False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == "\\" and quoted:
            escaped = True
        elif ch == '"':
            quoted = not quoted
        elif ch == "#" and not quoted:
            break
        out.append(ch)
    return "".join(out)


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array_lines(name: str, items: list[str], indent: str = "") -> list[str]:
    return [f"{indent}{name} = [", *(f"{indent}  {_q(x)}," for x in items), f"{indent}]"]


def _edit(mutate) -> None:
    """Apply a line edit, validate the result, then replace the file."""
    p = path()
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    lines = mutate(lines)
    new = "\n".join(lines) + "\n"
    try:
        doc = tomllib.loads(new)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"edit would break {p.name} ({e}); nothing was written", 1) from None
    problems = validate(doc)
    if problems:
        raise ConfigError(f"edit would make {p.name} invalid ({'; '.join(problems)}); nothing was written", 1)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(new, encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    reload()


def _list_edit(dotted: str, add: list[str], remove: list[str]) -> list[str]:
    k = key(dotted)
    if k.kind != "list":
        raise ConfigError(f"{dotted} is not a list ({k.kind}); use `config set` or edit the file", 2)
    current = list(get(dotted)) if _raw(k) is not None else []
    added = [v for v in dict.fromkeys(add) if v not in current]
    removed = [v for v in dict.fromkeys(remove) if v in current]
    missing = [v for v in remove if v not in current]
    if missing:
        raise ConfigError(f"not in {dotted}: {', '.join(missing)}", 1)
    if not added and not removed:
        return []
    wanted = [v for v in current if v not in removed] + added

    def mutate(lines: list[str]) -> list[str]:
        span = _section_span(lines, k.section)
        if span is None:
            while lines and not lines[-1].strip():
                lines.pop()
            return [*lines, "", f"[{k.section}]", *_array_lines(k.name, wanted)]
        start, end = span
        ks = _key_span(lines, start, end, k.name)
        if ks is None:
            insert = end
            while insert > start and not lines[insert - 1].strip():
                insert -= 1
            return lines[:insert] + _array_lines(k.name, wanted) + lines[insert:]
        first, last = ks
        indent = re.match(r"\s*", lines[first]).group(0)
        if first == last:                     # inline array: rewrite as one item per line
            return lines[:first] + _array_lines(k.name, wanted, indent) + lines[last + 1:]
        body = lines[first + 1:last]
        kept = []
        for line in body:                     # drop removed items, keep comments and other items as written
            item = _strip_comment(line).strip().rstrip(",").strip()
            if item and item.startswith('"'):
                try:
                    if json.loads(item) in removed:
                        continue
                except json.JSONDecodeError:
                    pass
            kept.append(line)
        item_indent = next((re.match(r"\s*", ln).group(0) for ln in body if ln.strip()), indent + "  ")
        kept += [f"{item_indent}{_q(v)}," for v in added]
        return lines[:first + 1] + kept + lines[last:]

    _edit(mutate)
    return [f"+ {v}" for v in added] + [f"- {v}" for v in removed]


def add(dotted: str, values: list[str]) -> list[str]:
    return _list_edit(dotted, values, [])


def remove(dotted: str, values: list[str]) -> list[str]:
    return _list_edit(dotted, [], values)


def set_value(dotted: str, raw: str) -> str:
    k = key(dotted)
    if k.kind == "int":
        if not raw.isdigit():
            raise ConfigError(f"{dotted} takes a non-negative integer", 2)
        literal = raw
    elif k.kind == "str":
        literal = _q(raw)
    else:
        raise ConfigError(f"{dotted} is a {k.kind}; use `config add/remove` or edit the file", 2)

    def mutate(lines: list[str]) -> list[str]:
        span = _section_span(lines, k.section)
        if span is None:
            return [*lines, "", f"[{k.section}]", f"{k.name} = {literal}"]
        start, end = span
        ks = _key_span(lines, start, end, k.name)
        if ks is None:
            insert = end
            while insert > start and not lines[insert - 1].strip():
                insert -= 1
            return lines[:insert] + [f"{k.name} = {literal}"] + lines[insert:]
        i = ks[0]
        comment = lines[i][len(_strip_comment(lines[i])):]
        indent = re.match(r"\s*", lines[i]).group(0)
        return lines[:i] + [f"{indent}{k.name} = {literal}{(' ' + comment.strip()) if comment.strip() else ''}"] + lines[i + 1:]

    _edit(mutate)
    return f"{dotted} = {literal}"

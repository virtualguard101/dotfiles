"""dotfiles config: show and edit the lists and tunables in dotfiles.toml. The schema, loading,
validation and the comment-preserving editor live in utils/common/config.py (the data layer)."""
from __future__ import annotations

import json
import os

from dotfiles.utils import ui
from dotfiles.utils.common.config import (
    SCHEMA,
    ConfigError,
    add,
    get,
    path,
    remove,
    set_value,
    validate,
)


def cmd(action: str, dotted: str | None, values: list[str]) -> int:
    if action == "path":
        ui.info(str(path()))
        return 0
    if action == "list":
        problems = validate()
        for k in SCHEMA:
            if dotted and not k.dotted.startswith(dotted):
                continue
            value = get(k.dotted)
            source = " (env " + k.env + ")" if k.env and os.environ.get(k.env) else ""
            if k.kind == "list":
                ui.info(f"{k.dotted} ({len(value)}){source}")
                for v in value:
                    ui.info(f"  {v}")
            elif k.kind == "tables":
                ui.info(f"{k.dotted} ({len(value)} tables)")
                for t in value:
                    ui.info("  " + ", ".join(f"{f}={t[f]}" for f in t))
            else:
                ui.info(f"{k.dotted} = {value}{source}")
            ui.dim(f"    {k.desc}")
        for p in problems:
            ui.err(p)
        return 1 if problems else 0
    if dotted is None:
        raise ConfigError(f"config {action} needs a KEY", 2)
    if action == "get":
        value = get(dotted)
        if isinstance(value, list):
            for v in value:
                ui.info(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
        else:
            ui.info(str(value))
        return 0
    if action in ("add", "remove"):
        if not values:
            raise ConfigError(f"config {action} needs at least one VALUE", 2)
        changes = add(dotted, values) if action == "add" else remove(dotted, values)
        for c in changes:
            ui.info(f"  {c}")
        ui.ok(f"{path().name} updated" if changes else "nothing to change")
        return 0
    if action == "set":
        if len(values) != 1:
            raise ConfigError("config set needs exactly one VALUE", 2)
        ui.ok(set_value(dotted, values[0]))
        return 0
    raise ConfigError(f"unknown config action: {action}", 2)

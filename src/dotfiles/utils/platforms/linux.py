"""Linux-only helpers: /etc/os-release, Arch detection and the /proc process listing."""
from __future__ import annotations

import os
from functools import cache

from dotfiles.utils.platforms import Proc, _decode


def list_processes() -> list[Proc]:
    uid = os.getuid()
    out = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            base = f"/proc/{entry.name}"
            with open(f"{base}/comm", encoding="utf-8", errors="replace") as fh:
                name = fh.read().strip().lower()
            try:
                exe = os.readlink(f"{base}/exe")
            except OSError:
                exe = ""
            with open(f"{base}/cmdline", "rb") as fh:
                argv = tuple(_decode(a) for a in fh.read().split(b"\0") if a)
            out.append(Proc(int(entry.name), name, exe, argv))
        except OSError:
            continue
    return out


@cache
def os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    key, sep, value = line.strip().partition("=")
                    if sep:
                        data[key] = value.strip().strip('"')
            break
        except OSError:
            continue
    return data


def is_arch_like() -> bool:
    rel = os_release()
    return rel.get("ID") == "arch" or "arch" in rel.get("ID_LIKE", "").split()

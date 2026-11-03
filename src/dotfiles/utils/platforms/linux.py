"""Linux-only helpers: /etc/os-release, distro and package-manager detection, and the /proc
process listing."""
from __future__ import annotations

import os
from functools import cache

from dotfiles.utils.platforms import Proc, _decode, have


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


def is_debian_like() -> bool:
    """Debian and its derivatives (Kali, Ubuntu, ...): ID_LIKE names debian."""
    rel = os_release()
    return rel.get("ID") == "debian" or "debian" in rel.get("ID_LIKE", "").split()


# package manager -> its manifest in packages/ (one per manager, shared by the distros that use it)
PACKAGE_MANIFESTS = {"pacman": "archpkg.txt", "apt": "aptpkg.txt"}


def package_manager() -> str | None:
    """The system package manager the manifests support: pacman on Arch-like distros, apt on
    Debian-like ones; None elsewhere."""
    if is_arch_like() and have("pacman"):
        return "pacman"
    if is_debian_like() and have("apt-get") and have("dpkg-query"):
        return "apt"
    return None


def distro() -> str:
    """os-release ID (e.g. arch, kali, debian); "linux" when it cannot be read. Names the per-distro
    /etc backup directory, so it must stay stable across reinstalls (unlike the hostname)."""
    value = os_release().get("ID", "").strip().lower()
    return value if value and value.replace("-", "").replace("_", "").isalnum() else "linux"

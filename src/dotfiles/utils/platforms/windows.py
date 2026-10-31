"""Windows-only helpers: PowerShell, the tasklist process listing, ACLs and the scoop location.
Importable on every platform (nothing runs at import time), so the logic stays testable on Linux."""
from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path

from dotfiles.utils.platforms import Proc, Result, have, home, run


def list_processes() -> list[Proc]:
    r = run(["tasklist", "/fo", "csv", "/nh"], timeout=60)
    out = []
    for row in csv.reader(io.StringIO(r.stdout)):
        if len(row) >= 2 and row[1].isdigit():
            name = row[0].lower()
            out.append(Proc(int(row[1]), name[:-4] if name.endswith(".exe") else name))
    return out


def powershell(script: str, timeout: float = 120) -> Result:
    """Windows: run a PowerShell snippet with -NoProfile, forcing UTF-8 output."""
    exe = "pwsh" if have("pwsh") else "powershell"
    prefix = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
    return run([exe, "-NoProfile", "-NonInteractive", "-Command", prefix + script], timeout=timeout)


def scoop_dir() -> Path:
    return Path(os.environ["SCOOP"]) if os.environ.get("SCOOP") else home() / "scoop"


ALLOWED_SIDS_EXTRA = ("S-1-5-18", "S-1-5-32-544")   # SYSTEM, Administrators


def current_sid() -> str:
    out = run(["whoami", "/user", "/fo", "csv", "/nh"]).stdout.strip()
    m = re.search(r'"(S-1-[\d-]+)"', out)
    return m.group(1) if m else ""


def restrict_acl(path: Path, is_dir: bool) -> bool:
    """Windows: drop inheritance; only the current user, SYSTEM and Administrators keep access."""
    inh = "(OI)(CI)" if is_dir else ""
    sids = [current_sid(), *ALLOWED_SIDS_EXTRA]
    args = ["icacls", str(path), "/inheritance:r"]
    for sid in sids:
        if sid:
            args += ["/grant:r", f"*{sid}:{inh}F"]
    return run([*args, "/q"]).ok

"""Windows system settings: registry keys exported into system/windows/registry/, the Windows
counterpart of the /etc backup (a separate chezmoi instance whose source dir is system/windows/).

Opt-in: dotfiles.toml [windows] registry lists the keys (empty by default, so nothing here runs
until you choose keys). Each key is one `reg export` file, stored as UTF-8 text with LF line ends so
git can diff it, and encrypted when the secret scan finds something in it. Restoring shows a diff
against the live key; --apply runs `reg import`, which only merges: values added since the backup
are not removed.

Everything that touches Windows goes through reg(), so tests replace that one function.
"""
from __future__ import annotations

import codecs
import difflib
import re
import tempfile
from pathlib import Path

from dotfiles.utils import platforms, ui
from dotfiles.utils.common import chez
from dotfiles.utils.common import config as dotconf

# a hive and at least one subkey: a whole hive is never exported
_KEY = re.compile(r"^(HKCU|HKLM|HKCR|HKU|HKCC|HKEY_CURRENT_USER|HKEY_LOCAL_MACHINE|HKEY_CLASSES_ROOT|HKEY_USERS|HKEY_CURRENT_CONFIG)\\[^\\].*$",
                  re.I)
_NEEDS_ADMIN = re.compile(r"^(HKLM|HKCR|HKEY_LOCAL_MACHINE|HKEY_CLASSES_ROOT)\\", re.I)


def reg(*args: str) -> platforms.Result:
    return platforms.run(["reg", *args])


def registry_keys() -> list[str]:
    return list(dotconf.get("windows.registry"))


def file_name(key: str) -> str:
    """HKCU\\Software\\Foo Bar -> HKCU_Software_Foo_Bar.reg"""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_") + ".reg"


def key_files(keys: list[str]) -> dict[str, str]:
    """file name -> key, refusing malformed keys and two keys that would share a file."""
    out: dict[str, str] = {}
    for key in keys:
        if not _KEY.match(key):
            raise ui.Abort(f"windows.registry: {key!r} is not a registry key (HKCU\\Software\\... , a hive and a subkey)", 2)
        name = file_name(key)
        if name in out and out[name] != key:
            raise ui.Abort(f"windows.registry: {key!r} and {out[name]!r} would be stored in the same file {name}", 2)
        out[name] = key
    return out


def decode_reg(data: bytes) -> str:
    """`reg export` writes UTF-16 with a BOM and CRLF -> UTF-8 text with LF."""
    text = data.decode("utf-16") if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE) else data.decode("utf-8-sig")
    return text.replace("\r\n", "\n")


def encode_reg(text: str) -> bytes:
    """The inverse, in the form `reg import` reads: UTF-16 LE with a BOM and CRLF."""
    return codecs.BOM_UTF16_LE + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le")


def export_key(key: str) -> str | None:
    """The key as .reg text; None when it does not exist or cannot be read."""
    with tempfile.TemporaryDirectory(dir=platforms.runtime_dir()) as d:
        out = Path(d) / "key.reg"
        if not reg("export", key, str(out), "/y").ok or not out.is_file():
            return None
        return decode_reg(out.read_bytes())


def header_key(text: str) -> str | None:
    """The first key line of an export: [HKEY_CURRENT_USER\\Software\\Foo] -> HKEY_CURRENT_USER\\Software\\Foo"""
    m = re.search(r"^\[(.+)\]\s*$", text, re.M)
    return m.group(1) if m else None


def status() -> None:
    ui.section("Registry keys (system/windows)")
    keys = registry_keys()
    if not keys:
        ui.info("none configured: dotfiles config add windows.registry HKCU\\Software\\...")
        return
    source = chez.system_source_dir() / "registry"
    stored = {p.name for p in source.glob("*")} if source.is_dir() else set()
    for name, key in key_files(keys).items():
        have = name in stored or f"encrypted_{name}.age" in stored
        ui.info(f"  {ui.pad(key, 64)}{'backed up' if have else 'not backed up yet'}")


def backup(run) -> None:
    ui.section("Registry keys -> system/windows/ (separate chezmoi instance)")
    keys = registry_keys()
    if not keys:
        ui.info("none configured (dotfiles config add windows.registry HKCU\\Software\\...)")
        return
    from dotfiles.cli import etc
    from dotfiles.cli.backup import has_secret

    files = key_files(keys)
    chez.system_source_dir().mkdir(parents=True, exist_ok=True)
    n = changed = 0
    with etc._Stage() as stage:
        for name, key in files.items():
            text = export_key(key)
            if text is None:
                ui.warn(f"cannot export (missing key, or no access): {key}")
                continue
            dst = stage.dir / "registry" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text, encoding="utf-8", newline="\n")
            encrypt = has_secret(dst)
            n += 1
            if etc.unchanged(stage, dst, encrypt):
                continue
            if encrypt:
                ui.info(f"  encrypt  {key}")
                stage.cz(*run.flags, "add", "--encrypt", "--", str(dst))
            else:
                ui.info(f"  plain    {key}")
                if not stage.cz(*run.flags, "add", "--secrets", "error", "--", str(dst)).ok:
                    ui.warn(f"chezmoi secret scan hit; switching to encrypted: {key}")
                    stage.cz(*run.flags, "add", "--encrypt", "--", str(dst))
            changed += 1
        managed = stage.cz("managed", "--include", "files", "--path-style", "absolute").stdout.splitlines()
        for t in sorted(managed):
            leaf = Path(t).name
            if leaf not in files:
                ui.note(f"in system/windows but no longer listed in windows.registry: registry/{leaf}")
    ui.ok(f"exported {n} registry keys, {changed} new or changed (staging area removed)")


def cmd_restore(apply: bool, filters: list[str]) -> int:
    source = chez.system_source_dir()
    if not (source / "registry").is_dir():
        raise ui.Abort("no registry backup in system/windows/; run dotfiles backup first", 2)
    if apply and not platforms.is_admin() and any(_NEEDS_ADMIN.match(k) for k in registry_keys()):
        raise ui.Abort("importing into HKLM / HKCR needs an elevated (administrator) shell", 2)
    from dotfiles.cli import etc

    wanted = [f.lower() for f in filters]
    changed = 0
    ui.info("source: system/windows")
    with etc._Stage(source) as stage:
        r = stage.cz("apply", "--force", "--no-tty")
        if not r.ok:
            raise ui.Abort(f"failed to decrypt system/windows: {r.stderr.strip()[:300]}")
        # compared as Paths: chezmoi prints C:/x/y, Path(...) iterates as C:\x\y (a str comparison never matches)
        encrypted = {Path(p) for p in stage.cz("managed", "-i", "encrypted", "-p", "absolute").stdout.splitlines()}
        for f in sorted((stage.dir / "registry").glob("*.reg")):
            stored = f.read_text(encoding="utf-8")
            key = header_key(stored)
            if key is None:
                ui.warn(f"no key line in {f.name}; skipped")
                continue
            if wanted and not any(w in (key.lower(), f.name.lower()) for w in wanted):
                continue
            live = export_key(key) or ""
            if live == stored:
                continue
            changed += 1
            ui.section(key)
            if f in encrypted:
                ui.info("(encrypted file; the content diff is not shown in the terminal)")
            else:
                diff = difflib.unified_diff(live.splitlines(), stored.splitlines(), "live", "backup", lineterm="", n=2)
                ui.info("\n".join(diff).rstrip() or "(differs only in line endings)")
            if not apply:
                continue
            with tempfile.TemporaryDirectory(dir=platforms.runtime_dir()) as d:
                tmp = Path(d) / "import.reg"
                tmp.write_bytes(encode_reg(stored))
                if not reg("import", str(tmp)).ok:
                    ui.warn(f"reg import failed: {key}")
    if not changed:
        ui.ok("the registry matches the backup")
    elif not apply:
        ui.info("this was a preview; add --apply to import (merges: values added since the backup stay)")
    return 0

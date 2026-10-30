"""Filesystem logic of the Windows cleanup targets: %LOCALAPPDATA% etc. are pointed at temp
directories so the tests run on any platform. Process detection, file locks, admin rights and
external commands are not covered (those need real Windows)."""
import os
import time
from pathlib import Path

import pytest

from dotfiles import clean, sysinfo, ui
from dotfiles.clean import windows as win


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "me"
    local, roaming, temp = home / "AppData" / "Local", home / "AppData" / "Roaming", home / "AppData" / "Local" / "Temp"
    for d in (local, roaming, temp):
        d.mkdir(parents=True)
    for mod in (win, sysinfo):
        for name, value in (("home", home), ("localappdata", local), ("appdata", roaming)):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, lambda v=value: v)
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("SCOOP", str(home / "scoop"))
    monkeypatch.setattr(sysinfo, "proc_running", lambda names: False)
    monkeypatch.setattr(sysinfo, "file_locked", lambda p: False)
    monkeypatch.setattr(win, "pid_lock", lambda p: False)
    return home, local, roaming, temp


def ctx(deep=False):
    return clean.Ctx(deep=deep, dry=True)


def by_id(target_id):
    return next(t for t in win.targets() if t.id == target_id)


def old(path: Path, days=30):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_temp_keeps_recent_and_dirs_with_recent_children(env):
    _, _, _, temp = env
    (temp / "old.tmp").write_text("x")
    old(temp / "old.tmp")
    (temp / "new.tmp").write_text("x")
    busy = temp / "olddir"
    busy.mkdir()
    (busy / "fresh").write_text("x")      # a child file is new: keep the whole dir
    old(busy)
    stale = temp / "staledir"
    stale.mkdir()
    (stale / "f").write_text("x")
    old(stale / "f")
    old(stale)
    names = {p.name for p in by_id("temp").paths(ctx())}
    assert names == {"old.tmp", "staledir"}


def test_scoop_old_versions_and_stale_cache(env):
    home = env[0]
    app = home / "scoop" / "apps" / "git"
    for v in ("2.40", "2.41"):
        (app / v).mkdir(parents=True)
    os.symlink(app / "2.41", app / "current", target_is_directory=True)
    (app / "2.41" / "manifest.json").write_text('{"version": "2.41"}')
    cache = home / "scoop" / "cache"
    cache.mkdir()
    for name in ("git#2.40#x.zip", "git#2.41#x.zip", "gone#1.0#y.zip"):
        (cache / name).write_text("z")
    names = {p.name for p in by_id("scoop").paths(ctx())}
    assert names == {"2.40", "git#2.40#x.zip", "gone#1.0#y.zip"}
    assert "git#2.41#x.zip" in {p.name for p in by_id("scoop").paths(ctx(deep=True))}


def test_electron_and_vscode_family(env):
    _, _, roaming, _ = env
    code = roaming / "Code"
    for d in ("Cache", "CachedData", "User"):
        (code / d).mkdir(parents=True)
    app = roaming / "SomeApp"
    (app / "Partitions" / "p1" / "Code Cache").mkdir(parents=True)
    (app / "GPUCache").mkdir()
    (app / "IndexedDB").mkdir()      # offline data, untouched
    (roaming / "NotElectron" / "data").mkdir(parents=True)
    rel = {p.relative_to(roaming).as_posix() for p in by_id("electron").paths(ctx())}
    assert rel == {"Code/Cache", "Code/CachedData", "SomeApp/GPUCache", "SomeApp/Partitions/p1/Code Cache"}


def test_browser_profiles(env):
    _, local, _, _ = env
    root = local / "Microsoft" / "Edge" / "User Data"
    for d in ("Default/Cache", "Profile 1/Code Cache", "Default/Local Storage", "ShaderCache"):
        (root / d).mkdir(parents=True)
    rel = {p.relative_to(root).as_posix() for p in by_id("browser").paths(ctx())}
    assert rel == {"Default/Cache", "Profile 1/Code Cache", "ShaderCache"}


def test_updaters(env):
    _, local, _, _ = env
    d = local / "@acme-app-updater"
    (d / "pending").mkdir(parents=True)
    (d / "installer.exe").write_text("x")
    assert {p.name for p in by_id("updaters").paths(ctx())} == {"pending", "installer.exe"}


def test_deleter_scope_on_windows_layout(env):
    home = env[0]
    d = clean.make_ctx(win, dry=True, deep=False).deleter
    d.assert_deletable(home / "AppData" / "Local" / "Temp" / "x")
    with pytest.raises(ui.Abort):
        d.assert_deletable(home / "AppData" / "Local")        # a protected directory itself

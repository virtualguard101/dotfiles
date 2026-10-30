"""Platform ownership: rendering the .chezmoiignore template from both platform views, and
maintaining platforms.toml."""
import shutil

import pytest

from dotfiles import chez, sysinfo

needs_chezmoi = pytest.mark.skipif(not sysinfo.have("chezmoi"), reason="requires chezmoi")


def render_ignore(os_name: str) -> set[str]:
    r = chez.cz("execute-template", "--file", str(chez.source_state_dir() / ".chezmoiignore"), os_override=os_name)
    assert r.ok, r.stderr
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


@needs_chezmoi
def test_ignore_other_platform_targets_and_their_exclusive_parents():
    linux = render_ignore("linux")
    windows = render_ignore("windows")
    # Windows targets are ignored on Linux, along with parents exclusive to Windows
    # (otherwise readonly_Documents would chmod ~/Documents to read-only)
    assert {"AppData", "Documents", "Documents/PowerToys", ".docker", ".wakatime.cfg"} <= linux
    # parents used by both platforms must not be ignored
    assert ".config" not in linux and ".config" not in windows
    assert {".ssh", ".ssh/config", ".agents", ".config/fcitx5", ".bashrc"} <= windows
    assert ".gitconfig" not in linux | windows   # shared


@needs_chezmoi
def test_every_managed_target_has_an_owner():
    assert chez.check_platforms() == []


@needs_chezmoi
@pytest.mark.parametrize("os_name, absent", [("linux", "Documents"), ("windows", ".ssh")])
def test_other_platform_dirs_are_not_managed(os_name, absent):
    dirs = chez.managed(include="dirs", exclude="externals", os_override=os_name)
    assert absent not in dirs


def test_owner_and_covering():
    platforms = {"shared": [".gitconfig"], "linux": [".config/fcitx5"], "windows": ["AppData"]}
    assert chez.owner_of(".config/fcitx5/profile", platforms) == "linux"
    assert chez.owner_of(".config/fcitx5x", platforms) is None   # not inside the directory
    assert chez.owner_of("AppData/Roaming/x", platforms) == "windows"
    assert chez.owner_of(".gitconfig", platforms) == "shared"


def test_register_platform_keeps_header(tmp_path, monkeypatch):
    f = tmp_path / "platforms.toml"
    shutil.copy(chez.platforms_file(), f)
    header = f.read_text(encoding="utf-8").split("[platforms]")[0]
    monkeypatch.setattr(chez, "platforms_file", lambda: f)
    assert chez.register_platform(".config/kwinrc", "linux", dry=False)
    assert not chez.register_platform(".config/kwinrc", "linux", dry=False)   # already registered
    assert not chez.register_platform(".config/fcitx5/new", "linux", dry=False)  # covered by the directory entry
    text = f.read_text(encoding="utf-8")
    assert text.startswith(header)
    assert chez.load_platforms()["linux"].count(".config/kwinrc") == 1

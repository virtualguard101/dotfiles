"""平台归属：.chezmoiignore 模板渲染（两端视角）与 platforms.toml 的维护。"""
import shutil

import pytest

from dotfiles import chez, sysinfo

needs_chezmoi = pytest.mark.skipif(not sysinfo.have("chezmoi"), reason="需要 chezmoi")


def render_ignore(os_name: str) -> set[str]:
    r = chez.cz("execute-template", "--file", str(chez.source_state_dir() / ".chezmoiignore"), os_override=os_name)
    assert r.ok, r.stderr
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


@needs_chezmoi
def test_ignore_other_platform_targets_and_their_exclusive_parents():
    linux = render_ignore("linux")
    windows = render_ignore("windows")
    # Windows 的目标在 Linux 上忽略，连同只属于 Windows 的父目录（否则 readonly_Documents 会把 ~/Documents 改成只读）
    assert {"AppData", "Documents", "Documents/PowerToys", ".docker", ".wakatime.cfg"} <= linux
    # 两端都有目标的父目录不能被忽略
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
    assert chez.owner_of(".config/fcitx5x", platforms) is None   # 不是前缀目录
    assert chez.owner_of("AppData/Roaming/x", platforms) == "windows"
    assert chez.owner_of(".gitconfig", platforms) == "shared"


def test_register_platform_keeps_header(tmp_path, monkeypatch):
    f = tmp_path / "platforms.toml"
    shutil.copy(chez.platforms_file(), f)
    header = f.read_text(encoding="utf-8").split("[platforms]")[0]
    monkeypatch.setattr(chez, "platforms_file", lambda: f)
    assert chez.register_platform(".config/kwinrc", "linux", dry=False)
    assert not chez.register_platform(".config/kwinrc", "linux", dry=False)   # 已登记
    assert not chez.register_platform(".config/fcitx5/new", "linux", dry=False)  # 已被目录覆盖
    text = f.read_text(encoding="utf-8")
    assert text.startswith(header)
    assert chez.load_platforms()["linux"].count(".config/kwinrc") == 1

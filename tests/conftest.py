"""Fixtures shared by the tests that run a second chezmoi instance over system/ (etc, winsys)."""
import pytest

from dotfiles.utils import platforms
from dotfiles.utils.common import chez


@pytest.fixture
def system_repo(tmp_path, monkeypatch):
    """A throwaway age key and chezmoi config (via XDG_CONFIG_HOME), an empty system/<distro>
    source, and a fake /etc under tmp_path."""
    if not platforms.have("chezmoi"):
        pytest.skip("requires chezmoi")
    conf = tmp_path / "config" / "chezmoi"
    conf.mkdir(parents=True)
    r = platforms.run(["chezmoi", "age-keygen", "--output", str(conf / "key.txt")])
    assert r.ok, r.stderr
    key_text = (conf / "key.txt").read_text(encoding="utf-8")
    recipient = next(ln.split(": ", 1)[1] for ln in key_text.splitlines() if ln.lower().startswith("# public key: "))
    (conf / "chezmoi.toml").write_text(    # forward slashes: a Windows path would need its backslashes escaped in TOML
        f'encryption = "age"\n[age]\n  identity = "{(conf / "key.txt").as_posix()}"\n  recipient = "{recipient}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    source = tmp_path / "system" / "testdistro"
    monkeypatch.setattr(chez, "system_source_dir", lambda distro=None: source)
    monkeypatch.setattr(platforms, "runtime_dir", lambda: tmp_path)
    etc_dir = tmp_path / "etc"
    etc_dir.mkdir()
    return source, etc_dir


@pytest.fixture
def link_dir():
    """link_dir(link, target): a directory link. A directory junction on Windows (what scoop's
    `current` is; needs no privilege, unlike a symlink), a symlink elsewhere."""
    import os
    import subprocess
    import sys

    def make(link, target):
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
        else:
            os.symlink(target, link, target_is_directory=True)
    return make


def pytest_collection_modifyitems(config, items):
    """The /etc backup (etc.py, restore's `system` step) is Linux-only: it stages a fake /etc with
    POSIX paths, modes and distro names, so its tests are skipped on Windows."""
    import sys
    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(reason="/etc backup is Linux-only")
    for item in items:
        if item.path.name == "test_etc.py" or item.name == "test_restore_skips_etc_from_another_distro":
            item.add_marker(skip)

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
    (conf / "chezmoi.toml").write_text(
        f'encryption = "age"\n[age]\n  identity = "{conf / "key.txt"}"\n  recipient = "{recipient}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    source = tmp_path / "system" / "testdistro"
    monkeypatch.setattr(chez, "system_source_dir", lambda distro=None: source)
    monkeypatch.setattr(platforms, "runtime_dir", lambda: tmp_path)
    etc_dir = tmp_path / "etc"
    etc_dir.mkdir()
    return source, etc_dir

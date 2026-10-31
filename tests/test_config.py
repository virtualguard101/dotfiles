import shutil
import tomllib

import pytest

from dotfiles.cli import backup, clean
from dotfiles.cli.clean.base import Target
from dotfiles.utils.common import chez, config


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A scratch copy of the repo's dotfiles.toml; edits never touch the real file."""
    f = tmp_path / "dotfiles.toml"
    shutil.copy(chez.repo() / config.FILE_NAME, f)
    monkeypatch.setattr(config, "path", lambda: f)
    config.reload()
    yield f
    config.reload()


def test_repo_file_is_valid_and_covers_the_schema():
    config.reload()
    assert config.validate() == []
    doc = tomllib.loads((chez.repo() / config.FILE_NAME).read_text(encoding="utf-8"))
    for k in config.SCHEMA:
        if k.kind != "tables" or k.dotted != "clean.paths":   # clean.paths is optional (commented example)
            assert config._raw(k, doc) is not None, f"{k.dotted} missing from dotfiles.toml"


def test_add_turns_inline_array_into_one_item_per_line(cfg):
    assert config.add("backup.linux.ignore", [".config/spectaclerc"]) == ["+ .config/spectaclerc"]
    text = cfg.read_text(encoding="utf-8")
    assert 'ignore = [\n  ".config/spectaclerc",\n]' in text
    assert config.get("backup.linux.ignore") == [".config/spectaclerc"]


def test_edits_keep_comments_and_other_items(cfg):
    before = cfg.read_text(encoding="utf-8")
    comments = [ln for ln in before.splitlines() if ln.lstrip().startswith("#")]
    config.add("etc.extra", ["/etc/new.d"])
    config.remove("etc.extra", ["/etc/hostname"])
    after = cfg.read_text(encoding="utf-8")
    assert [ln for ln in after.splitlines() if ln.lstrip().startswith("#")] == comments
    extra = config.get("etc.extra")
    assert extra[-1] == "/etc/new.d" and "/etc/hostname" not in extra and "/etc/profile.d" in extra
    assert after.count("\n") == before.count("\n")      # one line in, one line out


def test_add_existing_is_a_no_op_and_remove_missing_fails(cfg):
    before = cfg.read_text(encoding="utf-8")
    assert config.add("etc.extra", ["/etc/hostname"]) == []
    with pytest.raises(config.ConfigError):
        config.remove("etc.extra", ["/etc/not-there"])
    assert cfg.read_text(encoding="utf-8") == before


def test_set_scalar_and_reject_bad_values(cfg):
    assert config.set_value("clean.linux.thumbnail_days", "30") == "clean.linux.thumbnail_days = 30"
    assert config.get("clean.linux.thumbnail_days") == 30
    before = cfg.read_text(encoding="utf-8")
    for dotted, value in (("clean.linux.thumbnail_days", "soon"), ("etc.extra", "x")):
        with pytest.raises(config.ConfigError):
            config.set_value(dotted, value)
    with pytest.raises(config.ConfigError):
        config.add("clean.linux.thumbnail_days", ["1"])     # not a list
    assert cfg.read_text(encoding="utf-8") == before


def test_missing_key_and_section_are_created(cfg):
    text = cfg.read_text(encoding="utf-8").replace("disabled = []\n", "")
    cfg.write_text(text, encoding="utf-8")
    config.reload()
    config.add("clean.disabled", ["docker"])
    assert config.get("clean.disabled") == ["docker"]
    assert config.get("clean.opt") == []                # neighbouring key untouched


def test_env_overrides_file(cfg, monkeypatch):
    monkeypatch.setenv("THUMBNAIL_DAYS", "7")
    assert config.get("clean.linux.thumbnail_days") == 7
    monkeypatch.setenv("THUMBNAIL_DAYS", "not-a-number")
    assert config.get("clean.linux.thumbnail_days") == 90


def test_validate_reports_typos_and_types():
    doc = {"backup": {"linux": {"track": ["x"], "trak": ["y"]}}, "etc": {"extra": "not-a-list"},
           "clean": {"paths": [{"id": "x", "tier": "opt", "desc": "d"}]}}
    problems = config.validate(doc)
    assert "unknown key backup.linux.trak" in problems
    assert "etc.extra must be a list of strings" in problems
    assert any("clean.paths[0] is missing paths" in p for p in problems)


@pytest.mark.parametrize("target, hit", [
    (".config/spectaclerc", True),
    (".local/share/konsole/bookmarks.xml", True),     # inside a directory pattern
    (".config/autostart/vboxclient.desktop", True),   # glob
    (".config/spectaclerc2", False),
    (".local/share/konsolex/a", False),
])
def test_backup_ignore_patterns(target, hit):
    patterns = [".config/spectaclerc", ".local/share/konsole/", ".config/autostart/vbox*"]
    assert backup.is_ignored(target, patterns) is hit


def test_clean_targets_apply_disabled_opt_and_paths(cfg, tmp_path, monkeypatch):
    class Mod:
        @staticmethod
        def targets():
            return [Target("a", "default", "A", paths=lambda c: []), Target("b", "default", "B", paths=lambda c: [])]

    (tmp_path / "junk").mkdir()
    monkeypatch.setattr(clean.platforms, "home", lambda: tmp_path)
    config.add("clean.disabled", ["a"])
    config.add("clean.opt", ["b"])
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write('\n[[clean.paths]]\nid = "junk"\ntier = "default"\ndesc = "junk dir"\npaths = ["~/junk", "~/absent"]\n')
    config.reload()
    got = {t.id: t for t in clean.targets(Mod)}
    assert set(got) == {"b", "junk"}
    assert got["b"].tier == "opt"
    assert got["junk"].paths(None) == [tmp_path / "junk"]


def test_clean_path_target_cannot_shadow_builtin(cfg):
    class Mod:
        @staticmethod
        def targets():
            return [Target("uv", "default", "uv", paths=lambda c: [])]

    with cfg.open("a", encoding="utf-8") as fh:
        fh.write('\n[[clean.paths]]\nid = "uv"\ntier = "opt"\ndesc = "x"\npaths = ["~/x"]\n')
    config.reload()
    with pytest.raises(Exception, match="clashes"):
        clean.targets(Mod)

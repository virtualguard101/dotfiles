import os

import pytest

from dotfiles import fsutil, ui


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "home"
    (root / "cache" / "a").mkdir(parents=True)
    (root / "cache" / "a" / "f").write_bytes(b"x" * 100)
    (root / "keep").mkdir()
    (root / "keep" / "important").write_bytes(b"y" * 1000)
    os.symlink(root / "keep", root / "cache" / "link")      # a link pointing outside the cache
    return root


def test_size_does_not_follow_links(tree):
    assert fsutil.size_of([tree / "cache"]) == 100


def test_delete_removes_link_not_target(tree):
    d = fsutil.Deleter(dry=False, safe_roots=[tree], protected=[tree])
    d.remove([tree / "cache"])
    assert not (tree / "cache").exists()
    assert (tree / "keep" / "important").read_bytes() == b"y" * 1000


def test_dry_run_deletes_nothing(tree):
    fsutil.Deleter(dry=True, safe_roots=[tree], protected=[tree]).remove([tree / "cache"])
    assert (tree / "cache" / "a" / "f").exists()


@pytest.mark.parametrize("bad", ["relative/path", "/", "PROTECTED", "OUTSIDE"])
def test_refuses_unsafe_paths(tree, tmp_path, bad):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = {"PROTECTED": tree, "OUTSIDE": outside}.get(bad, bad)
    d = fsutil.Deleter(dry=False, safe_roots=[tree], protected=[tree])
    with pytest.raises(ui.Abort):
        d.assert_deletable(target)

"""Platform ownership: rendering the .chezmoiignore template from both platform views, and
maintaining platforms.toml."""
import shutil
import tempfile
from pathlib import Path

import pytest

from dotfiles.utils import platforms
from dotfiles.utils.common import chez

needs_chezmoi = pytest.mark.skipif(not platforms.have("chezmoi"), reason="requires chezmoi")


def render_ignore(os_name: str, tags: list[str] | None = None) -> set[str]:
    """Render .chezmoiignore against an empty config, so this machine's own .tags never leak in
    (tags=None then really means a config written before tags existed)."""
    with tempfile.TemporaryDirectory() as d:
        empty = Path(d, "chezmoi.toml")
        empty.write_text("", encoding="utf-8")
        r = platforms.run([*chez.base_args(os_name, tags), "--config", str(empty), "execute-template",
                           "--file", str(chez.source_state_dir() / ".chezmoiignore")])
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
    assert chez.register_platform(".config/dotfiles-test/never-registered.conf", "linux", dry=False)
    assert not chez.register_platform(".config/dotfiles-test/never-registered.conf", "linux", dry=False)   # already registered
    assert not chez.register_platform(".config/fcitx5/new", "linux", dry=False)  # covered by the directory entry
    text = f.read_text(encoding="utf-8")
    assert text.startswith(header)
    assert chez.load_platforms()["linux"].count(".config/dotfiles-test/never-registered.conf") == 1


KDE = {".config/kwinrc", ".config/kdeglobals", ".local/share/konsole"}
UNITS = {".config/systemd/user/zeroclaw.service", ".config/systemd/user/taskdog-server.service"}
ARCH_TAGS = ["arch", "kde", "taskdog", "zeroclaw"]     # this Arch machine, with both programs installed


@needs_chezmoi
def test_requires_ignores_targets_whose_tag_is_missing():
    no_tags = render_ignore("linux")                         # config written before tags: no filtering
    arch = render_ignore("linux", ARCH_TAGS)
    kali = render_ignore("linux", ["kali", "debian", "gnome"])
    assert arch == no_tags
    # program tags: a user unit deploys only where the binary its ExecStart runs is installed
    assert UNITS <= kali and UNITS <= render_ignore("linux", ["arch", "kde"])
    assert KDE <= kali and not KDE & arch
    # parents shared with targets this machine keeps stay managed
    assert ".config" not in kali
    # ...and a parent left with only ignored targets is ignored with them: Kali has no user units,
    # and under .local/share only the KDE-tagged Konsole files
    assert {".config/systemd", ".config/systemd/user", ".local", ".local/share"} <= kali
    assert not {".config/systemd", ".local/share"} & arch
    assert ".config/systemd/user/kwallet_auto_unlock.service" in kali
    # [requires] arch: Kali keeps its own XDG user dirs
    assert ".config/user-dirs.dirs" in kali and ".config/user-dirs.dirs" not in arch
    assert render_ignore("windows", ["kde"]) == render_ignore("windows")    # requires never re-enables


@needs_chezmoi
def test_kali_view_drops_only_targets_of_missing_tags():
    kali_tags = ["kali", "debian", "gnome"]
    every = set(chez.managed(include="files", os_override="linux", tags=ARCH_TAGS))
    kali = set(chez.managed(include="files", os_override="linux", tags=kali_tags))
    assert kali < every
    dropped = every - kali
    missing = [t for tag, targets in chez.load_requires().items() if tag not in kali_tags for t in targets]
    assert all(chez.covering(t, missing) for t in dropped)
    assert {".zshrc", ".config/fcitx5/profile"} <= kali


def test_register_platform_keeps_other_tables(tmp_path, monkeypatch):
    f = tmp_path / "platforms.toml"
    shutil.copy(chez.platforms_file(), f)
    monkeypatch.setattr(chez, "platforms_file", lambda: f)
    requires = chez.load_requires()
    assert chez.register_platform(".config/dotfiles-test/x.conf", "linux", dry=False)
    assert chez.load_requires() == requires and requires
    assert ".config/dotfiles-test/x.conf" in chez.load_platforms()["linux"]
    text = f.read_text(encoding="utf-8")
    assert text.index("[platforms]") < text.index("[requires]")
    assert "\n\n[requires]" in text                           # the blank line before the next table stays


@needs_chezmoi
def test_check_platforms_validates_requires(tmp_path, monkeypatch):
    f = tmp_path / "platforms.toml"
    text = chez.platforms_file().read_text(encoding="utf-8")
    f.write_text(text + '\nBad_Tag = [".config/kwinrc"]\nnvidia = [".config/not-managed-anywhere"]\n', encoding="utf-8")
    monkeypatch.setattr(chez, "platforms_file", lambda: f)
    problems = chez.check_platforms()
    assert any("Bad_Tag" in p for p in problems)
    assert any(".config/not-managed-anywhere matches no entry" in p for p in problems)


@needs_chezmoi
@pytest.mark.parametrize("installed, tags", [
    ([], set()),
    ([".cargo/bin/zeroclaw"], {"zeroclaw"}),
    ([".cargo/bin/zeroclaw", ".local/bin/taskdog-server"], {"zeroclaw", "taskdog"}),
])
def test_program_tags_follow_the_installed_binaries(tmp_path, installed, tags):
    """.chezmoi.toml.tmpl adds a program tag when the binary exists under ~ (rendered against a
    fake home, so this machine's own programs do not matter)."""
    import json
    import tomllib
    for rel in installed:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()
    r = platforms.run([*chez.base_args(), "--override-data", json.dumps({"chezmoi": {"homeDir": str(tmp_path)}}),
                       "execute-template", "--init", "--file", str(chez.source_state_dir() / ".chezmoi.toml.tmpl")])
    assert r.ok, r.stderr
    rendered = set(tomllib.loads(r.stdout)["data"]["tags"])
    assert rendered & {"zeroclaw", "taskdog"} == tags



@needs_chezmoi
def test_retired_targets_are_not_owned_or_managed():
    """Every target in .chezmoiremove is retired: no ownership entry covers it and chezmoi does not
    manage it (both together would be "inconsistent state" at apply)."""
    r = platforms.run([*chez.base_args("linux"), "execute-template", "--file", str(chez.source_state_dir() / ".chezmoiremove")])
    assert r.ok, r.stderr
    retired = [ln.strip() for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("#")]
    assert ".zimrc" in retired
    owners = chez.load_platforms()
    assert not [t for t in retired if chez.owner_of(t, owners)]
    managed = set(chez.managed(include="files,symlinks,externals", exclude="", os_override="linux", tags=ARCH_TAGS))
    assert not set(retired) & managed

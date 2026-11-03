from pathlib import Path

import pytest

from dotfiles.utils.common import guard

FAKE_GH = "ghp_" + "a1B2" * 9   # built at runtime: the full token never appears in the source


@pytest.mark.parametrize("source, target", [
    ("home/private_dot_ssh/encrypted_config.age", ".ssh/config"),
    ("home/encrypted_dot_wakatime.cfg.age", ".wakatime.cfg"),
    ("home/dot_gitconfig.tmpl", ".gitconfig"),
    # encrypted template: chezmoi puts .age outermost (encrypted_x.tmpl.age)
    ("home/private_dot_config/Code/User/encrypted_settings.json.tmpl.age", ".config/Code/User/settings.json"),
    ("home/dot_x.tmpl.literal", ".x.tmpl"),
    ("home/dot_aria2/create_empty_aria2.session", ".aria2/aria2.session"),
    ("home/readonly_Documents/PowerToys/Backup/x.ptb", "Documents/PowerToys/Backup/x.ptb"),
    ("home/AppData/Roaming/Code/User/keybindings.json", "AppData/Roaming/Code/User/keybindings.json"),
    ("home/literal_dot_npmrc", "dot_npmrc"),
    ("system/etc/sudoers", "/etc/sudoers"),                      # legacy layout without a distro
    ("system/arch/etc/sudoers", "/etc/sudoers"),
    ("system/kali/etc/grub.d/executable_40_custom", "/etc/grub.d/40_custom"),
    # not deployment targets: repo-root tooling and chezmoi's own config
    ("src/dotfiles/utils/common/guard/checks.py", None),
    ("packages/archpkg.txt", None),
    ("bootstrap.sh", None),
    ("home/.chezmoiignore", None),
    ("home/.chezmoidata/platforms.toml", None),
])
def test_target_of_with_chezmoiroot(source, target):
    assert guard.target_of(source, root="home") == target


def test_plaintext_token_is_blocked_anywhere():
    for path in ("home/dot_profile", "src/dotfiles/x.py", "packages/archpkg.txt"):
        rules = [f.rule for f in guard.check_content(path, f"export GH_TOKEN={FAKE_GH}\n".encode())]
        assert "github-token" in rules, path


def test_allow_marker():
    data = f"GH_TOKEN={FAKE_GH}  # gitleaks:allow\n".encode()
    assert not [f for f in guard.check_content("home/dot_profile", data) if f.level == "error"]


def test_policy_requires_encryption_for_sensitive_targets(monkeypatch):
    monkeypatch.setattr(guard.paths, "_SOURCE_ROOT", "home")
    errors = lambda p, d=b"x\n": [f.rule for f in guard.check_content(p, d) if f.level == "error"]
    assert any(r.startswith("must-encrypt") for r in errors("home/dot_wakatime.cfg"))
    assert any(r.startswith("must-encrypt") for r in errors("home/private_dot_ssh/private_id_ed25519"))
    assert any(r.startswith("must-encrypt") for r in errors("system/etc/sudoers"))
    assert not errors("home/private_dot_ssh/id_ed25519.pub")
    assert not errors("home/dot_vimrc")


def test_age_files_must_be_ciphertext(monkeypatch):
    monkeypatch.setattr(guard.paths, "_SOURCE_ROOT", "home")
    rules = [f.rule for f in guard.check_content("home/encrypted_dot_x.age", b"plain\n")]
    assert "age-not-ciphertext" in rules
    armored = b"-----BEGIN AGE ENCRYPTED FILE-----\nYWdl\n-----END AGE ENCRYPTED FILE-----\n"
    assert not guard.check_content("home/encrypted_dot_x.age", armored)
    assert "age-without-encrypted-prefix" in [f.rule for f in guard.check_content("home/dot_x.age", armored)]


def test_scan_exit_codes(tmp_path):
    clean, dirty = tmp_path / "a", tmp_path / "b"
    clean.write_text("set number\n")
    dirty.write_text(f"token = {FAKE_GH}\n")
    assert guard.cmd_scan([str(clean)], quiet=True) == 0
    assert guard.cmd_scan([str(dirty)], quiet=True) == 1


@pytest.mark.parametrize("text, hit", [
    ("/home/alice/x", True),
    ("/home/alice", True),
    (r"C:\Users\alice\x", True),
    (r'"C:\\Users\\alice\\x"', True),                  # JSON-escaped
    ("/c/Users/alice/x", True),                         # MSYS
    ("/home/alicex/y", False),                          # a different user that merely shares a prefix
    ("/home/username/file", False),                     # generic example in a comment
    (r"C:\Users\bob\x", False),
    ("$HOME/x", False),
    ("~/x", False),
])
def test_home_path_only_matches_this_user(text, hit):
    assert bool(guard.home_path_rx(Path("/home/alice")).search(text.encode())) is hit, text


def test_home_path_rule_ignores_examples_but_flags_this_home(monkeypatch):
    monkeypatch.setattr(guard.checks, "_this_home_rx", lambda: guard.home_path_rx(Path("/home/alice")))
    def rules(data: bytes) -> list[str]:
        return [f.rule for f in guard.check_content("src/x.py", data)]
    assert "absolute-home-path" not in rules(b'# says "set by /home/username/file"\n')
    assert "absolute-home-path" in rules(b"cache = '/home/alice/.cache'\n")


def test_registered_check_runs_in_every_mode(monkeypatch):
    """The extension interface: a check appended to CHECKS runs wherever check_content does."""
    def no_todo(ctx):
        if ctx.text is not None and b"TODO-SECRET" in ctx.text:
            yield guard.Finding("error", "todo-secret", ctx.path, ctx.text.count(b"\n", 0, ctx.text.index(b"TODO-SECRET")) + 1)

    monkeypatch.setattr(guard.checks, "CHECKS", [*guard.CHECKS, no_todo])
    found = guard.check_content("src/x.py", b"ok\nTODO-SECRET here\n")
    assert [(f.rule, f.line) for f in found] == [("todo-secret", 2)]
    assert guard.check_content("home/dot_x.age", b"TODO-SECRET") != []     # still an age check failure ...
    assert all(f.rule != "todo-secret" for f in guard.check_content("home/dot_x.age", b"TODO-SECRET"))  # ... text checks skip ciphertext


def test_checks_can_be_run_selectively():
    data = b"token = " + b"ghp_" + b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + b"\n"
    assert [f.rule for f in guard.check_content("src/x.py", data, checks=[guard.checks.check_secrets])] == ["github-token"]
    assert guard.check_content("src/x.py", data, checks=[guard.checks.check_policy]) == []


def test_findings_never_carry_content():
    secret = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    for f in guard.check_content("home/dot_bashrc", f"export GH={secret}\n".encode()):
        assert secret not in str(f) and secret not in repr(f)


# ---------------------------------------------------------------- platform ownership (issue #8)

OWNERS = {"shared": [".gitconfig"], "linux": [".config/fcitx5", ".zshrc"], "windows": ["AppData"]}


@pytest.mark.parametrize("source, owned", [
    ("home/dot_zshrc", True),
    ("home/private_dot_config/private_fcitx5/profile", True),         # inside a directory entry
    ("home/dot_config/foo/bar.conf", False),                           # added with plain chezmoi add
    ("home/dot_config/fcitx5x/a", False),                              # a sibling, not inside the entry
    ("system/arch/etc/hosts", True),                                   # /etc: per distro, not platform-owned
    ("src/dotfiles/cli/backup.py", True),                              # not a deployment target
    ("home/.chezmoidata/platforms.toml", True),
])
def test_ownership_check(monkeypatch, source, owned):
    monkeypatch.setattr(guard.paths, "_SOURCE_ROOT", "home")
    rules = [f.rule for f in guard.check_content(source, b"x\n", owners=OWNERS)]
    assert (not any(r.startswith("no-platform-owner") for r in rules)) is owned, rules


def test_ownership_check_needs_owners(monkeypatch):
    monkeypatch.setattr(guard.paths, "_SOURCE_ROOT", "home")
    assert guard.check_content("home/dot_config/foo/bar.conf", b"x\n") == []    # e.g. scan, or no platforms.toml


def test_invalid_platforms_toml_is_an_error():
    findings = []
    assert guard.cli.owners_from(b"[platforms\nlinux = [", findings) is None
    assert [f.rule for f in findings] == ["platforms-toml-invalid"]


TRICKY = """# header with [brackets] and "quotes"
[platforms]
shared = [".gitconfig"]          # inline array, comment with ] and #
linux = [
  ".config/a b",   # trailing comment ]
  "escaped \\"quote\\"",
  ".local/share/konsole/配置方案 1.profile",
]
note = "a scalar is ignored"

[requires]
kde = [
  ".config/kwinrc",
]
"""


def test_platforms_parser_fallback_matches_tomllib(monkeypatch):
    import sys
    real = guard.parse_tables(TRICKY)
    assert real["platforms"]["linux"][1] == 'escaped "quote"'
    monkeypatch.setitem(sys.modules, "tomllib", None)      # Python < 3.11: no tomllib
    assert guard.parse_tables(TRICKY) == real
    repo_file = (Path(__file__).resolve().parents[1] / "home" / ".chezmoidata" / "platforms.toml").read_text(encoding="utf-8")
    monkeypatch.delitem(sys.modules, "tomllib")
    expected = guard.parse_tables(repo_file)
    monkeypatch.setitem(sys.modules, "tomllib", None)
    assert guard.parse_tables(repo_file) == expected

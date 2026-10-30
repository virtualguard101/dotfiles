import pytest

from dotfiles import guard

FAKE_GH = "ghp_" + "a1B2" * 9   # built at runtime: the full token never appears in the source


@pytest.mark.parametrize("source, target", [
    ("home/private_dot_ssh/encrypted_config.age", ".ssh/config"),
    ("home/encrypted_dot_wakatime.cfg.age", ".wakatime.cfg"),
    ("home/dot_gitconfig.tmpl", ".gitconfig"),
    ("home/readonly_Documents/PowerToys/Backup/x.ptb", "Documents/PowerToys/Backup/x.ptb"),
    ("home/AppData/Roaming/Code/User/keybindings.json", "AppData/Roaming/Code/User/keybindings.json"),
    ("home/literal_dot_npmrc", "dot_npmrc"),
    ("system/etc/sudoers", "/etc/sudoers"),
    ("system/etc/grub.d/executable_40_custom", "/etc/grub.d/40_custom"),
    # not deployment targets: repo-root tooling and chezmoi's own config
    ("src/dotfiles/guard.py", None),
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
    monkeypatch.setattr(guard, "_SOURCE_ROOT", "home")
    errors = lambda p, d=b"x\n": [f.rule for f in guard.check_content(p, d) if f.level == "error"]
    assert any(r.startswith("must-encrypt") for r in errors("home/dot_wakatime.cfg"))
    assert any(r.startswith("must-encrypt") for r in errors("home/private_dot_ssh/private_id_ed25519"))
    assert any(r.startswith("must-encrypt") for r in errors("system/etc/sudoers"))
    assert not errors("home/private_dot_ssh/id_ed25519.pub")
    assert not errors("home/dot_vimrc")


def test_age_files_must_be_ciphertext(monkeypatch):
    monkeypatch.setattr(guard, "_SOURCE_ROOT", "home")
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

"""/etc backup on dpkg systems (Debian, Kali): which files packages own, and which conffiles the
user changed. Checked against fake dpkg databases (<admin>/status, info/*.list, diversions)."""
import hashlib
import os
from pathlib import Path

from dotfiles.cli import etc


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


STOCK = md5(b"stock\n")

STATUS = f"""Package: openssh-server
Status: install ok installed
Conffiles:
 /etc/ssh/sshd_config {STOCK}
 /etc/ssh/moduli {STOCK}
 /etc/ssh/old.conf {STOCK} obsolete
 /etc/ssh/gone.conf {STOCK} remove-on-upgrade
Description: secure shell server

Package: zsh-common
Status: install ok installed
Conffiles:
 /etc/zsh/zprofile {STOCK}

Package: kali-defaults
Status: install ok installed

Package: removed-but-not-purged
Status: deinstall ok config-files
Conffiles:
 /etc/leftover.conf {STOCK}
"""

# kali-defaults replaces the stock zprofile; dpkg moves zsh-common's copy to zprofile.original
DIVERSIONS = "/etc/zsh/zprofile\n/etc/zsh/zprofile.original\nkali-defaults\n"


def fake_system(tmp_path: Path) -> tuple[Path, Path]:
    root, admin = tmp_path / "root", tmp_path / "root/var/lib/dpkg"
    (admin / "info").mkdir(parents=True)
    (admin / "status").write_text(STATUS, encoding="utf-8")
    (admin / "diversions").write_text(DIVERSIONS, encoding="utf-8")
    (admin / "info/openssh-server.list").write_text("/.\n/etc\n/etc/ssh/sshd_config\n/usr/sbin/sshd\n", encoding="utf-8")
    (admin / "info/kali-defaults.list").write_text("/etc/zsh/zprofile\n", encoding="utf-8")
    files = {
        "etc/ssh/sshd_config": b"PermitRootLogin no\n",     # edited by the user
        "etc/ssh/moduli": b"stock\n",                       # untouched
        "etc/zsh/zprofile": b"kali's own version\n",        # the diverting package's file, not a conffile edit
        "etc/zsh/zprofile.original": b"stock\n",            # zsh-common's diverted copy, untouched
        "etc/leftover.conf": b"changed\n",                  # package removed: not live configuration
    }
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root, admin


def test_conffiles_skip_obsolete_and_removed_packages():
    conf = etc.dpkg_conffiles(STATUS)
    assert set(conf) == {"/etc/ssh/sshd_config", "/etc/ssh/moduli", "/etc/zsh/zprofile"}
    assert conf["/etc/ssh/moduli"] == STOCK


def test_diverted_conffile_lives_at_the_diverted_path():
    div = etc.dpkg_diversions(DIVERSIONS)
    assert div == {"/etc/zsh/zprofile": ("/etc/zsh/zprofile.original", "kali-defaults")}
    conf = etc.dpkg_conffiles(STATUS, div)
    assert "/etc/zsh/zprofile.original" in conf and "/etc/zsh/zprofile" not in conf


def test_modified_reports_only_real_edits(tmp_path):
    root, admin = fake_system(tmp_path)
    assert etc.dpkg_modified(admin, root=root) == ["/etc/ssh/sshd_config"]
    (root / "etc/ssh/moduli").unlink()                      # a deleted conffile is not "modified"
    assert etc.dpkg_modified(admin, root=root) == ["/etc/ssh/sshd_config"]


def test_owned_includes_package_lists_and_diverted_copies(tmp_path):
    _, admin = fake_system(tmp_path)
    assert etc.dpkg_owned(admin) == {"/etc/ssh/sshd_config", "/etc/zsh/zprofile", "/etc/zsh/zprofile.original"}


# ---------------------------------------------------------------- re-encryption churn

import pytest  # noqa: E402

from dotfiles.cli.backup import Runner  # noqa: E402
from dotfiles.utils import platforms  # noqa: E402
from dotfiles.utils.common import chez  # noqa: E402


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


def test_unchanged_compares_decrypted_content_mode_and_encryption(system_repo):
    source, etc_dir = system_repo
    source.mkdir(parents=True)
    with etc._Stage() as stage:
        dst = stage.dir / "etc" / "app.conf"
        dst.parent.mkdir(parents=True)
        dst.write_text("key = value\n", encoding="utf-8")
        os_chmod = os.chmod
        os_chmod(dst, 0o600)
        assert not etc.unchanged(stage, dst, encrypt=True)            # not in the source yet
        assert stage.cz("add", "--encrypt", "--", str(dst)).ok
        assert etc.unchanged(stage, dst, encrypt=True)
        assert not etc.unchanged(stage, dst, encrypt=False)           # would switch to plaintext
        os_chmod(dst, 0o644)
        assert not etc.unchanged(stage, dst, encrypt=True)            # mode changed
        os_chmod(dst, 0o600)
        dst.write_text("key = other\n", encoding="utf-8")
        assert not etc.unchanged(stage, dst, encrypt=True)            # content changed


def test_backup_twice_leaves_encrypted_sources_untouched(system_repo, monkeypatch):
    source, etc_dir = system_repo
    secret, plain = etc_dir / "secret.conf", etc_dir / "plain.conf"
    secret.write_text("password = hunter2\n", encoding="utf-8")
    secret.chmod(0o600)
    plain.write_text("option = 1\n", encoding="utf-8")
    plain.chmod(0o644)
    monkeypatch.setattr(etc, "collect_targets", lambda: {str(secret): "secret", str(plain): "plain"})

    def snapshot():
        return {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    etc.backup(Runner(dry=False))
    first = snapshot()
    assert any(p.name.startswith("encrypted_") for p in first) and len(first) == 2
    etc.backup(Runner(dry=False))
    assert snapshot() == first                                        # no re-encryption
    secret.write_text("password = changed\n", encoding="utf-8")
    etc.backup(Runner(dry=False))
    after = snapshot()
    assert after.keys() == first.keys()
    assert [p for p in after if after[p] != first[p]] == [p for p in after if p.name.startswith("encrypted_")]

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


@pytest.mark.parametrize("mode", [0o600, 0o440, 0o640, 0o644, 0o400, 0o755, 0o444])
def test_mode_attrs_match_what_chezmoi_add_records(system_repo, mode):
    from dotfiles.cli.backup import source_attrs
    source, _ = system_repo
    source.mkdir(parents=True)
    with etc._Stage() as stage:
        dst = stage.dir / "etc" / "f.conf"
        dst.parent.mkdir(parents=True)
        dst.write_text("x\n", encoding="utf-8")
        os.chmod(dst, mode)
        assert stage.cz("add", "--", str(dst)).ok
        name = Path(stage.cz("source-path", "--", str(dst)).stdout.strip()).name
    assert source_attrs(name) == etc.mode_attrs(mode)


@pytest.mark.parametrize("umask", [0o022, 0o002])
def test_backup_twice_no_churn_for_modes_chezmoi_cannot_express(system_repo, monkeypatch, umask):
    """sudoers is 0440 and some files are 0640: chezmoi's attributes cannot express those modes,
    and under umask 002 even 0644 / 0755 differ from what chezmoi derives. None of that may make
    an unchanged file count as changed."""
    source, etc_dir = system_repo
    files = {"sudoers": (0o440, "secret"), "shadowish": (0o640, "plain"), "crypttab": (0o644, "secret"),
             "hook.sh": (0o755, "plain"), "hosts": (0o644, "plain")}
    for name, (mode, _) in files.items():
        (etc_dir / name).write_text(f"{name} content\n", encoding="utf-8")
        os.chmod(etc_dir / name, mode)
    monkeypatch.setattr(etc, "collect_targets", lambda: {str(etc_dir / n): k for n, (_, k) in files.items()})

    def snapshot():
        return {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    old = os.umask(umask)
    try:
        etc.backup(Runner(dry=False))
        first = snapshot()
        assert len(first) == len(files)
        etc.backup(Runner(dry=False))
        assert snapshot() == first
        os.chmod(etc_dir / "hosts", 0o600)                 # a real mode change is still picked up:
        etc.backup(Runner(dry=False))                      # no longer world-readable -> encrypted
        assert "encrypted_private_hosts.age" in {p.name for p in snapshot()}
    finally:
        os.umask(old)


# ---------------------------------------------------------------- files identical to the package's

def fake_pacman_db(tmp_path: Path, backups: dict[str, bytes]) -> Path:
    db = tmp_path / "pacman"
    pkg = db / "local" / "sudo-1.9-1"
    pkg.mkdir(parents=True)
    lines = ["%FILES%", "etc/", *backups, "usr/bin/sudo", "", "%BACKUP%", *[f"{n}\t{md5(d)}" for n, d in backups.items()], ""]
    (pkg / "files").write_text("\n".join(lines), encoding="utf-8")
    other = db / "local" / "tree-2-1"                      # a package without backup files
    other.mkdir()
    (other / "files").write_text("%FILES%\nusr/bin/tree\n", encoding="utf-8")
    return db


def test_pacman_backups_reads_the_backup_section(tmp_path):
    db = fake_pacman_db(tmp_path, {"etc/sudoers": b"stock\n", "etc/sudo.conf": b"conf\n"})
    assert etc.pacman_backups(db) == {"/etc/sudoers": md5(b"stock\n"), "/etc/sudo.conf": md5(b"conf\n")}


def test_package_original_pacman(tmp_path, monkeypatch):
    db = fake_pacman_db(tmp_path, {"etc/sudoers": b"stock\n", "etc/sudo.conf": b"conf\n", "etc/gone.conf": b"x\n"})
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/sudoers").write_bytes(b"stock\n")            # untouched
    (root / "etc/sudo.conf").write_bytes(b"edited\n")         # edited by the user
    (root / "etc/hosts").write_bytes(b"stock\n")              # no record: not tracked by any package
    monkeypatch.setattr(etc, "PACMAN_DB", db)
    monkeypatch.setattr(platforms, "have", lambda tool: tool == "pacman")
    wanted = ["/etc/sudoers", "/etc/sudo.conf", "/etc/hosts", "/etc/gone.conf"]
    assert etc.package_original(wanted, root=root) == {"/etc/sudoers"}


def test_package_original_dpkg_covers_conffiles_and_plain_package_files(tmp_path, monkeypatch):
    root, admin = fake_system(tmp_path)
    (admin / "info/openssh-server.md5sums").write_text(f"{md5(b'shipped\n')}  etc/ssh/ssh_config.d/50-shipped.conf\n", encoding="utf-8")
    for rel, data in {"etc/ssh/ssh_config.d/50-shipped.conf": b"shipped\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    monkeypatch.setattr(etc, "DPKG_ADMIN", admin)
    monkeypatch.setattr(platforms, "have", lambda tool: tool == "dpkg-query")
    wanted = ["/etc/ssh/moduli", "/etc/ssh/sshd_config", "/etc/ssh/ssh_config.d/50-shipped.conf", "/etc/hostname"]
    # moduli (conffile) and the shipped drop-in match; the edited sshd_config and an unowned file do not
    assert etc.package_original(wanted, root=root) == {"/etc/ssh/moduli", "/etc/ssh/ssh_config.d/50-shipped.conf"}


def test_package_original_keeps_what_it_cannot_verify(tmp_path, monkeypatch):
    """A root-only file without sudo, and a missing file, are not "original": keep backing them up."""
    db = fake_pacman_db(tmp_path, {"etc/sudoers": b"stock\n", "etc/gone.conf": b"x\n"})
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/sudoers").write_bytes(b"stock\n")
    monkeypatch.setattr(etc, "PACMAN_DB", db)
    monkeypatch.setattr(platforms, "have", lambda tool: tool == "pacman")
    monkeypatch.setattr(etc, "_md5", lambda path: "" if path.name == "sudoers" else None)   # unreadable / missing
    assert etc.package_original(["/etc/sudoers", "/etc/gone.conf"], root=root) == set()
    assert etc.package_original([], root=root) == set()


def test_collect_targets_leaves_out_package_originals(monkeypatch):
    monkeypatch.setattr(etc, "_owned_by_packages", lambda: set())
    monkeypatch.setattr(etc, "_modified_package_files", lambda: [])
    lists = {"extra": ["/etc/zshrc", "/etc/hostname"], "secret": ["/etc/sudoers"], "exclude": []}
    monkeypatch.setattr(etc, "_list", lambda name: lists[name])
    monkeypatch.setattr(etc, "_files_under", lambda p: [p])
    monkeypatch.setattr(etc, "package_original", lambda paths: {"/etc/zshrc", "/etc/sudoers"} & set(paths))
    assert etc.collect_targets() == {"/etc/hostname": "plain"}


# ---------------------------------------------------------------- dpkg leftovers worth a look

def test_find_leftovers_skips_diverted_and_regenerated_files(tmp_path):
    root = tmp_path / "etc"
    for rel in ["plymouth/plymouthd.conf.dpkg-new", "ca-certificates.conf.dpkg-old", "ssh/sshd_config.dpkg-dist",
                "a.conf", "ssh/moduli.ucf-new"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n", encoding="utf-8")
    skip = {str(root / "plymouth/plymouthd.conf"), str(root / "ca-certificates.conf")}
    found, hidden = etc.find_leftovers(root, etc.DPKG_LEFTOVERS, skip)
    assert found == [str(root / "ssh/moduli.ucf-new"), str(root / "ssh/sshd_config.dpkg-dist")]
    assert hidden == 2


# ---------------------------------------------------------------- stale entries in system/

def test_backup_drops_entries_that_became_package_originals(system_repo, monkeypatch):
    source, etc_dir = system_repo
    names = {"was-edited.conf": "plain", "now-stock.conf": "plain", "excluded.conf": "plain"}
    for n in names:
        (etc_dir / n).write_text(f"{n}\n", encoding="utf-8")
        (etc_dir / n).chmod(0o644)
    everything = {str(etc_dir / n): k for n, k in names.items()}
    monkeypatch.setattr(etc, "collect_targets", lambda: everything)
    monkeypatch.setattr(etc, "package_original", lambda paths: set())
    etc.backup(Runner(dry=False))
    assert len([p for p in source.rglob("*") if p.is_file()]) == 3
    # later: now-stock.conf equals the package's version, excluded.conf was excluded by the user
    monkeypatch.setattr(etc, "collect_targets", lambda: {str(etc_dir / "was-edited.conf"): "plain"})
    monkeypatch.setattr(etc, "package_original", lambda paths: {str(etc_dir / "now-stock.conf")} & set(paths))
    etc.backup(Runner(dry=True))                                       # a preview drops nothing
    assert len([p for p in source.rglob("*") if p.is_file()]) == 3
    etc.backup(Runner(dry=False))
    left = sorted(p.name for p in source.rglob("*") if p.is_file())
    assert left == ["excluded.conf", "was-edited.conf"]               # only the verified original went

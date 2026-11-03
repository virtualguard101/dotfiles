"""/etc backup on dpkg systems (Debian, Kali): which files packages own, and which conffiles the
user changed. Checked against fake dpkg databases (<admin>/status, info/*.list, diversions)."""
import hashlib
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

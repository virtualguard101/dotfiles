"""Windows registry backup (system/windows/registry): the key -> file mapping, the .reg encoding,
and backup / restore against a fake `reg` and a real second chezmoi instance (runs on Linux)."""
from pathlib import Path

import pytest

from dotfiles.cli import winsys
from dotfiles.cli.backup import Runner
from dotfiles.utils import platforms

KEY = "HKCU\\Software\\Test App"
LONG = "HKEY_CURRENT_USER\\Software\\Test App"
FAKE_TOKEN = "ghp_" + "a1B2" * 9      # built at runtime: the full token never appears in the source
TEXT = f'Windows Registry Editor Version 5.00\n\n[{LONG}]\n"Theme"="dark"\n"Size"=dword:0000000a\n'


class FakeReg:
    """`reg export KEY FILE /y` and `reg import FILE` over a dict of .reg texts (UTF-16 + CRLF on disk, as reg writes)."""

    def __init__(self, keys: dict[str, str]):
        self.keys = {self.norm(k): t for k, t in keys.items()}
        self.imported: list[str] = []

    @staticmethod
    def norm(key: str) -> str:
        return winsys.long_key(key).lower()

    def __call__(self, *args: str) -> platforms.Result:
        if args[0] == "export":
            text = self.keys.get(self.norm(args[1]))
            if text is None:
                return platforms.Result(1, "", "ERROR: The system was unable to find the specified registry key")
            Path(args[2]).write_bytes(winsys.encode_reg(text))
            return platforms.Result(0, "", "")
        if args[0] == "import":
            text = winsys.decode_reg(Path(args[1]).read_bytes())
            key = winsys.header_key(text)
            self.keys[self.norm(key)] = text
            self.imported.append(key)
            return platforms.Result(0, "", "")
        raise AssertionError(args)


@pytest.fixture
def registry(system_repo, monkeypatch):
    source, _ = system_repo
    fake = FakeReg({KEY: TEXT})
    monkeypatch.setattr(winsys, "reg", fake)
    monkeypatch.setattr(winsys, "registry_keys", lambda: [KEY])
    return source, fake


def snapshot(source: Path) -> dict[Path, bytes]:
    return {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_file_names_and_key_validation():
    assert winsys.file_name("HKCU\\Software\\Foo Bar") == "HKCU_Software_Foo_Bar.reg"
    assert winsys.key_files([KEY, "hklm\\SYSTEM\\Foo"]) == {"HKCU_Software_Test_App.reg": KEY, "hklm_SYSTEM_Foo.reg": "hklm\\SYSTEM\\Foo"}
    for bad in ["HKCU", "HKCU\\", "Software\\Foo", "HKLM/Software/Foo", ""]:
        with pytest.raises(Exception, match="not a registry key"):
            winsys.key_files([bad])
    with pytest.raises(Exception, match="same file"):
        winsys.key_files(["HKCU\\Software\\A B", "HKCU\\Software\\A_B"])


def test_reg_files_round_trip_between_utf16_crlf_and_utf8_lf():
    data = winsys.encode_reg(TEXT)
    assert data[:2] == b"\xff\xfe" and b"\r\x00\n\x00" in data
    assert winsys.decode_reg(data) == TEXT
    assert winsys.decode_reg(TEXT.encode("utf-8-sig")) == TEXT         # already text
    assert winsys.header_key(TEXT) == LONG


def test_nothing_configured_does_nothing(monkeypatch, capsys):
    monkeypatch.setattr(winsys, "registry_keys", lambda: [])
    monkeypatch.setattr(winsys, "reg", lambda *a: pytest.fail("reg must not run"))
    winsys.backup(Runner(dry=False))
    assert "none configured" in capsys.readouterr().out


def test_backup_stores_text_and_leaves_it_alone_the_second_time(registry):
    source, fake = registry
    winsys.backup(Runner(dry=False))
    files = snapshot(source)
    assert list(files) == [Path("registry/HKCU_Software_Test_App.reg")]
    assert files[Path("registry/HKCU_Software_Test_App.reg")].decode("utf-8") == TEXT
    winsys.backup(Runner(dry=False))
    assert snapshot(source) == files
    fake.keys[fake.norm(KEY)] = TEXT.replace("dark", "light")
    winsys.backup(Runner(dry=False))
    assert b"light" in snapshot(source)[Path("registry/HKCU_Software_Test_App.reg")]


def test_backup_encrypts_a_key_that_holds_a_secret_and_does_not_churn(registry):
    source, fake = registry
    fake.keys[fake.norm(KEY)] = TEXT + f'"Token"="{FAKE_TOKEN}"\n'
    winsys.backup(Runner(dry=False))
    files = snapshot(source)
    assert list(files) == [Path("registry/encrypted_HKCU_Software_Test_App.reg.age")]
    assert FAKE_TOKEN.encode() not in files[next(iter(files))]
    winsys.backup(Runner(dry=False))
    assert snapshot(source) == files                                   # not re-encrypted


def test_backup_warns_about_a_missing_key_and_about_a_key_no_longer_listed(registry, monkeypatch, capsys):
    source, fake = registry
    winsys.backup(Runner(dry=False))
    monkeypatch.setattr(winsys, "registry_keys", lambda: ["HKCU\\Software\\Nope"])
    winsys.backup(Runner(dry=False))
    out = capsys.readouterr()
    assert "cannot export" in out.out + out.err
    assert "no longer listed" in out.out and "HKCU_Software_Test_App.reg" in out.out
    assert "HKCU_Software_Test_App.reg" in {p.name for p in source.rglob("*.reg")}      # never deleted


def test_restore_previews_then_imports(registry, capsys):
    source, fake = registry
    winsys.backup(Runner(dry=False))
    fake.keys[fake.norm(KEY)] = TEXT.replace('"dark"', '"light"')       # the live key drifted
    capsys.readouterr()
    assert winsys.cmd_restore(apply=False, filters=[]) == 0
    out = capsys.readouterr().out
    assert '-"Theme"="light"' in out and '+"Theme"="dark"' in out and "preview" in out
    assert fake.imported == []
    assert winsys.cmd_restore(apply=True, filters=[]) == 0
    assert fake.imported == [LONG]
    assert fake.keys[fake.norm(KEY)] == TEXT
    capsys.readouterr()
    winsys.cmd_restore(apply=True, filters=[])
    assert "matches the backup" in capsys.readouterr().out
    assert fake.imported == [LONG]                                      # nothing left to import


def test_restore_filters_by_key_or_file_name(registry):
    source, fake = registry
    winsys.backup(Runner(dry=False))
    fake.keys[fake.norm(KEY)] = TEXT.replace('"dark"', '"light"')
    winsys.cmd_restore(apply=True, filters=["HKCU\\Software\\Something Else"])
    assert fake.imported == []
    winsys.cmd_restore(apply=True, filters=["hkcu_software_test_app.reg"])
    assert fake.imported == [LONG]
    fake.keys[fake.norm(KEY)] = TEXT.replace('"dark"', '"light"')
    winsys.cmd_restore(apply=True, filters=[KEY])                       # the short form used in dotfiles.toml
    assert fake.imported == [LONG, LONG]


def test_restore_never_shows_the_content_of_an_encrypted_key(registry, capsys):
    source, fake = registry
    secret = TEXT + f'"Token"="{FAKE_TOKEN}"\n'
    fake.keys[fake.norm(KEY)] = secret
    winsys.backup(Runner(dry=False))
    fake.keys[fake.norm(KEY)] = TEXT
    capsys.readouterr()
    winsys.cmd_restore(apply=False, filters=[])
    out = capsys.readouterr().out
    assert "encrypted file" in out and FAKE_TOKEN not in out


HKLM_KEY = r"HKLM\SOFTWARE\Test Policy"
HKLM_LONG = r"HKEY_LOCAL_MACHINE\SOFTWARE\Test Policy"
HKLM_TEXT = f'Windows Registry Editor Version 5.00\n\n[{HKLM_LONG}]\n"Level"=dword:00000001\n'


def test_restore_needs_a_backup(registry):
    with pytest.raises(Exception, match="no registry backup"):
        winsys.cmd_restore(apply=False, filters=[])


def test_restore_asks_for_admin_only_when_an_hklm_key_is_imported(registry, monkeypatch):
    source, fake = registry
    fake.keys[fake.norm(HKLM_KEY)] = HKLM_TEXT
    monkeypatch.setattr(winsys, "registry_keys", lambda: [KEY, HKLM_KEY])
    winsys.backup(Runner(dry=False))
    monkeypatch.setattr(platforms, "is_admin", lambda: False)

    # only the HKCU key drifted: a normal shell restores it although the list also holds an HKLM key
    fake.keys[fake.norm(KEY)] = TEXT.replace('"dark"', '"light"')
    assert winsys.cmd_restore(apply=True, filters=[]) == 0
    assert fake.imported == [LONG]

    # the HKLM key drifted too: refused up front, nothing imported (not even the HKCU key)
    fake.keys[fake.norm(KEY)] = TEXT.replace('"dark"', '"light"')
    fake.keys[fake.norm(HKLM_KEY)] = HKLM_TEXT.replace("00000001", "00000000")
    with pytest.raises(Exception, match="elevated") as e:
        winsys.cmd_restore(apply=True, filters=[])
    assert HKLM_LONG in str(e.value)
    assert fake.imported == [LONG]

    # naming the HKCU key leaves the HKLM one out; an elevated shell imports the rest
    winsys.cmd_restore(apply=True, filters=[KEY])
    assert fake.imported == [LONG, LONG]
    monkeypatch.setattr(platforms, "is_admin", lambda: True)
    winsys.cmd_restore(apply=True, filters=[])
    assert fake.imported == [LONG, LONG, HKLM_LONG]


def test_long_key():
    assert winsys.long_key(r"HKCU\Software\Foo") == r"HKEY_CURRENT_USER\Software\Foo"
    assert winsys.long_key(r"hklm\SYSTEM\Foo") == r"HKEY_LOCAL_MACHINE\SYSTEM\Foo"
    assert winsys.long_key(r"HKEY_USERS\x") == r"HKEY_USERS\x"
    assert winsys.long_key("HKCU_Software_Foo.reg") == "HKCU_Software_Foo.reg"

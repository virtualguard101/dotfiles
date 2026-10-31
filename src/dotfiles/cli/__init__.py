"""Command-line entry point: `uv run dotfiles <command>`. Subcommands import lazily so a failure
in one module does not affect the others."""
from __future__ import annotations

import argparse
import sys

from dotfiles import __version__
from dotfiles.utils import platforms, ui

EPILOG = """\
common flows:
  after a reinstall   bootstrap.sh / bootstrap.ps1  ->  dotfiles restore --apply
  daily backup        dotfiles backup -n  ->  dotfiles backup --commit
  deploy repo edits   dotfiles apply  ->  dotfiles apply --apply
  cache cleanup       dotfiles clean scan  ->  dotfiles clean run --apply
Every modifying command previews by default; see README.md for details."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dotfiles", description="cross-platform dotfiles maintenance built on chezmoi",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"dotfiles {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    s = sub.add_parser("doctor", help="check the system, toolchain, encryption config and repo state (read-only)")
    s.add_argument("--json", action="store_true", help="print JSON")

    s = sub.add_parser("restore", help="post-reinstall recovery: key -> config -> dotfiles -> packages -> ssh -> hooks (plan by default)")
    s.add_argument("--apply", action="store_true", help="actually run")
    s.add_argument("--only", action="append", default=[], metavar="STEP", help="run only these steps (repeatable, comma-separated)")
    s.add_argument("--skip", action="append", default=[], metavar="STEP", help="skip these steps")
    s.add_argument("--key-file", metavar="PATH", help="age private key file (e.g. on a USB drive); prompts interactively when omitted")
    s.add_argument("-y", "--yes", action="store_true", help="auto-confirm steps that ask")

    s = sub.add_parser("apply", help="deploy the repo into ~ (reverse of backup): preview diffs, --apply deploys with a backup")
    s.add_argument("targets", nargs="*", metavar="TARGET", help="only these paths under ~ (default: everything)")
    s.add_argument("--apply", action="store_true", help="actually deploy")

    sub.add_parser("status", help="backup state: drift, unmanaged candidates, package manifests, permissions (read-only)")

    s = sub.add_parser("backup", help="re-add managed files -> add candidates -> system config -> package manifests -> check")
    s.add_argument("-n", "--dry-run", action="store_true", help="show what would change")
    s.add_argument("--no-system", action="store_true", help="skip /etc (Linux); no sudo needed")
    s.add_argument("--commit", action="store_true", help="commit after the check passes (no push)")

    sub.add_parser("check", help="security check: plaintext secrets / .age format / must-encrypt targets / platform ownership")

    s = sub.add_parser("audit", help="sensitive-file permissions (Linux 600/700, Windows ACL)")
    s.add_argument("--apply", action="store_true", help="tighten permissions")

    s = sub.add_parser("system-restore", help="Linux: compare system/ backups with the current /etc; --apply installs")
    s.add_argument("--apply", action="store_true", help="actually install (needs sudo)")
    s.add_argument("paths", nargs="*", metavar="/etc/PATH", help="only these paths; hardware-bound files must be named explicitly")

    s = sub.add_parser("clean", help="cache cleanup (preview by default)")
    csub = s.add_subparsers(dest="clean_cmd", metavar="<scan|list|run|leftovers>")
    c = csub.add_parser("scan", help="read-only: size and state per target, uncovered dirs, leftovers")
    c.add_argument("--deep", action="store_true")
    csub.add_parser("list", help="list all cleanup targets")
    c = csub.add_parser("run", help="run cleanup; equals the default tier when no target is given")
    c.add_argument("targets", nargs="*", metavar="TARGET", help="target id, or default / opt / all")
    c.add_argument("--apply", action="store_true", help="actually run")
    c.add_argument("--deep", action="store_true", help="more thorough cleanup mode")
    c.add_argument("-x", "--exclude", action="append", default=[], metavar="TARGET", help="exclude a target (repeatable, comma-separated)")
    csub.add_parser("leftovers", help="read-only: leftover dirs of uninstalled apps")

    s = sub.add_parser("config", help="backup and cleanup lists in dotfiles.toml: list / get / add / remove / set / path")
    s.add_argument("action", choices=["list", "get", "add", "remove", "set", "path"])
    s.add_argument("key", nargs="?", metavar="KEY", help="dotted key, e.g. backup.linux.ignore (list: optional prefix)")
    s.add_argument("values", nargs="*", metavar="VALUE")

    s = sub.add_parser("guard", help="commit checks (invoked by pre-commit); arguments are passed to guard", add_help=False)
    s.add_argument("args", nargs=argparse.REMAINDER)
    return p


def _split(values: list[str]) -> list[str]:
    return [x for v in values for x in v.split(",") if x]


def dispatch(a: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if a.cmd == "doctor":
        from dotfiles.cli import doctor
        return doctor.run(as_json=a.json)
    if a.cmd == "restore":
        from dotfiles.cli import restore
        return restore.run(apply=a.apply, only=_split(a.only), skip=_split(a.skip), key_file=a.key_file,
                           assume_yes=a.yes)
    if a.cmd == "apply":
        from dotfiles.cli import apply
        return apply.cmd_apply(a.targets, apply=a.apply)
    if a.cmd in ("status", "backup", "check"):
        from dotfiles.cli import backup
        if a.cmd == "status":
            return backup.cmd_status()
        if a.cmd == "backup":
            return backup.cmd_backup(dry=a.dry_run, with_system=not a.no_system, commit=a.commit)
        return backup.cmd_check()
    if a.cmd == "audit":
        from dotfiles.cli import audit
        return audit.cmd_audit(apply=a.apply)
    if a.cmd == "system-restore":
        if not platforms.IS_LINUX:
            raise ui.Abort("system-restore is Linux-only", 2)
        from dotfiles.cli import etc
        return etc.cmd_restore(apply=a.apply, filters=a.paths)
    if a.cmd == "clean":
        from dotfiles.cli import clean
        if a.clean_cmd == "scan":
            return clean.cmd_scan(deep=a.deep)
        if a.clean_cmd == "list":
            return clean.cmd_list()
        if a.clean_cmd == "run":
            return clean.cmd_run(a.targets, a.exclude, apply=a.apply, deep=a.deep)
        if a.clean_cmd == "leftovers":
            return clean.cmd_leftovers()
        parser.parse_args(["clean", "--help"])
        return 2
    if a.cmd == "config":
        from dotfiles.cli import config
        return config.cmd(a.action, a.key, a.values)
    if a.cmd == "guard":
        from dotfiles.utils.common import guard
        return guard.main(a.args)
    parser.print_help()
    return 0 if a.cmd is None else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        return dispatch(a, parser)
    except ui.Abort as e:
        ui.err(str(e))
        return e.code
    except KeyboardInterrupt:
        ui.err("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())

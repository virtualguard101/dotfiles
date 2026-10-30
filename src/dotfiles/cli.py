"""命令行入口：uv run dotfiles <子命令>。各子命令按需导入，单个模块出错不影响其他命令。"""
from __future__ import annotations

import argparse
import sys

from dotfiles import __version__, sysinfo, ui

EPILOG = """\
常用流程:
  重装后恢复   bootstrap.sh / bootstrap.ps1  →  dotfiles restore --apply
  日常备份     dotfiles backup -n  →  dotfiles backup --commit
  清理缓存     dotfiles clean scan  →  dotfiles clean run --apply
所有修改性操作默认只预览；详见 README.md。"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dotfiles", description="基于 chezmoi 的跨平台 dotfiles 维护工具",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"dotfiles {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<子命令>")

    s = sub.add_parser("doctor", help="检测系统环境、工具链、加密配置与仓库状态（只读）")
    s.add_argument("--json", action="store_true", help="以 JSON 输出")

    s = sub.add_parser("restore", help="重装后恢复：私钥 → 配置 → dotfiles → 软件包 → SSH → hooks（默认只显示计划）")
    s.add_argument("--apply", action="store_true", help="实际执行")
    s.add_argument("--only", action="append", default=[], metavar="步骤", help="只执行指定步骤（可重复或用逗号分隔）")
    s.add_argument("--skip", action="append", default=[], metavar="步骤", help="跳过指定步骤")
    s.add_argument("--key-file", metavar="路径", help="age 私钥文件（例如 U 盘中的文件）；不给时交互询问")
    s.add_argument("-y", "--yes", action="store_true", help="对需要确认的步骤自动确认")

    sub.add_parser("status", help="备份状态：漂移、未托管的候选、软件包清单、权限（只读）")

    s = sub.add_parser("backup", help="re-add 已托管文件 → 添加候选 → 系统配置 → 软件包清单 → check")
    s.add_argument("-n", "--dry-run", action="store_true", help="只显示将要发生的变更")
    s.add_argument("--no-system", action="store_true", help="跳过 /etc（Linux），不需要 sudo")
    s.add_argument("--commit", action="store_true", help="检查通过后提交（不推送）")

    sub.add_parser("check", help="安全检查：明文密钥 / .age 格式 / 必须加密的目标 / 平台归属 / 私钥位置")

    s = sub.add_parser("audit", help="敏感文件权限检查（Linux 600/700，Windows ACL）")
    s.add_argument("--apply", action="store_true", help="收紧权限")

    s = sub.add_parser("system-restore", help="Linux：把 system/ 中的 /etc 备份与当前系统对比，--apply 时安装")
    s.add_argument("--apply", action="store_true", help="实际安装（需要 sudo）")
    s.add_argument("paths", nargs="*", metavar="/etc/路径", help="只处理这些路径；与硬件绑定的文件必须显式指定")

    s = sub.add_parser("clean", help="缓存清理（默认只预览）")
    csub = s.add_subparsers(dest="clean_cmd", metavar="<scan|list|run|leftovers>")
    c = csub.add_parser("scan", help="只读：各目标占用、未覆盖项、残留目录")
    c.add_argument("--deep", action="store_true")
    csub.add_parser("list", help="列出全部清理目标")
    c = csub.add_parser("run", help="执行清理；不指定目标时等同 default")
    c.add_argument("targets", nargs="*", metavar="目标", help="目标 id，或 default / opt / all")
    c.add_argument("--apply", action="store_true", help="实际执行")
    c.add_argument("--deep", action="store_true", help="更彻底的清理方式")
    c.add_argument("-x", "--exclude", action="append", default=[], metavar="目标", help="排除目标（可重复或用逗号分隔）")
    csub.add_parser("leftovers", help="只读：已卸载应用的残留目录")

    s = sub.add_parser("guard", help="提交检查（pre-commit 调用）；参数原样传给 guard", add_help=False)
    s.add_argument("args", nargs=argparse.REMAINDER)
    return p


def _split(values: list[str]) -> list[str]:
    return [x for v in values for x in v.split(",") if x]


def dispatch(a: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if a.cmd == "doctor":
        from dotfiles import doctor
        return doctor.run(as_json=a.json)
    if a.cmd == "restore":
        from dotfiles import restore
        return restore.run(apply=a.apply, only=_split(a.only), skip=_split(a.skip), key_file=a.key_file,
                           assume_yes=a.yes)
    if a.cmd in ("status", "backup", "check", "audit"):
        from dotfiles import backup
        if a.cmd == "status":
            return backup.cmd_status()
        if a.cmd == "backup":
            return backup.cmd_backup(dry=a.dry_run, with_system=not a.no_system, commit=a.commit)
        if a.cmd == "check":
            return backup.cmd_check()
        return backup.cmd_audit(apply=a.apply)
    if a.cmd == "system-restore":
        if not sysinfo.IS_LINUX:
            raise ui.Abort("system-restore 只用于 Linux", 2)
        from dotfiles import system_etc
        return system_etc.cmd_restore(apply=a.apply, filters=a.paths)
    if a.cmd == "clean":
        from dotfiles import clean
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
    if a.cmd == "guard":
        from dotfiles import guard
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
        ui.err("已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())

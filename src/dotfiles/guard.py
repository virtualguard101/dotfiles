#!/usr/bin/env python3
"""chezmoi 源仓库的提交/推送检查（跨平台，仅依赖 Python 3.9+ 标准库与 git）。

子命令：
  staged [路径...]     pre-commit 阶段：检查暂存区（index）中的内容；不给路径时检查全部暂存文件
  push                 pre-push 阶段：逐个检查将被推送的每个提交新增/修改的文件
  tree                 检查工作区内所有已跟踪及未忽略的新文件（dotfiles check 使用）
  scan [-q] 文件...    只做密钥特征扫描，命中返回 1（dotfiles backup 判断是否需要加密）
  prune-stash          删除 pre-commit 遗留的 stash 补丁并收紧其目录权限

检查项：
  age       *.age 必须是 age 密文；chezmoi 的 encrypted_ 前缀与 .age 后缀必须成对出现
  secret    明文文件不得包含密钥特征
  policy    敏感目标（SSH、凭据文件、/etc/sudoers 等）必须以 encrypted_*.age 形式存储
  warn      含绝对家目录路径（不阻止提交）

部署目标按 chezmoi 规则换算：仓库根目录 .chezmoiroot 指定的目录（如 home/）下是 ~ 的目标，
system/ 下是系统路径（/etc/...），其余文件（工具代码、软件包清单）不是部署目标，只做密钥扫描。

本文件既是 dotfiles 包的模块，也会被 pre-commit 直接执行，因此只能依赖标准库。
输出只包含 文件:行号 与规则名，永远不打印命中的内容。
行内含 "gitleaks:allow" 或 "pragma: allowlist secret" 时跳过该行的 secret 检查。
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------- 规则

# 明文密钥特征：(规则名, 正则)。修改后 backup.sh 会自动沿用（它调用本脚本的 scan）。
SECRET_RULES: list[tuple[str, re.Pattern[bytes]]] = [
    (name, re.compile(rx.encode()))
    for name, rx in [
        ("private-key", r"-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY( BLOCK)?-----"),
        ("age-secret-key", r"AGE-SECRET-KEY-1[0-9A-Z]{20,}"),
        ("github-token", r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,}"),
        ("github-pat", r"github_pat_[A-Za-z0-9_]{40,}"),
        ("openai-anthropic-key", r"(?<![A-Za-z0-9_])sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}"),
        ("aws-access-key", r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"),
        ("slack-token", r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}"),
        ("pypi-token", r"pypi-[A-Za-z0-9_-]{50,}"),
        ("npm-token", r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}"),
        ("npmrc-auth", r"_auth(Token)?\s*=\s*[^\s$]{8,}"),
        ("wakatime-key", r"waka_[0-9a-f-]{36}"),
        ("google-api-key", r"AIza[0-9A-Za-z_-]{35}"),
        ("generic-assignment",
         r"(?i)(api[_-]?key|secret|token|pass(word|wd)?)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_.~-]{12,}"),
    ]
]
ALLOW_MARKERS = (b"gitleaks:allow", b"pragma: allowlist secret")

# 必须加密的 chezmoi 目标路径（相对 ~，或以 / 开头表示 system/ 下的系统路径）。
# fnmatch 语义：* 可跨越 /。
ENCRYPT_REQUIRED = [
    ".ssh/*", ".gnupg/*",
    ".pypirc", ".npmrc", ".netrc", "_netrc", ".git-credentials",
    ".docker/config.json", ".config/gh/hosts.yml", "AppData/Roaming/GitHub CLI/hosts.yml",
    ".wakatime.cfg", ".aws/credentials", ".kube/config",
    ".config/rclone/rclone.conf", "AppData/Roaming/rclone/rclone.conf",
    "*.pem", "*.key", "*.p12", "*.pfx", "*id_rsa", "*id_ecdsa", "*id_ed25519",
    ".env", ".env.*", "*/.env", "*/.env.*",
    "/etc/shadow*", "/etc/gshadow*", "/etc/sudoers", "/etc/sudoers.d/*", "/etc/crypttab",
    "/etc/NetworkManager/system-connections/*", "/etc/wireguard/*", "/etc/ssh/ssh_host_*_key",
    "/etc/mihomo/config.yaml",
]
ENCRYPT_EXEMPT = ["*.pub", ".ssh/known_hosts*", ".env.example", "*/.env.example"]

HOME_PATH_RX = re.compile(rb"(/(?:home)/[^/\s\"']+/|/(?:Users)/[^/\s\"']+/|[A-Za-z]:\\Users\\[^\\\s\"']+\\)")

AGE_ARMOR_BEGIN = b"-----BEGIN AGE ENCRYPTED FILE-----"
AGE_ARMOR_END = b"-----END AGE ENCRYPTED FILE-----"
AGE_BINARY = b"age-encryption.org/v1\n"

MAX_BYTES = 5 * 1024 * 1024
FILE_PREFIXES = ("encrypted_", "private_", "readonly_", "empty_", "executable_", "create_",
                 "modify_", "remove_", "symlink_", "run_", "once_", "onchange_", "before_",
                 "after_", "literal_")
DIR_PREFIXES = ("remove_", "external_", "exact_", "private_", "readonly_", "literal_")
LEGACY_NON_TARGET_TOP = ("scripts",)   # 没有 .chezmoiroot 的旧布局中被忽略的工具目录
SYSTEM_TOP = "system"                   # /etc 等系统配置的源目录（独立 chezmoi 实例）

# ---------------------------------------------------------------- 输出

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")   # Windows 控制台编码不支持时不崩溃
    except (AttributeError, ValueError):
        pass


@dataclass
class Finding:
    level: str      # error | warn
    rule: str
    path: str
    line: int = 0
    where: str = ""  # 提交短 SHA（push 模式）

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        prefix = f"{self.where} " if self.where else ""
        return f"{prefix}{loc}: [{self.rule}]"


# ---------------------------------------------------------------- git 辅助

def git(*args: str, input_: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], input=input_, check=True,
                          stdout=subprocess.PIPE).stdout


def read_blobs(shas: list[str]) -> dict[str, bytes]:
    """一次 git cat-file --batch 读出多个 blob，内容只留在内存中。"""
    if not shas:
        return {}
    uniq = list(dict.fromkeys(shas))
    out = git("cat-file", "--batch", input_=("\n".join(uniq) + "\n").encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    for sha in uniq:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        pos = nl + 1
        if len(header) < 3 or header[1] != b"blob":
            continue
        size = int(header[2])
        blobs[sha] = out[pos:pos + size]
        pos += size + 1
    return blobs


def index_entries() -> dict[str, tuple[str, str]]:
    """路径 → (mode, blob sha)，来自暂存区。"""
    entries = {}
    for rec in git("ls-files", "-s", "-z").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, sha, _stage = meta.decode().split()
        entries[path.decode("utf-8", "surrogateescape")] = (mode, sha)
    return entries


# ---------------------------------------------------------------- chezmoi 路径

def _strip_attrs(comp: str, prefixes: tuple[str, ...]) -> tuple[str, bool]:
    """去掉 chezmoi 属性前缀；遇到 literal_ 时停止并保留其后的原样名称。"""
    while True:
        if comp.startswith("literal_"):
            return comp[len("literal_"):], True
        for p in prefixes:
            if comp.startswith(p):
                comp = comp[len(p):]
                break
        else:
            return comp, False


_SOURCE_ROOT: str | None = None


def source_root() -> str:
    """仓库根目录下 .chezmoiroot 的内容（如 "home"）；没有时为空串（旧布局）。"""
    global _SOURCE_ROOT
    if _SOURCE_ROOT is None:
        try:
            top = git("rev-parse", "--show-toplevel").decode().strip()
            _SOURCE_ROOT = Path(top, ".chezmoiroot").read_text(encoding="utf-8").strip().strip("/")
        except (OSError, subprocess.CalledProcessError):
            _SOURCE_ROOT = ""
    return _SOURCE_ROOT


def target_of(source: str, root: str | None = None) -> str | None:
    """chezmoi 源路径（相对仓库根）→ 目标路径（~ 相对，或 system/ 下转为 /...）；非目标文件返回 None。"""
    root = source_root() if root is None else root
    parts = source.split("/")
    system = parts[0] == SYSTEM_TOP
    if system:
        parts = parts[1:]
    elif root:
        root_parts = root.split("/")
        if parts[:len(root_parts)] != root_parts:
            return None
        parts = parts[len(root_parts):]
    elif parts[0] in LEGACY_NON_TARGET_TOP:
        return None
    # chezmoi 忽略源状态中以 . 开头的条目（.chezmoiignore、.chezmoidata 等是它自己的配置）
    if not parts or any(p.startswith(".") for p in parts):
        return None
    out = []
    for i, comp in enumerate(parts):
        last = i == len(parts) - 1
        comp, literal = _strip_attrs(comp, FILE_PREFIXES if last else DIR_PREFIXES)
        if not literal and comp.startswith("dot_"):
            comp = "." + comp[4:]
        if last:
            for suffix in (".tmpl", ".literal", ".age"):
                if comp.endswith(suffix):
                    comp = comp[: -len(suffix)]
        out.append(comp)
    target = "/".join(out)
    return "/" + target if system else target


def is_encrypted_source(source: str) -> bool:
    return Path(source).name.startswith("encrypted_") and source.endswith(".age")


def requires_encryption(target: str) -> bool:
    if any(fnmatch.fnmatchcase(target, p) for p in ENCRYPT_EXEMPT):
        return False
    return any(fnmatch.fnmatchcase(target, p) for p in ENCRYPT_REQUIRED)


# ---------------------------------------------------------------- 检查

def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def secret_lines(data: bytes) -> Iterator[tuple[int, str]]:
    for no, line in enumerate(data.splitlines(), 1):
        if any(m in line for m in ALLOW_MARKERS):
            continue
        for name, rx in SECRET_RULES:
            if rx.search(line):
                yield no, name
                break


def check_content(path: str, data: bytes, mode: str = "100644") -> list[Finding]:
    f: list[Finding] = []
    if mode in ("120000", "160000"):   # 符号链接 / 子模块
        return f
    name = Path(path).name
    is_age = path.endswith(".age")

    if is_age:
        if not (data.startswith(AGE_ARMOR_BEGIN) or data.startswith(AGE_BINARY)):
            f.append(Finding("error", "age-not-ciphertext", path))
        elif data.startswith(AGE_ARMOR_BEGIN) and AGE_ARMOR_END not in data[-200:]:
            f.append(Finding("error", "age-armor-truncated", path))
        if not name.startswith("encrypted_") and target_of(path) is not None:
            f.append(Finding("error", "age-without-encrypted-prefix", path))
        return f
    if name.startswith("encrypted_") and target_of(path) is not None:
        f.append(Finding("error", "encrypted-prefix-without-age", path))

    target = target_of(path)
    if target is not None and requires_encryption(target):
        f.append(Finding("error", f"must-encrypt({target})", path))

    if len(data) > MAX_BYTES or is_binary(data):
        return f
    for no, rule in secret_lines(data):
        f.append(Finding("error", rule, path, no))
    m = HOME_PATH_RX.search(data)
    if m:
        no = data.count(b"\n", 0, m.start()) + 1
        f.append(Finding("warn", "absolute-home-path", path, no))
    return f


def report(findings: list[Finding], hint: bool = True) -> int:
    errors = [x for x in findings if x.level == "error"]
    warns = [x for x in findings if x.level == "warn"]
    for x in warns:
        print(f"warning: {x}")
    for x in errors:
        print(f"error:   {x}")
    if errors and hint:
        print(
            "\n检测到未加密的敏感内容（输出中不含具体值）。处理方式：\n"
            "  - 敏感文件改用加密托管: chezmoi forget <目标>，再 chezmoi add --encrypt <目标>\n"
            "  - 若为误报，在该行加注释 gitleaks:allow，或调整 src/dotfiles/guard.py 的规则\n"
            "  - 已提交但未推送: git reset --soft HEAD~1 后重新处理；已推送则必须轮换该凭据"
        )
    return 1 if errors else 0


# ---------------------------------------------------------------- 子命令

def cmd_staged(paths: list[str]) -> int:
    entries = index_entries()
    wanted = [p.replace("\\", "/") for p in paths] if paths else list(entries)
    wanted = [p for p in wanted if p in entries]
    blobs = read_blobs([entries[p][1] for p in wanted])
    findings: list[Finding] = []
    for p in wanted:
        mode, sha = entries[p]
        findings += check_content(p, blobs.get(sha, b""), mode)
    return report(findings)


ZERO = "0" * 40


def push_commits() -> list[str]:
    to_ref = os.environ.get("PRE_COMMIT_TO_REF") or "HEAD"
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    if set(to_ref) == {"0"}:   # 删除远程分支
        return []
    if from_ref and set(from_ref) != {"0"}:
        rng = [f"{from_ref}..{to_ref}"]
    else:   # 新分支或首次推送：所有不在任何远程上的提交
        rng = [to_ref, "--not", "--remotes"]
    return git("rev-list", "--reverse", *rng).decode().split()


def cmd_push() -> int:
    findings: list[Finding] = []
    for commit in push_commits():
        raw = git("diff-tree", "-r", "-z", "--root", "--no-commit-id", "--no-renames",
                  "--diff-filter=AMT", commit)
        fields = raw.split(b"\0")
        changes: list[tuple[str, str, str]] = []
        for i in range(0, len(fields) - 1, 2):
            meta = fields[i].decode().lstrip(":").split()
            if len(meta) < 5:
                continue
            changes.append((fields[i + 1].decode("utf-8", "surrogateescape"), meta[1], meta[3]))
        blobs = read_blobs([sha for _, _, sha in changes])
        for path, mode, sha in changes:
            for x in check_content(path, blobs.get(sha, b""), mode):
                x.where = commit[:8]
                findings.append(x)
    return report([x for x in findings if x.level == "error"])


def cmd_tree() -> int:
    raw = git("ls-files", "-z", "-co", "--exclude-standard")
    findings: list[Finding] = []
    for p in raw.decode("utf-8", "surrogateescape").split("\0"):
        if not p or not os.path.isfile(p) or os.path.islink(p):
            continue
        with open(p, "rb") as fh:
            findings += check_content(p, fh.read())
    return report(findings, hint=False)


def cmd_scan(files: list[str], quiet: bool) -> int:
    hit = False
    for p in files:
        try:
            with open(p, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_BYTES or is_binary(data):
            continue
        for no, rule in secret_lines(data):
            hit = True
            if quiet:
                return 1
            print(f"{p}:{no}: [{rule}]")
    return 1 if hit else 0


def precommit_home() -> Path:
    env = os.environ.get("PRE_COMMIT_HOME")
    if env:
        return Path(env)
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(cache) / "pre-commit"


def cmd_prune_stash(max_age_minutes: int) -> int:
    """pre-commit 会把未暂存的改动存为 patch* 且从不删除；这些补丁可能含明文。"""
    home = precommit_home()
    if not home.is_dir():
        return 0
    if os.name == "posix":
        try:
            os.chmod(home, 0o700)
        except OSError:
            pass
    cutoff = time.time() - max_age_minutes * 60
    for p in home.glob("patch*"):
        try:
            # 只删旧补丁：正在运行的 pre-commit（包括本仓库这次）的补丁还要用来恢复
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("staged"); s.add_argument("paths", nargs="*")
    sub.add_parser("push")
    sub.add_parser("tree")
    s = sub.add_parser("scan"); s.add_argument("-q", "--quiet", action="store_true"); s.add_argument("files", nargs="+")
    s = sub.add_parser("prune-stash"); s.add_argument("--max-age-minutes", type=int, default=60)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "staged":
            return cmd_staged(a.paths)
        if a.cmd == "push":
            return cmd_push()
        if a.cmd == "tree":
            return cmd_tree()
        if a.cmd == "scan":
            return cmd_scan(a.files, a.quiet)
        return cmd_prune_stash(a.max_age_minutes)
    except subprocess.CalledProcessError as e:
        print(f"error: git 命令失败: {' '.join(map(str, e.cmd))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

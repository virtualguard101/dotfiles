"""两端共用的清理目标：语言工具链的包缓存。只有默认路径按平台不同。"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from dotfiles import sysinfo
from dotfiles.clean import Ctx, Skip, Target, clean_from_paths, existing
from dotfiles.sysinfo import IS_WINDOWS, have, home, localappdata, query, xdg_cache, xdg_data

PATCH_MINUTES = 60   # pre-commit：保留多少分钟内的 stash 补丁（正在运行的提交还要用它恢复工作区）


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def _queried(cmd: list[str], default: Path) -> Path:
    value = query(cmd)
    return Path(value) if value else default


def _skip_or_paths(running: bool, reason: str, paths: list[Path]) -> list:
    return [Skip(reason)] if running else paths


# ---------------------------------------------------------------- uv / pip

def uv() -> Target:
    default = localappdata() / "uv" / "cache" if IS_WINDOWS else xdg_cache() / "uv"
    return Target(
        "uv", "default", "uv cache prune（--deep: uv cache clean）",
        avail=lambda c: have("uv"),
        paths=lambda c: [_queried(["uv", "cache", "dir"], default)],
        clean=lambda c: c.cmd("uv", "cache", "clean" if c.deep else "prune"),
    )


def pip() -> Target:
    default = localappdata() / "pip" / "Cache" if IS_WINDOWS else xdg_cache() / "pip"
    return Target(
        "pip", "default", "pip cache purge",
        avail=lambda c: have("pip"),
        paths=lambda c: [_queried(["pip", "cache", "dir"], default)],
        clean=lambda c: c.cmd("pip", "cache", "purge"),
    )


# ---------------------------------------------------------------- JavaScript

def pnpm() -> Target:
    meta = localappdata() / "pnpm-cache" if IS_WINDOWS else xdg_cache() / "pnpm"

    def paths(c: Ctx):
        store = query(["pnpm", "store", "path"])
        return [p for p in (Path(store) if store else None, meta) if p]

    def clean(c: Ctx):
        c.cmd("pnpm", "store", "prune")
        c.remove([meta])

    return Target("pnpm", "default", "pnpm store prune + 元数据缓存", avail=lambda c: have("pnpm"),
                  paths=paths, clean=clean)


def npm() -> Target:
    default = localappdata() / "npm-cache" if IS_WINDOWS else home() / ".npm"

    def root() -> Path:
        return _queried(["npm", "config", "get", "cache"], default)

    def paths(c: Ctx):
        r = root()
        return [r / "_cacache", r / "_logs"] + ([r / "_npx"] if c.deep else [])

    def clean(c: Ctx):
        r = root()
        c.cmd("npm", "cache", "clean", "--force")
        c.remove([r / "_logs"] + ([r / "_npx"] if c.deep else []))

    return Target("npm", "default", "npm cache clean --force + _logs（--deep 额外删除 _npx）",
                  avail=lambda c: have("npm"), paths=paths, clean=clean)


def bun() -> Target:
    def cache_dir() -> Path:
        if os.environ.get("BUN_INSTALL_CACHE_DIR"):
            return Path(os.environ["BUN_INSTALL_CACHE_DIR"])
        return _env_path("BUN_INSTALL", home() / ".bun") / "install" / "cache"

    return Target("bun", "default", "bun pm cache rm", avail=lambda c: have("bun"),
                  paths=lambda c: [cache_dir()], clean=lambda c: c.cmd("bun", "pm", "cache", "rm"))


def yarn() -> Target:
    """classic 与 berry 的全局缓存都直接删目录：berry 在项目外会拒绝运行 yarn cache clean。"""
    dirs = [home() / ".yarn" / "berry" / "cache",
            localappdata() / "Yarn" / "Cache" if IS_WINDOWS else xdg_cache() / "yarn"]
    return Target("yarn", "default", "yarn 全局缓存目录（classic 与 berry）",
                  avail=lambda c: bool(existing(dirs)), paths=lambda c: dirs,
                  clean=lambda c: c.remove(dirs))


# ---------------------------------------------------------------- 编译型语言

def go() -> Target:
    """Go 已卸载但构建缓存还在时直接删目录。"""
    default = localappdata() / "go-build" if IS_WINDOWS else xdg_cache() / "go-build"

    def paths(c: Ctx):
        if not have("go"):
            return [default]
        out = [_queried(["go", "env", "GOCACHE"], default)]
        if c.deep and (mod := query(["go", "env", "GOMODCACHE"])):
            out.append(Path(mod))
        return out

    def clean(c: Ctx):
        if not have("go"):
            c.remove([default])
            return
        c.cmd("go", "clean", "-cache")
        if c.deep:
            c.cmd("go", "clean", "-modcache")

    return Target("go", "default", "go clean -cache（--deep 额外 -modcache；Go 已卸载时删除缓存目录）",
                  avail=lambda c: have("go") or default.exists(), paths=paths, clean=clean)


def cargo() -> Target:
    root = _env_path("CARGO_HOME", home() / ".cargo")

    def paths(c: Ctx):
        if sysinfo.proc_running(["cargo"]):
            return [Skip("cargo 正在运行")]
        out = [root / "registry" / "src", root / "git" / "checkouts"]
        if c.deep:
            out += [root / "registry" / "cache", root / "git" / "db"]
        return out

    return Target("cargo", "default", "$CARGO_HOME/registry/src 与 git/checkouts（--deep 额外 registry/cache、git/db）",
                  avail=lambda c: (root / "registry").exists() or (root / "git").exists(), paths=paths)


def nuget() -> Target:
    default = localappdata() / "NuGet" / "v3-cache" if IS_WINDOWS else xdg_data() / "NuGet" / "http-cache"
    path = _env_path("NUGET_HTTP_CACHE_PATH", default)

    def clean(c: Ctx):
        if have("dotnet"):
            c.cmd("dotnet", "nuget", "locals", "http-cache", "--clear")
        else:
            c.remove([path])

    return Target("nuget", "default", "dotnet nuget locals http-cache --clear",
                  avail=lambda c: path.exists(), paths=lambda c: [path], clean=clean)


def vcpkg() -> Target:
    """classic 模式：installed/ 不受影响。"""
    def root() -> Path | None:
        for r in (os.environ.get("VCPKG_ROOT"), str(home() / "vcpkg")):
            if r and (Path(r) / ".vcpkg-root").is_file():
                return Path(r)
        return None

    def paths(c: Ctx):
        r = root()
        if r is None:
            return []
        if sysinfo.proc_running(["vcpkg"]):
            return [Skip("vcpkg 正在运行")]
        return [r / "buildtrees", r / "packages"] + ([r / "downloads"] if c.deep else [])

    return Target("vcpkg", "default", "$VCPKG_ROOT/buildtrees 与 packages（--deep 额外 downloads）",
                  avail=lambda c: root() is not None, paths=paths)


# ---------------------------------------------------------------- pre-commit

def precommit_home() -> Path:
    return _env_path("PRE_COMMIT_HOME", xdg_cache() / "pre-commit")


def precommit() -> Target:
    """stash 补丁 patch* 可能含未暂存的明文，pre-commit 从不删除它们。"""
    def old_patches() -> list[Path]:
        cutoff = time.time() - PATCH_MINUTES * 60
        try:
            return [p for p in precommit_home().glob("patch*") if p.is_file() and p.stat().st_mtime < cutoff]
        except OSError:
            return []

    def clean(c: Ctx):
        if c.deep:
            c.cmd("pre-commit", "clean")
            return
        c.cmd("pre-commit", "gc")
        c.remove(old_patches())

    return Target("precommit", "default",
                  f"pre-commit gc + 删除 {PATCH_MINUTES} 分钟前遗留的 stash 补丁（--deep: pre-commit clean）",
                  avail=lambda c: have("pre-commit") and precommit_home().exists(),
                  paths=lambda c: [precommit_home()], clean=clean)


# ---------------------------------------------------------------- JVM（opt）

def gradle() -> Target:
    root = _env_path("GRADLE_USER_HOME", home() / ".gradle")
    dirs = [root / "caches", root / "daemon"]

    def clean(c: Ctx):
        if have("gradle"):
            c.cmd("gradle", "--stop", "-q")
        c.remove(dirs)

    return Target("gradle", "opt", "$GRADLE_USER_HOME/caches 与 daemon（先 gradle --stop；下次构建重新下载依赖）",
                  avail=lambda c: dirs[0].exists(), paths=lambda c: dirs, clean=clean)


def _maven_running() -> bool:
    if IS_WINDOWS:
        r = sysinfo.powershell("Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | ForEach-Object CommandLine")
        return "plexus.classworlds" in r.stdout
    return any("plexus.classworlds" in " ".join(p.argv) for p in sysinfo.processes())


def maven() -> Target:
    repo = home() / ".m2" / "repository"
    return Target("maven", "opt", "~/.m2/repository（下次构建重新下载全部依赖）",
                  avail=lambda c: repo.exists(),
                  paths=lambda c: _skip_or_paths(_maven_running(), "Maven 正在运行", [repo]))


# ---------------------------------------------------------------- Docker（opt）

_SIZE_RX = re.compile(r"^(Build Cache|Images)\|([\d.]+)\s*([kKMGT]?B)")
_MULT = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def _docker_up() -> bool:
    if not have("docker"):
        return False
    if IS_WINDOWS:
        return Path(r"\\.\pipe\docker_engine").exists()
    return sysinfo.run(["docker", "info"], timeout=5).ok


def _docker_reclaimable(c: Ctx) -> int:
    r = sysinfo.run(["docker", "system", "df", "--format", "{{.Type}}|{{.Reclaimable}}"], timeout=30)
    total = 0
    for line in r.stdout.splitlines():
        if m := _SIZE_RX.match(line.strip()):
            total += int(float(m.group(2)) * _MULT.get(m.group(3), 1))
    return total


def docker() -> Target:
    def clean(c: Ctx):
        c.cmd("docker", "builder", "prune", "-f")
        c.cmd("docker", "image", "prune", "-f")

    return Target("docker", "opt", "docker builder prune + image prune（仅 dangling 镜像；占用列为可回收总量）",
                  avail=lambda c: _docker_up(), paths=lambda c: [], size=_docker_reclaimable, clean=clean)


__all__ = ["uv", "pip", "pnpm", "npm", "bun", "yarn", "go", "cargo", "nuget", "vcpkg", "precommit",
           "gradle", "maven", "docker", "precommit_home", "clean_from_paths"]

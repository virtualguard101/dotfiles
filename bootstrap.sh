#!/usr/bin/env bash
# 新机器引导（Arch Linux）：安装 git / chezmoi / uv，把仓库克隆到 chezmoi 默认源目录，
# 然后交给 `dotfiles restore`。其余逻辑都在 Python 中，本脚本只做 uv 出现之前必须由 shell 完成的事。
#
# 用法（先下载、阅读，再运行；不要 curl | bash，否则无法交互输入私钥）:
#   curl -fsSLO https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.sh
#   bash bootstrap.sh                 # 只显示恢复计划
#   bash bootstrap.sh --apply         # 执行；其他参数原样传给 dotfiles restore
#
# 选项: --repo URL  --branch 分支  （也可用环境变量 DOTFILES_REPO / DOTFILES_BRANCH）
set -Eeuo pipefail

REPO_URL=${DOTFILES_REPO:-https://github.com/virtualguard101/dotfiles.git}
BRANCH=${DOTFILES_BRANCH:-main}
args=()
while (( $# )); do
  case $1 in
    --repo) REPO_URL=${2:?--repo 需要参数}; shift 2 ;;
    --branch) BRANCH=${2:?--branch 需要参数}; shift 2 ;;
    --) shift; args+=("$@"); break ;;
    *) args+=("$1"); shift ;;
  esac
done

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[[ $(uname -s) == Linux ]] || die "仅支持 Linux；Windows 请使用 bootstrap.ps1"
(( EUID != 0 )) || die "请以普通用户运行（需要时会调用 sudo）"

# ---------------------------------------------------------------- 1. 先决工具
need=()
for tool in git chezmoi uv; do
  command -v "$tool" >/dev/null 2>&1 || need+=("$tool")
done
if (( ${#need[@]} )); then
  command -v pacman >/dev/null 2>&1 || die "缺少 ${need[*]}；请用本机的包管理器安装后重试"
  log "安装 ${need[*]}（pacman，官方仓库）"
  sudo pacman -S --needed "${need[@]}"
fi

# ---------------------------------------------------------------- 2. 仓库
SRC=${XDG_DATA_HOME:-$HOME/.local/share}/chezmoi
repo_id() {   # https://github.com/a/b(.git) 与 git@github.com:a/b(.git) 视为同一仓库
  sed -E 's#^(https?://|ssh://)?(git@)?##; s#:#/#; s#\.git$##; s#/$##' <<<"$1"
}
if [[ -d $SRC/.git ]]; then
  origin=$(git -C "$SRC" remote get-url origin 2>/dev/null || true)
  [[ $(repo_id "$origin") == "$(repo_id "$REPO_URL")" ]] ||
    die "$SRC 已是另一个仓库的克隆（origin: ${origin:-无}）；请先移走它"
  log "使用已有的克隆 $SRC"
  if [[ -z $(git -C "$SRC" status --porcelain) ]]; then
    git -C "$SRC" pull --ff-only --quiet || log "无法快进更新（离线或本地有提交），继续使用当前版本"
  else
    log "工作区有未提交的改动，不自动更新"
  fi
elif [[ -e $SRC && -n $(ls -A "$SRC" 2>/dev/null) ]]; then
  die "$SRC 已存在且不是 git 克隆；请先移走它"
else
  log "克隆 $REPO_URL（$BRANCH）到 $SRC"
  git clone --branch "$BRANCH" "$REPO_URL" "$SRC"
fi
[[ -f $SRC/.chezmoiroot && -f $SRC/pyproject.toml ]] || die "$SRC 不是预期的仓库布局（缺少 .chezmoiroot / pyproject.toml）"

# ---------------------------------------------------------------- 3. 交给 Python
[[ -t 0 ]] || log "标准输入不是终端：需要交互的步骤（私钥、sudo）请改用 --key-file 或在终端中运行"
log "运行 dotfiles restore ${args[*]}"
exec uv run --project "$SRC" --frozen --no-dev --quiet dotfiles restore "${args[@]}"

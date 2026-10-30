#!/usr/bin/env bash
# Fresh-machine bootstrap (Arch Linux): installs git / chezmoi / uv, clones the repo into chezmoi's
# default source dir, then hands over to `dotfiles restore`. Everything else lives in Python; this
# script only does what must happen before uv exists.
#
# Usage (download, read, then run; do not curl | bash — the key prompt needs a terminal):
#   curl -fsSLO https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.sh
#   bash bootstrap.sh                 # show the recovery plan only
#   bash bootstrap.sh --apply         # execute; other arguments are passed to dotfiles restore
#
# Options: --repo URL  --branch NAME  (or the env vars DOTFILES_REPO / DOTFILES_BRANCH)
set -Eeuo pipefail

REPO_URL=${DOTFILES_REPO:-https://github.com/virtualguard101/dotfiles.git}
BRANCH=${DOTFILES_BRANCH:-main}
args=()
while (( $# )); do
  case $1 in
    --repo) REPO_URL=${2:?--repo needs a value}; shift 2 ;;
    --branch) BRANCH=${2:?--branch needs a value}; shift 2 ;;
    --) shift; args+=("$@"); break ;;
    *) args+=("$1"); shift ;;
  esac
done

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[[ $(uname -s) == Linux ]] || die "Linux only; on Windows use bootstrap.ps1"
(( EUID != 0 )) || die "run as a regular user (sudo is called when needed)"

# ---------------------------------------------------------------- 1. prerequisites
need=()
for tool in git chezmoi uv; do
  command -v "$tool" >/dev/null 2>&1 || need+=("$tool")
done
if (( ${#need[@]} )); then
  command -v pacman >/dev/null 2>&1 || die "missing ${need[*]}; install them with your package manager and retry"
  log "installing ${need[*]} (pacman, official repos)"
  sudo pacman -S --needed "${need[@]}"
fi

# ---------------------------------------------------------------- 2. repository
SRC=${XDG_DATA_HOME:-$HOME/.local/share}/chezmoi
repo_id() {   # https://github.com/a/b(.git) and git@github.com:a/b(.git) are the same repo
  sed -E 's#^(https?://|ssh://)?(git@)?##; s#:#/#; s#\.git$##; s#/$##' <<<"$1"
}
if [[ -d $SRC/.git ]]; then
  origin=$(git -C "$SRC" remote get-url origin 2>/dev/null || true)
  [[ $(repo_id "$origin") == "$(repo_id "$REPO_URL")" ]] ||
    die "$SRC is already a clone of another repo (origin: ${origin:-none}); move it away first"
  log "using the existing clone $SRC"
  if [[ -z $(git -C "$SRC" status --porcelain) ]]; then
    git -C "$SRC" pull --ff-only --quiet || log "cannot fast-forward (offline or local commits); keeping the current version"
  else
    log "working tree has uncommitted changes; not updating"
  fi
elif [[ -e $SRC && -n $(ls -A "$SRC" 2>/dev/null) ]]; then
  die "$SRC exists and is not a git clone; move it away first"
else
  log "cloning $REPO_URL ($BRANCH) into $SRC"
  git clone --branch "$BRANCH" "$REPO_URL" "$SRC"
fi
[[ -f $SRC/.chezmoiroot && -f $SRC/pyproject.toml ]] || die "$SRC does not have the expected layout (missing .chezmoiroot / pyproject.toml)"

# ---------------------------------------------------------------- 3. hand over to Python
[[ -t 0 ]] || log "stdin is not a terminal: interactive steps (key, sudo) need --key-file or a real terminal"
log "running dotfiles restore ${args[*]}"
exec uv run --project "$SRC" --frozen --no-dev --quiet dotfiles restore "${args[@]}"

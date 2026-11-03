#!/usr/bin/env bash
# Arch follow-up after the Kali work of 2026-11-04 (0.5.3, gnome tag, fcitx5 notifications). Run on
# Arch, from a regular user shell in a terminal (some steps ask for sudo):
#
#   bash scripts/arch.sh            # show what would change (nothing is written)
#   bash scripts/arch.sh --apply    # do it; asks before each step that writes (--yes skips that)
#
# Every step checks first and skips what is already done, so rerunning it is safe.
# Steps:
#   1. git pull --ff-only (main, clean working tree)
#   2. chezmoi config: tags should be arch, kde (+ program tags), without gnome; regenerated if the
#      template output differs
#   3. dotfiles apply. Expected: only .config/fcitx5/conf/notifications.conf (the rime entry is
#      dropped). fcitx5 rewrites its config from memory on exit, so a running fcitx5 is told to
#      reload (fcitx5-remote -r) right after the apply
#   4. leftovers of the pruning (todo 6.2): VM guest tools and rime packages, vboxservice.service
#      (sudo; skipped when already removed)
#   5. dotfiles backup (archpkg.txt, /etc with sudo): preview, then backup --commit, then git push
#   6. dotfiles doctor
set -Eeuo pipefail

# The whole script is read before anything runs: step 1 may pull a new version of this file.
main() {
apply=0 yes=0
for arg in "$@"; do
  case $arg in
    --apply) apply=1 ;;
    -y|--yes) yes=1 ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

[[ $(uname -s) == Linux ]] || die "Linux only"
(( EUID != 0 )) || die "run as a regular user"
# shellcheck disable=SC1091
id=$(. /etc/os-release && printf '%s' "${ID:-}")
[[ $id == arch ]] || die "this script is for Arch (os-release ID is '${id:-unknown}')"
REPO=${XDG_DATA_HOME:-$HOME/.local/share}/chezmoi
[[ -f $REPO/.chezmoiroot ]] || die "no dotfiles repo at $REPO"
cd "$REPO"
command -v uv >/dev/null || die "uv not found (open a new shell, or see bootstrap.sh)"
(( apply )) || log "preview only; rerun with --apply to make the changes"

# ---------------------------------------------------------------- 1. update the repo
log "1. update the repo"
branch=$(git branch --show-current)
[[ $branch == main ]] || die "on branch '$branch'; switch to main first (git switch main)"
[[ -z $(git status --porcelain) ]] || die "the working tree has uncommitted changes; commit or stash them first"
git fetch --quiet origin
behind=$(git rev-list --count HEAD..origin/main)
if (( behind )); then
  info "$behind new commits on origin/main"
  (( apply )) || info "(the previews below show the current checkout, not the pulled one)"
  step "git pull --ff-only" git pull --ff-only --quiet || true
else
  info "up to date"
fi

# ---------------------------------------------------------------- 2. chezmoi config (tags)
log "2. chezmoi config"
tags=$(chezmoi data --format json 2>/dev/null \
  | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin).get("tags") or []))' 2>/dev/null || true)
info "current tags: ${tags:-none}"
for t in arch kde; do
  [[ " $tags " == *" $t "* ]] || warn "tag '$t' is missing; the regeneration below should add it"
done
[[ " $tags " != *" gnome "* ]] || warn "tag 'gnome' is set: gnome-shell is on PATH, so the Kali fcitx5 autostart entry would deploy here"
step "regenerate the config if it differs from the template (dotfiles restore --apply --only config)" \
  uv run dotfiles restore --apply --only config || true

# ---------------------------------------------------------------- 3. deploy (fcitx5-aware)
log "3. dotfiles apply"
fcitx_pending=0
pending=$(chezmoi status --exclude externals 2>/dev/null || true)
[[ $pending == *".config/fcitx5/"* ]] && fcitx_pending=1
uv run dotfiles apply || true
if step "deploy the changes above (overwritten files are backed up first)" uv run dotfiles apply --apply; then
  if (( fcitx_pending )) && pgrep -x fcitx5 >/dev/null; then
    # otherwise the running fcitx5 writes its in-memory config (with the rime entry) back on exit
    fcitx5-remote -r && info "fcitx5 reloaded its config"
  fi
fi

# ---------------------------------------------------------------- 4. pruning leftovers (todo 6.2)
log "4. VM guest tools and rime"
pkgs=()
for p in virtualbox-guest-utils open-vm-tools fcitx5-rime rime-ice-git rime-pinyin-moegirl; do
  pacman -Q "$p" >/dev/null 2>&1 && pkgs+=("$p")
done
if systemctl is-enabled --quiet vboxservice.service 2>/dev/null; then
  step "sudo systemctl disable --now vboxservice.service" sudo systemctl disable --now vboxservice.service || true
fi
if (( ${#pkgs[@]} )); then
  info "still installed: ${pkgs[*]}"
  step "sudo pacman -Rns ${pkgs[*]} (unneeded dependencies go too)" sudo pacman -Rns "${pkgs[@]}" || true
else
  info "already removed"
fi

# ---------------------------------------------------------------- 5. backup
log "5. dotfiles backup (archpkg.txt, drifted dotfiles, /etc)"
if (( apply )); then
  uv run dotfiles backup -n || true
  step "back up and commit (dotfiles backup --commit; asks for sudo for /etc)" \
    uv run dotfiles backup --commit || true
  ahead=$(git rev-list --count origin/main..HEAD)
  if (( ahead )); then
    git log --oneline origin/main..HEAD | sed 's/^/    /'
    step "git push ($ahead commits)" git push --quiet origin main || true
  fi
else
  info "(preview) would: dotfiles backup -n, then backup --commit and git push, each after confirmation"
fi

# ---------------------------------------------------------------- 6. check
log "6. dotfiles doctor"
uv run dotfiles doctor || true

cat <<'EOF'

==> afterwards:
    - After the next logout / reboot (fcitx5 exits and rewrites its config), chezmoi status should
      list nothing under .config/fcitx5. If it does: dotfiles apply shows the diff; Kali writes
      Chinese now too, so a difference in the comments' language means one side lost LANGUAGE.
    - Undecided from the pruning: ~/.local/share/mamba (mamba's root prefix; nothing loads it).
    - The old UV_PUBLISH_TOKEN was in the repo history (encrypted): revoke it on PyPI if not done.
EOF
}

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
# run a writing step: only with --apply, and after confirmation unless --yes
step() {
  local what=$1; shift
  if (( ! apply )); then info "(preview) would: $what"; return 1; fi
  if (( ! yes )); then
    read -r -p "    $what? [y/N] " reply
    [[ $reply == [yY]* ]] || { info "skipped"; return 1; }
  fi
  "$@"
}

main "$@"
exit

#!/usr/bin/env bash
# Kali follow-up after pulling the 0.5.0 release and the pruning that came after it. Run on Kali,
# from a regular user shell:
#
#   bash scripts/kali.sh            # show what would change (nothing is written)
#   bash scripts/kali.sh --apply    # do it; asks before each step that writes (--yes skips that)
#
# Steps:
#   1. git pull --ff-only (main, clean working tree)
#   2. regenerate the chezmoi config: tags now include program tags (zeroclaw, taskdog)
#   3. restore oh-my-zsh's push URL (0.4.0's hooks step switched it to SSH; 0.5.0 only does that
#      for your own repos)
#   4. move away the two user units Kali no longer manages (ignored there since 0.5.0, so
#      .chezmoiremove cannot reach them); kept in ~/.local/state/dotfiles/kali-<time>/
#   5. dotfiles apply: deploys the new plaintext .zshrc + encrypted secrets.zsh, the aria2 template,
#      and removes the targets in home/.chezmoiremove (backed up to pre-apply-* first)
#   6. remove the directories those removals left empty
#   7. dotfiles doctor
# Not done here (needs an interactive sudo and your review): the /etc backup, see the end.
set -Eeuo pipefail

apply=0 yes=0
for arg in "$@"; do
  case $arg in
    --apply) apply=1 ;;
    -y|--yes) yes=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

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

[[ $(uname -s) == Linux ]] || die "Linux only"
(( EUID != 0 )) || die "run as a regular user"
# shellcheck disable=SC1091
id=$(. /etc/os-release && printf '%s' "${ID:-}")
[[ $id == kali ]] || die "this script is for Kali (os-release ID is '${id:-unknown}')"
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
info "current tags: $(chezmoi data --format json 2>/dev/null | python3 -c 'import json,sys; print(", ".join(json.load(sys.stdin).get("tags") or []) or "none")' 2>/dev/null || echo unknown)"
step "regenerate the config (dotfiles restore --apply --only config)" \
  uv run dotfiles restore --apply --only config || true

# ---------------------------------------------------------------- 3. oh-my-zsh push URL
log "3. oh-my-zsh push URL"
omz=$HOME/.oh-my-zsh
if pushurl=$(git -C "$omz" config --get remote.origin.pushurl 2>/dev/null); then
  info "pushurl is $pushurl (oh-my-zsh is not your repo; fetch and push should both use origin's URL)"
  step "git -C ~/.oh-my-zsh config --unset remote.origin.pushurl" \
    git -C "$omz" config --unset remote.origin.pushurl || true
else
  info "no pushurl override; nothing to do"
fi

# ---------------------------------------------------------------- 4. user units Kali no longer manages
log "4. user units without their program"
units=(zeroclaw.service:.cargo/bin/zeroclaw taskdog-server.service:.local/bin/taskdog-server)
stash=$HOME/.local/state/dotfiles/kali-$(date +%Y%m%d-%H%M%S)
moved=0 seen=0
for entry in "${units[@]}"; do
  unit=${entry%%:*} bin=$HOME/${entry#*:}
  file=$HOME/.config/systemd/user/$unit
  [[ -f $file ]] || continue
  seen=1
  if [[ -e $bin ]]; then
    info "$unit: $bin exists, keeping it (rerun step 2 so its tag gets set)"
    continue
  fi
  if chezmoi source-path "$file" >/dev/null 2>&1; then
    info "$unit: still managed by chezmoi, keeping it"
    continue
  fi
  state=$(systemctl --user is-enabled "$unit" 2>/dev/null || true)
  info "$unit: $bin is not installed; unit is ${state:-not loaded}"
  if step "move ~/.config/systemd/user/$unit to $stash/" mkdir -p "$stash"; then
    [[ $state == enabled ]] && systemctl --user disable --now "$unit" || true
    mv -- "$file" "$stash/"
    moved=1
  fi
done
(( moved )) && systemctl --user daemon-reload
(( seen )) || info "none found"

# ---------------------------------------------------------------- 5. deploy (incl. .chezmoiremove)
log "5. dotfiles apply"
uv run dotfiles apply || true
step "deploy the changes above (removed and overwritten files are backed up first)" \
  uv run dotfiles apply --apply || true

# ---------------------------------------------------------------- 6. directories left empty
log "6. empty directories left by the removals"
found=0
for d in .config/Cursor/User .config/Cursor .config/ghostty .local/share/fcitx5/rime; do
  [[ -d $HOME/$d ]] || continue
  found=1
  if [[ -z $(ls -A "$HOME/$d") ]]; then
    step "rmdir ~/$d" rmdir -- "$HOME/$d" || true
  else
    info "~/$d is not empty, keeping it"
  fi
done
(( found )) || info "none"

# ---------------------------------------------------------------- 7. check
log "7. dotfiles doctor"
uv run dotfiles doctor || true

cat <<'EOF'

==> next, by hand (needs sudo; review before writing anything):
    /etc backup into system/kali/etc:
      uv run dotfiles status          # "/etc backup targets": what would be collected
      uv run dotfiles backup -n       # preview (root-only conffiles are checked with sudo)
      uv run dotfiles backup          # write it; then commit to main and push
    Unwanted entries: uv run dotfiles config add etc.exclude <path>
    Open a new terminal afterwards: ~/.zshrc now loads its tokens from ~/.config/zsh/secrets.zsh.
EOF

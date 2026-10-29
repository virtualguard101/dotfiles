#!/usr/bin/env bash
# dotfiles 备份脚本（Arch Linux）：基于 chezmoi 备份 ~ 与 /etc，敏感文件用 age 加密。
#
#   status                       只读：加密配置、chezmoi 漂移、备份候选、/etc 目标与待处理项、权限
#   backup [-n] [--no-etc] [--commit]
#                                re-add 已托管文件 → 添加候选 → /etc 写入 system/ → 更新 archpkg.txt → check
#   check                        只运行安全检查（明文密钥 / .age 格式 / 必须加密的目标 / 私钥位置），
#                                规则与 pre-commit hook 共用 scripts/hooks/dotfiles_guard.py
#   audit [--apply]              敏感文件权限检查，--apply 时收紧为 600/700
#   restore-etc [--apply] [/etc/路径...]
#                                从 system/ 解密到 tmpfs，与现有 /etc 对比，--apply 时 sudo 安装

set -Eeuo pipefail
shopt -s nullglob

# ---------------------------------------------------------------- 可调整的目标清单

# 推荐纳入 chezmoi 的明文配置（相对 ~；目录会展开为其中的普通文件）
HOME_TRACK=(
  .zshenv .bash_profile .profile .tool-versions
  .config/user-dirs.dirs .config/mimeapps.list
  .config/kdeglobals .config/kwinrc .config/kglobalshortcutsrc .config/kxkbrc
  .config/konsolerc .config/dolphinrc .config/spectaclerc .local/share/konsole
  .config/ghostty .config/bottom .config/htop .config/gh/config.yml
  .config/systemd/user .config/autostart
  .local/share/fcitx5/rime/default.custom.yaml
  .aria2/aria2.conf
)

# 目录展开时忽略的文件
HOME_EXCLUDE=('*.tbcache' '*.lock' '*.log' '*.bak' '*~' '*_history')

# 一律加密的配置（含 token / 凭据）
HOME_SECRET=(
  .wakatime.cfg .npmrc .config/gh/hosts.yml .docker/config.json
)

# 显式纳入的 /etc 文件或目录（目录中只取不属于任何软件包的文件）
ETC_EXTRA=(
  /etc/docker/daemon.json /etc/timeshift/timeshift.json
  /etc/sddm.conf /etc/sddm.conf.d /etc/mkinitcpio.d/linux-zen.preset
  /etc/locale.conf /etc/vconsole.conf /etc/hostname /etc/zsh/zshrc /etc/environment
  /etc/pacman.d/hooks /etc/systemd/system /etc/systemd/journald.conf.d
  /etc/systemd/logind.conf.d /etc/modprobe.d /etc/sysctl.d /etc/udev/rules.d
  /etc/X11/xorg.conf.d /etc/NetworkManager/conf.d /etc/environment.d
)

# 一律加密的 /etc 路径（通常 root 可读，需要 sudo）
ETC_SECRET=(
  /etc/NetworkManager/system-connections /etc/crypttab /etc/sudoers /etc/sudoers.d
  /etc/mihomo/config.yaml /etc/wireguard
)

# 不备份的 /etc 路径：账户数据库/主机密钥/自动生成文件，恢复它们弊大于利
ETC_EXCLUDE=(
  /etc/passwd /etc/group /etc/shadow /etc/gshadow /etc/subuid /etc/subgid
  /etc/machine-id /etc/resolv.conf '/etc/ssh/ssh_host_*'
  '*.pacnew' '*.pacsave' '*.bak' '*.bak.*' '*~'
)

# ---------------------------------------------------------------- 通用工具

if [[ -t 1 ]]; then
  C_R=$'\e[31m' C_G=$'\e[32m' C_Y=$'\e[33m' C_B=$'\e[1;34m' C_D=$'\e[2m' C_0=$'\e[0m'
else
  C_R='' C_G='' C_Y='' C_B='' C_D='' C_0=''
fi
section() { printf '\n%s== %s ==%s\n' "$C_B" "$*" "$C_0"; }
info()    { printf '%s\n' "$*"; }
ok()      { printf '%s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn()    { printf '%s!%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
err()     { printf '%s✗%s %s\n' "$C_R" "$C_0" "$*" >&2; }
die()     { err "$*"; exit 1; }
have()    { command -v "$1" >/dev/null 2>&1; }

DRY=1
run() {
  if (( DRY )); then printf '%s  + %s%s\n' "$C_D" "$*" "$C_0"; else "$@"; fi
}

rel_home() { printf '%s' "${1#"$HOME"/}"; }

TMP_PATHS=()
cleanup() { (( ${#TMP_PATHS[@]} )) && rm -rf -- "${TMP_PATHS[@]}"; return 0; }
trap cleanup EXIT

# 密钥特征与 pre-commit hook 共用一份规则：scripts/hooks/dotfiles_guard.py
GUARD="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/hooks/dotfiles_guard.py"
[[ -f $GUARD ]] || { printf '✗ 缺少 %s\n' "$GUARD" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf '✗ 需要 python3\n' >&2; exit 1; }
# guard scan：0 = 干净，1 = 命中，其他 = 出错。出错按命中处理（宁可多加密，不可漏加密）
has_secret() {
  local rc=0
  python3 "$GUARD" scan -q -- "$1" || rc=$?
  (( rc != 0 ))
}

# 文件或目录 → 其中的普通文件（跳过符号链接、大于 1MiB 的文件与 HOME_EXCLUDE）
expand_files() {
  local p=$1 f pat
  if [[ -f $p && ! -L $p ]]; then printf '%s\n' "$p"; return 0; fi
  [[ -d $p ]] || return 0
  while IFS= read -r f; do
    for pat in "${HOME_EXCLUDE[@]}"; do
      # shellcheck disable=SC2053
      [[ ${f##*/} == $pat ]] && continue 2
    done
    printf '%s\n' "$f"
  done < <(find "$p" -type f -size -1024k 2>/dev/null | sort || true)
}

etc_excluded() {
  local f=$1 pat
  for pat in "${ETC_EXCLUDE[@]}"; do
    # shellcheck disable=SC2053
    [[ $f == $pat ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------- 环境与 chezmoi

[[ $(uname -s) == Linux ]] || die "仅支持 Linux（Windows 请另写 .ps1 入口）"
have chezmoi || die "未找到 chezmoi"
have jq || die "需要 jq 解析 chezmoi 配置"

SRC=$(chezmoi source-path)
SYS_SRC="$SRC/system"
CZ_FLAGS=()
CZ_CONFIG=$(chezmoi dump-config --format json)

declare -A MANAGED=()
load_managed() {
  local p
  MANAGED=()
  while IFS= read -r p; do MANAGED[$p]=1; done \
    < <(chezmoi managed -p absolute -i files,symlinks -x externals)
}

# age 身份文件路径（可能有多个）
age_identities() {
  jq -r '.age.identity // empty, ((.age.identities // [])[])' <<<"$CZ_CONFIG" | sed '/^$/d'
}

preflight_crypto() {
  local id n=0
  [[ $(jq -r '.encryption // empty' <<<"$CZ_CONFIG") == age ]] ||
    die "chezmoi 未启用 age 加密（encryption = \"age\"）"
  jq -e '(.age.recipient // "") != "" or ((.age.recipients // []) | length > 0)' <<<"$CZ_CONFIG" >/dev/null ||
    die "chezmoi 配置缺少 age.recipient"
  while IFS= read -r id; do
    n=$((n + 1))
    [[ -r $id ]] || die "age 身份文件不可读: $id"
    case $id in "$SRC"/*) die "age 私钥位于源码目录内，会被提交: $id" ;; esac
  done < <(age_identities)
  (( n )) || die "chezmoi 配置缺少 age.identity"
  mkdir -p "$SYS_SRC"   # chezmoi ignored 只列出已存在的路径
  chezmoi ignored | grep -qx system || die ".chezmoiignore 中缺少 system/，否则 /etc 备份会被部署到 ~/system"
}

# 独立的 /etc chezmoi 实例：源 = system/，目标 = tmpfs 暂存区
STAGE=''
new_stage() {
  STAGE=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/chezmoi-etc.XXXXXX")
  chmod 700 "$STAGE"
  TMP_PATHS+=("$STAGE" "$STAGE.state.boltdb")
}
cz_sys() {
  local stage=$1; shift
  chezmoi --source "$SYS_SRC" --destination "$stage" \
    --persistent-state "$stage.state.boltdb" "$@"
}

SUDO_READY=0 SUDO_PROMPT=0
ensure_sudo() {
  (( SUDO_READY )) && return 0
  if sudo -n true 2>/dev/null || { (( SUDO_PROMPT )) && [[ -t 0 ]] && sudo -v; }; then SUDO_READY=1; return 0; fi
  return 1
}

# ---------------------------------------------------------------- /etc 目标收集

# 输出 "路径<TAB>plain|secret"
collect_etc_targets() {
  local -A owned=() seen=()
  local f p
  {
  if have pacman; then
    while IFS= read -r f; do owned[$f]=1; done < <(pacman -Qlq 2>/dev/null | grep '^/etc/')
    # 被用户修改过的软件包配置文件
    while IFS= read -r f; do
      etc_excluded "$f" || [[ -n ${seen[$f]:-} ]] || { seen[$f]=1; printf '%s\tplain\n' "$f"; }
    done < <(LC_ALL=C pacman -Qii 2>/dev/null | grep -oE '/etc/[^ ]+ \[modified\]' | cut -d' ' -f1 | sort -u)
  fi
  for p in "${ETC_EXTRA[@]}"; do
    [[ -e $p ]] || continue
    while IFS= read -r f; do
      [[ -d $p && -n ${owned[$f]:-} ]] && continue
      etc_excluded "$f" || [[ -n ${seen[$f]:-} ]] || { seen[$f]=1; printf '%s\tplain\n' "$f"; }
    done < <(expand_files "$p")
  done
  for p in "${ETC_SECRET[@]}"; do
    if [[ -d $p ]]; then
      if [[ -r $p && -x $p ]]; then find "$p" -type f 2>/dev/null || true
      elif ensure_sudo; then sudo find "$p" -type f 2>/dev/null || true
      else warn "无法列出 $p（需要 sudo）"; fi
    elif sudo -n test -e "$p" 2>/dev/null || [[ -e $p ]]; then
      printf '%s\n' "$p"
    fi | while IFS= read -r f; do
      etc_excluded "$f" || printf '%s\tsecret\n' "$f"
    done
  done
  } | sort -t$'\t' -k1,1 -k2,2r | awk -F'\t' '!s[$1]++'   # 同一路径 secret 优先
}

# ---------------------------------------------------------------- status

cmd_status() {
  section "加密配置"
  info "源目录:   $SRC"
  info "加密方式: $(jq -r '.encryption // "未设置"' <<<"$CZ_CONFIG")"
  info "接收者:   $(jq -r '[.age.recipient // empty, ((.age.recipients // [])[])] | join(", ")' <<<"$CZ_CONFIG")"
  local id
  while IFS= read -r id; do
    if [[ -r $id ]]; then info "身份文件: $id（可读）"; else warn "身份文件不可读: $id"; fi
  done < <(age_identities)

  section "chezmoi 漂移（chezmoi status）"
  chezmoi status || true

  section "备份候选（未托管）"
  load_managed
  local rel f kind flag entry
  for entry in "${HOME_SECRET[@]/#/secret:}" "${HOME_TRACK[@]/#/track:}"; do
    kind=${entry%%:*} rel=${entry#*:}
    while IFS= read -r f; do
      [[ -n ${MANAGED[$f]:-} ]] && continue
      flag=plain
      if [[ $kind == secret ]] || has_secret "$f"; then flag=encrypt; fi
      grep -qF -- "$HOME" "$f" 2>/dev/null && flag="$flag,含绝对 home 路径→跳过"
      printf '  %-50s %s\n' "$(rel_home "$f")" "$flag"
    done < <(expand_files "$HOME/$rel")
  done

  section "/etc 备份目标"
  collect_etc_targets | awk -F'\t' '{printf "  %-52s %s\n", $1, $2}'

  section "/etc 待处理项"
  info "pacnew / pacsave（用 pacdiff 合并）："
  { find /etc -xdev \( -name '*.pacnew' -o -name '*.pacsave' \) 2>/dev/null || true; } | sort | sed 's/^/  /'
  info "历史备份文件（确认后手动删除）："
  { find /etc -xdev -type f \( -name '*.bak' -o -name '*.bak.*' -o -name '*.OLD' -o -name '*~' -o -name '*.backup-*' \) -not -path '/etc/pacman.d/gnupg/*' 2>/dev/null || true; } | sort | sed 's/^/  /'
  if have etckeeper && [[ ! -d /etc/.git ]]; then
    info "etckeeper 已安装但未初始化：/etc 已由本脚本的 system/ 备份，可考虑卸载 etckeeper 以免重复"
  fi
  [[ -r /etc/timeshift/timeshift.json ]] && info "timeshift 快照已启用：可整盘回滚，但快照与系统同盘，不能替代异地备份"

  section "权限审计"
  audit_perms
  info ""
  info "下一步: backup -n 预览备份；audit --apply 收紧权限"
}

# ---------------------------------------------------------------- backup

backup_home_managed() {
  section "已托管文件 re-add"
  local f
  # 明文托管的文件若出现了密钥，先转为加密，再 re-add
  while IFS= read -r f; do
    [[ -f $f ]] && has_secret "$f" || continue
    warn "明文托管文件出现密钥特征，转为加密: $(rel_home "$f")"
    chezmoi "${CZ_FLAGS[@]}" add --encrypt -- "$f"
  done < <(chezmoi managed -p absolute -i files -x externals,encrypted,templates)
  chezmoi "${CZ_FLAGS[@]}" re-add
  ok "re-add 完成"
}

add_home_file() {
  local f=$1 kind=$2
  [[ -n ${MANAGED[$f]:-} ]] && return 0
  if grep -qF -- "$HOME" "$f" 2>/dev/null; then
    warn "跳过（含绝对 home 路径，建议改为模板 {{ .chezmoi.homeDir }}）: $(rel_home "$f")"
    return 0
  fi
  if [[ $kind == secret ]] || has_secret "$f"; then
    info "  加密  $(rel_home "$f")"
    chezmoi "${CZ_FLAGS[@]}" add --encrypt -- "$f"
  elif ! chezmoi "${CZ_FLAGS[@]}" add --secrets error -- "$f" 2>/dev/null; then
    warn "chezmoi 密钥扫描命中，改为加密: $(rel_home "$f")"
    chezmoi "${CZ_FLAGS[@]}" add --encrypt -- "$f"
  else
    info "  明文  $(rel_home "$f")"
  fi
}

backup_home_candidates() {
  section "添加候选配置"
  load_managed
  local rel f
  for rel in "${HOME_SECRET[@]}"; do
    while IFS= read -r f; do add_home_file "$f" secret; done < <(expand_files "$HOME/$rel")
  done
  for rel in "${HOME_TRACK[@]}"; do
    while IFS= read -r f; do add_home_file "$f" auto; done < <(expand_files "$HOME/$rel")
  done
}

backup_etc() {
  section "/etc → system/（独立 chezmoi 实例）"
  local stage path kind dst mode encrypt n=0 targets
  new_stage; stage=$STAGE
  mkdir -p "$SYS_SRC"
  targets=$(collect_etc_targets)
  while IFS=$'\t' read -r path kind; do
    [[ -n $path ]] || continue
    dst="$stage$path"
    mkdir -p "$(dirname "$dst")"
    if [[ -r $path ]]; then
      mode=$(stat -c %a "$path")
      cp -- "$path" "$dst"
    elif ensure_sudo; then
      mode=$(sudo stat -c %a "$path")
      (umask 077; sudo cat -- "$path" >"$dst")
    else
      warn "跳过（无读权限且无 sudo）: $path"; continue
    fi
    chmod "$mode" "$dst"
    # 非全局可读的 /etc 文件同样视为敏感
    encrypt=0
    [[ $kind == secret ]] && encrypt=1
    (( 8#$mode & 8#004 )) || encrypt=1
    has_secret "$dst" && encrypt=1
    if (( encrypt )); then
      info "  加密  $path"
      cz_sys "$stage" "${CZ_FLAGS[@]}" add --encrypt -- "$dst"
    else
      info "  明文  $path"
      cz_sys "$stage" "${CZ_FLAGS[@]}" add --secrets error -- "$dst" 2>/dev/null ||
        { warn "chezmoi 密钥扫描命中，改为加密: $path"; cz_sys "$stage" "${CZ_FLAGS[@]}" add --encrypt -- "$dst"; }
    fi
    n=$((n + 1))
  done <<<"$targets"
  ok "/etc 共处理 $n 个文件（暂存区位于 tmpfs，退出时删除）"
}

backup_pkglist() {
  section "软件包清单"
  have pacman || return 0
  local tmp
  tmp=$(mktemp); TMP_PATHS+=("$tmp")
  pacman -Qqe >"$tmp"
  if cmp -s "$tmp" "$SRC/archpkg.txt"; then ok "archpkg.txt 无变化"; return 0; fi
  diff "$SRC/archpkg.txt" "$tmp" | grep -E '^[<>]' | sed 's/^</  -/; s/^>/  +/' || true
  (( DRY )) || cp "$tmp" "$SRC/archpkg.txt"
}

# 安全检查：任何一项失败都返回非零。内容检查（age 密文 / 必须加密的目标 / 密钥特征）
# 由 dotfiles_guard.py tree 完成，与 pre-commit hook 规则一致；这里额外检查私钥文件位置。
gate() {
  section "安全检查"
  local bad=0 id
  while IFS= read -r id; do
    if git -C "$SRC" ls-files --error-unmatch -- "$id" >/dev/null 2>&1; then
      err "age 私钥被 git 跟踪: $id"; bad=1
    fi
  done < <(age_identities)
  (cd "$SRC" && python3 "$GUARD" tree) || bad=1
  (( bad )) && { err "安全检查未通过，不会提交"; return 1; }
  ok "未发现明文密钥，所有 .age 文件格式正确，敏感目标均已加密"
}

cmd_check() {
  preflight_crypto
  gate
}

cmd_backup() {
  local with_etc=1 commit=0
  while (( $# )); do
    case $1 in
      -n|--dry-run) CZ_FLAGS=(--dry-run --verbose) ;;
      --no-etc) with_etc=0 ;;
      --commit) commit=1 ;;
      *) die "backup: 未知参数 $1" ;;
    esac
    shift
  done
  (( ${#CZ_FLAGS[@]} )) || DRY=0
  SUDO_PROMPT=1
  preflight_crypto
  backup_home_managed
  backup_home_candidates
  (( with_etc )) && backup_etc
  backup_pkglist
  gate
  section "仓库状态"
  git -C "$SRC" status --short
  if (( commit && !DRY )); then
    git -C "$SRC" add -A
    git -C "$SRC" diff --cached --quiet && { ok "无变更可提交"; return 0; }
    git -C "$SRC" commit -q -m "backup: sync dotfiles and /etc ($(date +%F))"
    ok "已提交 $(git -C "$SRC" log -1 --format=%h)（未推送，确认后 git push）"
  fi
  info ""
  info "提醒：age 私钥不在仓库中，请另行离线备份（密码管理器 / U 盘）："
  age_identities | sed 's/^/  /'
}

# ---------------------------------------------------------------- audit

# 检查 path 的权限是否超出 want（八进制），apply 时收紧
check_mode() {
  local path=$1 want=$2 cur
  [[ -e $path && ! -L $path ]] || return 0
  cur=$(stat -c %a "$path")
  if (( 8#$cur & ~8#$want & 8#777 )); then
    printf '  %-50s %s → %s\n' "${path/#$HOME/\~}" "$cur" "$want"
    AUDIT_HITS=$((AUDIT_HITS + 1))
    (( DRY )) || chmod "$want" "$path"
  fi
}

audit_perms() {
  AUDIT_HITS=0
  local rel f id
  for rel in "${HOME_SECRET[@]}" .pypirc .netrc .git-credentials .ssh/config; do
    check_mode "$HOME/$rel" 600
  done
  check_mode "$HOME/.ssh" 700
  check_mode "$HOME/.gnupg" 700
  for f in "$HOME"/.ssh/*; do
    [[ -f $f && $f != *.pub && ${f##*/} != known_hosts* && ${f##*/} != config ]] && check_mode "$f" 600
  done
  while IFS= read -r id; do
    check_mode "$id" 600
    check_mode "$(dirname "$id")" 700
  done < <(age_identities 2>/dev/null)
  if (( AUDIT_HITS == 0 )); then ok "敏感文件权限正常"
  elif (( DRY )); then info "共 $AUDIT_HITS 项权限过宽，audit --apply 收紧"
  else ok "已收紧 $AUDIT_HITS 项权限"; fi
}

cmd_audit() {
  [[ ${1:-} == --apply ]] && DRY=0
  section "权限审计"
  audit_perms
}

# ---------------------------------------------------------------- restore-etc

cmd_restore_etc() {
  local filter=() stage f target mode enc_list n=0
  while (( $# )); do
    case $1 in --apply) DRY=0 ;; /etc/*) filter+=("$1") ;; *) die "restore-etc: 未知参数 $1" ;; esac
    shift
  done
  [[ -d $SYS_SRC/etc ]] || die "system/etc 不存在，尚未备份过 /etc"
  preflight_crypto
  new_stage; stage=$STAGE
  cz_sys "$stage" apply --force
  enc_list=$(cz_sys "$stage" managed -i encrypted -p absolute)
  # 预览时 sudo 可选（不可读的文件无法比较）；--apply 时必须
  SUDO_PROMPT=1
  local rsudo=()
  if ensure_sudo; then rsudo=(sudo); else (( DRY )) || die "恢复 /etc 需要 sudo"; fi
  while IFS= read -r f; do
    target=${f#"$stage"}
    if (( ${#filter[@]} )); then
      local hit=0 p
      for p in "${filter[@]}"; do [[ $target == "$p" || $target == "$p"/* ]] && hit=1; done
      (( hit )) || continue
    fi
    "${rsudo[@]}" cmp -s -- "$f" "$target" 2>/dev/null && continue
    n=$((n + 1))
    section "$target"
    if [[ -e $target && ! -r $target && ${#rsudo[@]} -eq 0 ]]; then
      info "（无读权限，无法比较）"
    elif grep -qxF -- "$f" <<<"$enc_list"; then
      info "（加密文件，不在终端显示内容差异）"
    else
      "${rsudo[@]}" diff -u --label "$target" --label "备份" -- "$target" "$f" 2>/dev/null || true
    fi
    mode=$("${rsudo[@]}" stat -c %a -- "$target" 2>/dev/null || stat -c %a -- "$f")
    run sudo install -D -o root -g root -m "$mode" -- "$f" "$target"
  done < <(find "$stage" -type f | sort)
  (( n )) || ok "/etc 与备份一致"
  (( DRY && n )) && info "加 --apply 执行以上 install；修改引导/initramfs 相关文件后记得 grub-mkconfig / mkinitcpio -P"
  return 0
}

# ---------------------------------------------------------------- main

usage() { sed -n '2,/^$/{s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"; }

main() {
  local cmd=${1:-}
  (( $# )) && shift
  case $cmd in
    status) cmd_status "$@" ;;
    backup) cmd_backup "$@" ;;
    check) cmd_check "$@" ;;
    audit) cmd_audit "$@" ;;
    restore-etc) cmd_restore_etc "$@" ;;
    -h|--help|help|'') usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"

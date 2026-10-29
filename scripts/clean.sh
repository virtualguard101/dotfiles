#!/usr/bin/env bash
# 缓存清理脚本（Arch Linux）：以 ~/.cache 为主，覆盖本机已安装工具的包缓存与应用缓存。
#
#   scan                         只读：各目标占用 / 状态、~/.cache 未覆盖项、残留目录、孤立包
#   list                         列出全部清理目标及其层级（不计算大小）
#   run [--apply] [--deep] [-x 目标]... [目标|default|all]...
#                                默认 dry-run，只打印将执行的命令；不指定目标时等同 default
#   leftovers                    只读：已卸载应用的残留目录、久未使用的 Electron 配置目录

set -Eeuo pipefail
shopt -s nullglob

# ---------------------------------------------------------------- 可调参数（同名环境变量覆盖）

THUMBNAIL_DAYS=${THUMBNAIL_DAYS:-90}   # thumbnails：清理多少天未访问的缩略图
STALE_DAYS=${STALE_DAYS:-180}          # leftovers：多少天无写入视为久未使用
JOURNAL_MAX=${JOURNAL_MAX:-1G}         # journal：vacuum 后保留的大小
PACCACHE_KEEP=${PACCACHE_KEEP:-1}      # pacman：每个包保留的版本数
UNCOVERED_MIN_MB=${UNCOVERED_MIN_MB:-10}

C=${XDG_CACHE_HOME:-$HOME/.cache}
CFG=${XDG_CONFIG_HOME:-$HOME/.config}
DATA=${XDG_DATA_HOME:-$HOME/.local/share}

# ---------------------------------------------------------------- 目标清单
# id|层级|说明。default 层被 run 默认包含；opt 层必须显式点名（或用 all）。

TARGETS=(
  "yay|default|AUR 构建目录（yay/paru）：已安装包的克隆 git clean -ffdx，已卸载包的克隆整体删除"
  "firefox|default|Firefox 网络缓存 cache2 / startupCache / thumbnails（配置文件被占用时跳过）"
  "electron|default|~/.config 下 Electron/Chromium 应用的 Cache、Code Cache、GPUCache 等（应用运行时跳过）"
  "uv|default|uv cache prune（--deep: uv cache clean）"
  "pip|default|pip cache purge"
  "pnpm|default|pnpm store prune + ~/.cache/pnpm 元数据缓存"
  "npm|default|npm cache clean --force + _logs（--deep 额外删除 _npx）"
  "bun|default|bun pm cache rm"
  "yarn|default|~/.yarn/berry/cache 与 ~/.cache/yarn"
  "go|default|go clean -cache（--deep 额外 go clean -modcache）"
  "cargo|default|~/.cargo/registry/src 与 git/checkouts（--deep 额外 registry/cache）"
  "nuget|default|dotnet nuget locals http-cache --clear"
  "vcpkg|default|\$VCPKG_ROOT/buildtrees 与 packages（--deep 额外 downloads）"
  "precommit|default|pre-commit gc + 删除遗留的 stash 补丁 patch*（--deep: pre-commit clean）"
  "lsp|default|编辑器语言服务缓存 ~/.cache/typescript ~/.cache/jedi（语言服务运行时跳过）"
  "wine|default|wine-mono/gecko 安装包与 winetricks 下载缓存"
  "kde|default|DrKonqi 崩溃报告、KRunner 书签副本、Discover/AppStream 目录缓存"
  "thumbnails|default|超过 THUMBNAIL_DAYS 天未访问的缩略图（--deep: 全部）"
  "zcompdump|default|旧 zsh 版本遗留的 ~/.zcompdump-*"
  "shader|opt|Mesa/RADV/Qt 着色器缓存（删除后首次启动游戏/应用会卡顿）"
  "gradle|opt|~/.gradle/caches 与 daemon 日志（先 gradle --stop）"
  "maven|opt|~/.m2/repository（删除后需重新下载全部依赖）"
  "trash|opt|清空回收站"
  "pacman|opt|sudo paccache -rk\$PACCACHE_KEEP 与 -ruk0（已卸载包的缓存全部删除）"
  "journal|opt|sudo journalctl --vacuum-size=\$JOURNAL_MAX"
  "docker|opt|docker builder prune + image prune（仅 dangling 镜像）"
)

# 已卸载应用的残留：检测名（包名或命令，任一存在即视为已安装）|路径（相对 ~）
LEFTOVER_MAP=(
  "cursor-bin cursor|.config/Cursor"
  "google-chrome google-chrome-stable|.config/google-chrome .cache/google-chrome"
  "chromium|.config/chromium .cache/chromium"
  "visual-studio-code-bin code|.config/Code .vscode"
  "discord|.config/discord"
  "notion-app-electron|.config/Notion"
  "obsidian|.config/obsidian"
  "feishu-bin|.config/LarkShell .cache/LarkShell"
  "telegram-desktop|.local/share/TelegramDesktop"
  "steam|.local/share/Steam"
  "wine|.wine .cache/wine"
)

ELECTRON_CACHE_DIRS=(Cache "Code Cache" GPUCache DawnGraphiteCache DawnWebGPUCache GPUPersistentCache GrShaderCache ShaderCache)
VSCODE_FAMILY=(Code "Code - OSS" VSCodium Cursor Windsurf)
VSCODE_CACHE_DIRS=(CachedData CachedExtensionVSIXs CachedProfilesData logs)

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
tilde()   { printf '%s' "${1/#"$HOME"/\~}"; }

DRY=1 DEEP=0 FAILED=0
run() {
  if (( DRY )); then printf '%s  + %s%s\n' "$C_D" "$*" "$C_0"; return 0; fi
  "$@" || { warn "命令失败（继续）: $*"; FAILED=1; }
}
rm_paths() {
  local p
  for p in "$@"; do [[ -e $p || -L $p ]] && run rm -rf -- "$p"; done
  return 0
}

# 若干路径的磁盘占用（KiB）；路径经 NUL 分隔传给 du，避免参数过长
kib_of() {
  local p existing=()
  for p in "$@"; do [[ -e $p ]] && existing+=("$p"); done
  (( ${#existing[@]} )) || { echo 0; return; }
  { printf '%s\0' "${existing[@]}" | du -sck --files0-from=- 2>/dev/null || true; } | tail -1 | cut -f1
}
human() {
  (( ${1:-0} > 0 )) || { echo 0; return; }
  numfmt --to=iec --from-unit=1024 --format='%.1f' "$1" 2>/dev/null | sed 's/\.0\([A-Z]\)/\1/' || echo "${1}K"
}

# 锁文件对应的进程是否存活。支持符号链接锁（Chromium SingletonLock → host-PID，
# Firefox lock → IP:+PID）与写有 PID 的普通文件（VS Code code.lock）。
lock_alive() {
  local f=$1 t pid
  if [[ -L $f ]]; then t=$(readlink -- "$f"); pid=${t##*[-+]}
  elif [[ -f $f ]]; then pid=$(head -c 32 -- "$f" | tr -dc 0-9)
  else return 1; fi
  [[ $pid =~ ^[0-9]+$ ]] && [[ -d /proc/$pid ]]
}

# 当前用户是否有进程的 exe 或 argv[0..1] 匹配 ERE（用于无锁文件的应用）
PROC_SNAPSHOT=''
proc_match() {
  [[ -n $PROC_SNAPSHOT ]] || PROC_SNAPSHOT=$(ps -u "$UID" -o args= 2>/dev/null | awk '{print $1, $2}')
  grep -qiE -- "$1" <<<"$PROC_SNAPSHOT"
}

pkg_or_cmd_present() {
  local n
  for n in "$@"; do
    pacman -Qq "$n" >/dev/null 2>&1 && return 0
    have "$n" && return 0
  done
  return 1
}

# ---------------------------------------------------------------- 目标实现
# paths_<id>：输出本次会被删除/修剪的路径（用于计算占用）；以 "!" 开头的行是跳过说明。
# clean_<id>：执行清理（通过 run，dry-run 时只打印）。
# avail_<id>（可选）：返回非零表示目标不适用（工具未安装）。
# kib_<id>（可选）：覆盖默认的 du 统计。

# --- yay / paru
declare -A AUR_BASES=()
load_bases() {
  (( ${#AUR_BASES[@]} )) && return 0
  local b
  while IFS= read -r b; do AUR_BASES[$b]=1; done < <(
    grep -h -A1 -e '^%BASE%' -e '^%NAME%' /var/lib/pacman/local/*/desc 2>/dev/null | grep -v -e '^%' -e '^--$' || true)
}
aur_dirs() {
  local root
  for root in "$C/yay" "$C/paru/clone"; do
    [[ -d $root ]] && printf '%s\n' "$root"/*/
  done
}
avail_yay() { [[ -d $C/yay || -d $C/paru/clone ]]; }
paths_yay() {
  if pgrep -u "$UID" -x 'yay|paru|makepkg' >/dev/null; then echo "!yay/paru/makepkg 正在运行"; return; fi
  load_bases
  local d name
  while IFS= read -r d; do
    d=${d%/} name=${d##*/}
    if [[ -n ${AUR_BASES[$name]:-} && -d $d/.git ]]; then
      git -C "$d" ls-files -o --directory 2>/dev/null | sed "s|^|$d/|; s|/$||"
    else
      printf '%s\n' "$d"
    fi
  done < <(aur_dirs)
}
clean_yay() {
  if pgrep -u "$UID" -x 'yay|paru|makepkg' >/dev/null; then warn "yay/paru/makepkg 正在运行，跳过"; return; fi
  load_bases
  local d name
  while IFS= read -r d; do
    d=${d%/} name=${d##*/}
    if [[ -n ${AUR_BASES[$name]:-} && -d $d/.git ]]; then
      [[ -n $(git -C "$d" ls-files -o --directory 2>/dev/null) ]] && run git -C "$d" clean -ffdxq
    else
      info "  已卸载或非 git 克隆: $name"
      run rm -rf -- "$d"
    fi
  done < <(aur_dirs)
}

# --- Firefox
firefox_profile_busy() {
  local name=$1 base
  for base in "$HOME/.mozilla/firefox" "$CFG/mozilla/firefox"; do
    lock_alive "$base/$name/lock" && return 0
  done
  return 1
}
avail_firefox() { [[ -d $C/mozilla/firefox ]]; }
paths_firefox() {
  local p name
  for p in "$C"/mozilla/firefox/*/; do
    p=${p%/} name=${p##*/}
    if firefox_profile_busy "$name"; then echo "!Firefox 配置 $name 正在使用"; continue; fi
    printf '%s\n' "$p/cache2" "$p/startupCache" "$p/thumbnails"
  done
}
clean_firefox() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}，跳过" || rm_paths "$p"; done < <(paths_firefox); }

# --- Electron / Chromium（~/.config/<App>）
is_vscode_family() { local n; for n in "${VSCODE_FAMILY[@]}"; do [[ $1 == "$n" ]] && return 0; done; return 1; }
electron_busy() {
  local d=$1 name=${1##*/}
  [[ -e $d/SingletonLock || -L $d/SingletonLock ]] && { lock_alive "$d/SingletonLock"; return; }
  [[ -e $d/code.lock ]] && { lock_alive "$d/code.lock"; return; }
  proc_match "/${name}(/| |$)"
}
electron_app_dirs() {
  local d sub
  for d in "$CFG"/*/; do
    d=${d%/}
    is_vscode_family "${d##*/}" && { printf '%s\n' "$d"; continue; }
    for sub in "${ELECTRON_CACHE_DIRS[@]}"; do
      if [[ -d $d/$sub ]] || compgen -G "$d/Partitions/*/$sub" >/dev/null || compgen -G "$d/Default/$sub" >/dev/null; then
        printf '%s\n' "$d"; break
      fi
    done
  done
}
paths_electron() {
  local d name sub base
  while IFS= read -r d; do
    name=${d##*/}
    if electron_busy "$d"; then echo "!$name 正在运行"; continue; fi
    for base in "$d" "$d"/Default "$d"/Profile* "$d"/Partitions/*; do
      [[ -d $base ]] || continue
      for sub in "${ELECTRON_CACHE_DIRS[@]}"; do [[ -d $base/$sub ]] && printf '%s\n' "$base/$sub"; done
    done
    if is_vscode_family "$name"; then
      for sub in "${VSCODE_CACHE_DIRS[@]}"; do [[ -d $d/$sub ]] && printf '%s\n' "$d/$sub"; done
    fi
  done < <(electron_app_dirs)
}
clean_electron() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}，跳过" || rm_paths "$p"; done < <(paths_electron); }

# --- uv
avail_uv() { have uv; }
paths_uv() { uv cache dir 2>/dev/null || echo "$C/uv"; }
clean_uv() { if (( DEEP )); then run uv cache clean; else run uv cache prune; fi; }

# --- pip
avail_pip() { have pip; }
paths_pip() { pip cache dir 2>/dev/null || echo "$C/pip"; }
clean_pip() { run pip cache purge; }

# --- pnpm
avail_pnpm() { have pnpm; }
paths_pnpm() { pnpm store path 2>/dev/null || true; echo "$C/pnpm"; }
clean_pnpm() { run pnpm store prune; rm_paths "$C/pnpm"; }

# --- npm
avail_npm() { have npm; }
npm_cache_root() { npm config get cache 2>/dev/null || echo "$HOME/.npm"; }
paths_npm() {
  local r; r=$(npm_cache_root)
  printf '%s\n' "$r/_cacache" "$r/_logs"
  (( DEEP )) && echo "$r/_npx"
  return 0
}
clean_npm() {
  local r; r=$(npm_cache_root)
  run npm cache clean --force
  rm_paths "$r/_logs"
  (( DEEP )) && rm_paths "$r/_npx"
  return 0
}

# --- bun
avail_bun() { have bun; }
paths_bun() { echo "${BUN_INSTALL_CACHE_DIR:-${BUN_INSTALL:-$HOME/.bun}/install/cache}"; }
clean_bun() { run bun pm cache rm; }

# --- yarn（classic 与 berry 的全局缓存都直接删除目录，避免 berry 在项目外拒绝运行）
avail_yarn() { [[ -d $HOME/.yarn/berry/cache || -d $C/yarn ]]; }
paths_yarn() { printf '%s\n' "$HOME/.yarn/berry/cache" "$C/yarn"; }
clean_yarn() { rm_paths "$HOME/.yarn/berry/cache" "$C/yarn"; }

# --- go
avail_go() { have go; }
paths_go() { go env GOCACHE 2>/dev/null; (( DEEP )) && go env GOMODCACHE 2>/dev/null; return 0; }
clean_go() { run go clean -cache; (( DEEP )) && run go clean -modcache; return 0; }

# --- cargo
CARGO_DIR=${CARGO_HOME:-$HOME/.cargo}
avail_cargo() { [[ -d $CARGO_DIR/registry || -d $CARGO_DIR/git ]]; }
paths_cargo() {
  if pgrep -u "$UID" -x cargo >/dev/null; then echo "!cargo 正在运行"; return; fi
  printf '%s\n' "$CARGO_DIR/registry/src" "$CARGO_DIR/git/checkouts"
  (( DEEP )) && printf '%s\n' "$CARGO_DIR/registry/cache" "$CARGO_DIR/git/db"
  return 0
}
clean_cargo() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}，跳过" || rm_paths "$p"; done < <(paths_cargo); }

# --- NuGet
avail_nuget() { [[ -d ${NUGET_HTTP_CACHE_PATH:-$DATA/NuGet/http-cache} ]]; }
paths_nuget() { echo "${NUGET_HTTP_CACHE_PATH:-$DATA/NuGet/http-cache}"; }
clean_nuget() {
  if have dotnet; then run dotnet nuget locals http-cache --clear
  else rm_paths "$(paths_nuget)"; fi
}

# --- vcpkg（classic 模式：installed/ 不受影响）
vcpkg_root() {
  local r
  for r in "${VCPKG_ROOT:-}" "$HOME/vcpkg"; do
    [[ -n $r && -f $r/.vcpkg-root ]] && { printf '%s' "$r"; return 0; }
  done
  return 1
}
avail_vcpkg() { vcpkg_root >/dev/null; }
paths_vcpkg() {
  local r; r=$(vcpkg_root) || return 0
  if pgrep -u "$UID" -x vcpkg >/dev/null; then echo "!vcpkg 正在运行"; return; fi
  printf '%s\n' "$r/buildtrees" "$r/packages"
  (( DEEP )) && echo "$r/downloads"
  return 0
}
clean_vcpkg() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}，跳过" || rm_paths "$p"; done < <(paths_vcpkg); }

# --- pre-commit
avail_precommit() { have pre-commit && [[ -d ${PRE_COMMIT_HOME:-$C/pre-commit} ]]; }
paths_precommit() { echo "${PRE_COMMIT_HOME:-$C/pre-commit}"; }
# pre-commit 把未暂存的改动存为 patch* 后从不删除，其中可能含明文；只删 1 小时前的，避免影响正在运行的提交
clean_precommit() {
  local home=${PRE_COMMIT_HOME:-$C/pre-commit} p
  if (( DEEP )); then run pre-commit clean; return; fi
  run pre-commit gc
  while IFS= read -r p; do rm_paths "$p"; done \
    < <(find "$home" -maxdepth 1 -type f -name 'patch*' -mmin +60 2>/dev/null || true)
}

# --- 语言服务缓存
avail_lsp() { [[ -d $C/typescript || -d $C/jedi ]]; }
paths_lsp() {
  if pgrep -u "$UID" -f 'tsserver|typingsInstaller|jedi-language-server|pylsp' >/dev/null; then
    echo "!语言服务正在运行（关闭编辑器后再清理）"; return
  fi
  printf '%s\n' "$C/typescript" "$C/jedi"
}
clean_lsp() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}，跳过" || rm_paths "$p"; done < <(paths_lsp); }

# --- wine
avail_wine() { [[ -d $C/wine || -d $C/winetricks ]]; }
paths_wine() { printf '%s\n' "$C/wine" "$C/winetricks"; }
clean_wine() { rm_paths "$C/wine" "$C/winetricks"; }

# --- KDE（只碰可再生且非 mmap 的缓存；ksycoca / *.kcache 交给 Plasma 自己管理）
avail_kde() { [[ -d $C/drkonqi || -d $C/bookmarksrunner || -d $C/appstream ]]; }
paths_kde() {
  printf '%s\n' "$C/drkonqi/crashes" "$C/bookmarksrunner" "$C/kcrash-metadata"
  if pgrep -u "$UID" -x plasma-discover >/dev/null; then echo "!Discover 正在运行，保留 AppStream 缓存"
  else printf '%s\n' "$C/appstream" "$C/discover"; fi
}
clean_kde() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}" || rm_paths "$p"; done < <(paths_kde); }

# --- 缩略图
avail_thumbnails() { [[ -d $C/thumbnails ]]; }
paths_thumbnails() {
  if (( DEEP )); then echo "$C/thumbnails"
  else find "$C/thumbnails" -type f -atime +"$THUMBNAIL_DAYS" 2>/dev/null || true; fi
}
clean_thumbnails() {
  if (( DEEP )); then rm_paths "$C/thumbnails"; return; fi
  local n
  n=$( { find "$C/thumbnails" -type f -atime +"$THUMBNAIL_DAYS" 2>/dev/null || true; } | wc -l)
  (( n )) || return 0
  run find "$C/thumbnails" -type f -atime +"$THUMBNAIL_DAYS" -delete
  info "  （$n 个文件）"
}

# --- zsh 补全缓存
avail_zcompdump() { have zsh; }
paths_zcompdump() {
  local cur f
  cur=$(zsh --version 2>/dev/null | awk '{print $2}')
  [[ -n $cur ]] || return 0
  for f in "$HOME"/.zcompdump-*; do
    [[ $f == *"-$cur" || $f == *"-$cur.zwc" ]] || printf '%s\n' "$f"
  done
}
clean_zcompdump() { local f; while IFS= read -r f; do rm_paths "$f"; done < <(paths_zcompdump); }

# --- 着色器缓存
avail_shader() { [[ -d $C/mesa_shader_cache || -d $C/radv_builtin_shaders ]] || compgen -G "$C/qtshadercache-*" >/dev/null; }
paths_shader() { printf '%s\n' "$C/mesa_shader_cache" "$C/mesa_shader_cache_db" "$C/radv_builtin_shaders" "$C"/qtshadercache-* "$C/nvidia/GLCache"; }
clean_shader() { local p; while IFS= read -r p; do rm_paths "$p"; done < <(paths_shader); }

# --- Gradle
GRADLE_DIR=${GRADLE_USER_HOME:-$HOME/.gradle}
avail_gradle() { [[ -d $GRADLE_DIR/caches ]]; }
paths_gradle() { printf '%s\n' "$GRADLE_DIR/caches" "$GRADLE_DIR/daemon"; }
clean_gradle() {
  have gradle && run gradle --stop -q
  rm_paths "$GRADLE_DIR/caches" "$GRADLE_DIR/daemon"
}

# --- Maven
avail_maven() { [[ -d $HOME/.m2/repository ]]; }
paths_maven() {
  if pgrep -u "$UID" -f 'org.codehaus.plexus.classworlds' >/dev/null; then echo "!Maven 正在运行"; return; fi
  echo "$HOME/.m2/repository"
}
clean_maven() { local p; while IFS= read -r p; do [[ $p == '!'* ]] && warn "${p#!}，跳过" || rm_paths "$p"; done < <(paths_maven); }

# --- 回收站
avail_trash() { [[ -d $DATA/Trash ]]; }
paths_trash() { printf '%s\n' "$DATA/Trash/files" "$DATA/Trash/info" "$DATA/Trash/expunged"; }
clean_trash() {
  if have gio; then run gio trash --empty
  else rm_paths "$DATA"/Trash/files/* "$DATA"/Trash/files/.[!.]* "$DATA"/Trash/info/*; fi
}

# --- pacman（系统级，需要 sudo）
avail_pacman() { have paccache; }
paths_pacman() { echo /var/cache/pacman/pkg; }
clean_pacman() { run sudo paccache -rk"$PACCACHE_KEEP"; run sudo paccache -ruk0; }

# --- journald（系统级，需要 sudo）
avail_journal() { have journalctl; }
paths_journal() { echo /var/log/journal; }
kib_journal() {
  journalctl --disk-usage 2>/dev/null | grep -oE '[0-9.]+[KMGT]' | head -1 |
    numfmt --from=iec --to-unit=1024 2>/dev/null || echo 0
}
clean_journal() { run sudo journalctl --vacuum-size="$JOURNAL_MAX"; }

# --- Docker
avail_docker() { have docker && timeout 5 docker info >/dev/null 2>&1; }
paths_docker() { :; }
kib_docker() {
  docker system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null |
    awk -F'|' '$1=="Build Cache" || $1=="Images" {split($2,a," "); sub(/B$/,"",a[1]); print a[1]}' |
    numfmt --from=si --to-unit=1024 2>/dev/null | awk '{s+=$1} END {printf "%d\n", s}'
}
clean_docker() { run docker builder prune -f; run docker image prune -f; }

# ---------------------------------------------------------------- 目标调度

declare -A TIER=() DESC=()
ORDER=()
for t in "${TARGETS[@]}"; do
  IFS='|' read -r id tier desc <<<"$t"
  ORDER+=("$id"); TIER[$id]=$tier; DESC[$id]=$desc
done
unset t id tier desc

target_avail() { declare -F "avail_$1" >/dev/null || return 0; "avail_$1" 2>/dev/null; }

# 设置全局 T_KIB（KiB）与 T_SKIPS（跳过说明，换行分隔）
measure() {
  local id=$1 line paths=()
  T_KIB=0 T_SKIPS=''
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    if [[ $line == '!'* ]]; then T_SKIPS+="${line#!}; "; else paths+=("$line"); fi
  done < <("paths_$id" 2>/dev/null || true)
  if declare -F "kib_$id" >/dev/null; then T_KIB=$("kib_$id" || echo 0)
  else T_KIB=$(kib_of "${paths[@]}"); fi
  T_KIB=${T_KIB:-0}
}

# ---------------------------------------------------------------- 子命令

cmd_list() {
  printf '%-11s %-8s %s\n' 目标 层级 说明
  local id
  for id in "${ORDER[@]}"; do printf '%-11s %-8s %s\n' "$id" "${TIER[$id]}" "${DESC[$id]}"; done
}

scan_uncovered() {
  section "~/.cache 中未被任何目标覆盖的项（≥ ${UNCOVERED_MIN_MB}M）"
  local DEEP=1   # 以 --deep 的路径集合判断覆盖范围
  local -A covered=()
  local id p top kib name
  for id in "${ORDER[@]}"; do
    target_avail "$id" || continue
    while IFS= read -r p; do
      [[ $p == "$C"/* ]] || continue
      top=${p#"$C"/}; covered[${top%%/*}]=1
    done < <("paths_$id" 2>/dev/null | grep -v '^!' || true)
  done
  # 部分目标在跳过时不输出路径，按目录名补齐
  for top in yay paru mozilla typescript jedi thumbnails; do covered[$top]=1; done
  local any=0
  while IFS=$'\t' read -r kib p; do
    name=${p##*/}
    [[ -n ${covered[$name]:-} ]] && continue
    (( kib >= UNCOVERED_MIN_MB * 1024 )) || continue
    printf '  %-9s %s\n' "$(human "$kib")" "$(tilde "$p")"; any=1
  done < <(du -sk -- "$C"/* "$C"/.[!.]* 2>/dev/null | sort -rn)
  (( any )) || ok "无"
}

cmd_leftovers() {
  section "已卸载应用的残留目录（确认无用后手动删除）"
  local entry names paths p any=0 kib
  for entry in "${LEFTOVER_MAP[@]}"; do
    names=${entry%%|*} paths=${entry#*|}
    # shellcheck disable=SC2086
    pkg_or_cmd_present $names && continue
    for p in $paths; do
      [[ -e $HOME/$p ]] || continue
      kib=$(kib_of "$HOME/$p")
      printf '  %-9s ~/%-36s （%s 未安装）\n' "$(human "$kib")" "$p" "${names%% *}"; any=1
    done
  done
  (( any )) || ok "无"

  section "超过 $STALE_DAYS 天无写入的 Electron/Chromium 配置目录"
  local d newest now
  any=0 now=$(date +%s)
  while IFS= read -r d; do
    newest=$(find "$d" -maxdepth 2 -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
    newest=${newest%.*}
    [[ -n $newest ]] || continue
    (( now - newest > STALE_DAYS * 86400 )) || continue
    printf '  %-9s %-36s 最后写入 %s\n' "$(human "$(kib_of "$d")")" "$(tilde "$d")" "$(date -d "@$newest" +%F)"; any=1
  done < <(electron_app_dirs)
  (( any )) || ok "无"
}

cmd_scan() {
  section "空间概览"
  df -h --output=target,size,used,avail,pcent / "$HOME" 2>/dev/null | awk '!s[$0]++'
  info "~/.cache 总占用: $(human "$(kib_of "$C")")"

  section "清理目标（run 默认只执行 default 层；--deep 时的数字见 scan --deep）"
  printf '%-11s %-8s %-9s %-10s %s\n' 目标 层级 占用 状态 说明
  local id state total=0
  for id in "${ORDER[@]}"; do
    if ! target_avail "$id"; then
      printf '%s%-11s %-8s %-9s %-10s %s%s\n' "$C_D" "$id" "${TIER[$id]}" - 不适用 "${DESC[$id]}" "$C_0"
      continue
    fi
    measure "$id"
    state=可清理
    [[ -n $T_SKIPS ]] && state=有跳过
    (( T_KIB == 0 )) && [[ -z $T_SKIPS ]] && state=无数据
    [[ ${TIER[$id]} == default ]] && total=$((total + T_KIB))
    printf '%-11s %-8s %-9s %-10s %s\n' "$id" "${TIER[$id]}" "$(human "$T_KIB")" "$state" "${DESC[$id]}"
    [[ -n $T_SKIPS ]] && printf '%s%-31s ↳ %s%s\n' "$C_Y" '' "${T_SKIPS%; }" "$C_0"
  done
  info ""
  info "default 层合计占用约 $(human "$total")（uv/pnpm/pre-commit 等为修剪，实际释放可能更少）"
  if have paccache && target_avail pacman; then
    info "pacman: 每包保留 $PACCACHE_KEEP 版可省 $(paccache -dk"$PACCACHE_KEEP" 2>/dev/null | grep -oE 'saved: [^)]*' | cut -d' ' -f2- || echo 0)，已卸载包可省 $(paccache -duk0 2>/dev/null | grep -oE 'saved: [^)]*' | cut -d' ' -f2- || echo 0)"
  fi

  scan_uncovered
  cmd_leftovers

  section "仅报告"
  if have pacman; then
    local orphans
    orphans=$(pacman -Qdtq 2>/dev/null || true)
    info "孤立包: $(grep -c . <<<"$orphans") 个（其中 -debug 包 $(grep -c -- '-debug$' <<<"$orphans") 个）；检查 pacman -Qdt，移除 pacman -Qdtq | sudo pacman -Rns -"
  fi
  [[ -d /var/lib/systemd/coredump ]] && info "coredump: $(human "$(kib_of /var/lib/systemd/coredump)")（systemd-tmpfiles 按期清理；coredumpctl list 查看）"
  info "Electron 的 Service Worker / IndexedDB 属于离线数据，不在清理范围内"
  info ""
  info "下一步: run 预览 default 层；run --apply 执行；run pacman journal docker 预览系统级清理"
}

cmd_run() {
  local sel=() excl=() ids=() id s
  while (( $# )); do
    case $1 in
      --apply) DRY=0 ;;
      --deep) DEEP=1 ;;
      -x|--exclude) [[ -n ${2:-} ]] || die "-x 需要目标名"; excl+=("$2"); shift ;;
      -h|--help) usage; return 0 ;;
      -*) die "run: 未知选项 $1" ;;
      *) sel+=("$1") ;;
    esac
    shift
  done
  (( ${#sel[@]} )) || sel=(default)
  for s in "${sel[@]}"; do
    case $s in
      all) ids+=("${ORDER[@]}") ;;
      default|opt) for id in "${ORDER[@]}"; do [[ ${TIER[$id]} == "$s" ]] && ids+=("$id"); done ;;
      *) [[ -n ${TIER[$s]:-} ]] || die "未知目标: $s（用 list 查看）"; ids+=("$s") ;;
    esac
  done
  for s in "${excl[@]}"; do [[ -n ${TIER[$s]:-} ]] || die "未知目标: $s"; done

  (( DRY )) && info "${C_Y}dry-run：以下命令不会执行，加 --apply 实际清理${C_0}"
  (( DEEP )) && info "${C_Y}--deep：uv/npm/go/cargo/vcpkg/pre-commit/thumbnails 使用更彻底的清理方式${C_0}"
  local -A done_=()
  local before after freed=0
  for id in "${ids[@]}"; do
    [[ -n ${done_[$id]:-} ]] && continue
    done_[$id]=1
    for s in "${excl[@]}"; do [[ $s == "$id" ]] && continue 2; done
    section "$id"
    if ! target_avail "$id"; then info "  不适用（未安装或无数据）"; continue; fi
    (( DRY )) || { measure "$id"; before=$T_KIB; }
    "clean_$id" || true   # 单条命令的失败由 run 记录
    if (( !DRY )); then
      measure "$id"; after=$T_KIB
      (( before > after )) && freed=$((freed + before - after))
    fi
  done
  if (( DRY )); then
    info ""; info "以上为预览。加 --apply 执行。"
  else
    info ""; ok "完成，估算释放 $(human "$freed")"
  fi
  (( FAILED )) && { err "部分命令失败，见上方警告"; return 1; }
  return 0
}

usage() { sed -n '2,/^$/{s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"; }

main() {
  [[ $(uname -s) == Linux ]] || die "仅支持 Linux"
  local cmd=${1:-}
  (( $# )) && shift
  case $cmd in
    scan) [[ ${1:-} == --deep ]] && DEEP=1; cmd_scan ;;
    list) cmd_list ;;
    run) cmd_run "$@" ;;
    leftovers) cmd_leftovers ;;
    -h|--help|help|'') usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"

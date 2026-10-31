# 参考手册

`dotfiles` 各命令的行为、规则与可调整项。快速上手见根目录 [README_zh-CN.md](../README_zh-CN.md)。
英文版：[reference.md](reference.md)。

所有命令都通过 uv 运行：`uv run dotfiles <子命令>`（在仓库内），或 `uv run --project ~/.local/share/chezmoi --frozen --no-dev dotfiles <子命令>`（在任意目录）。工具只依赖 Python 标准库，`--no-dev` 时不需要访问 PyPI。

约定：

- 修改性操作默认只预览：`restore`、`clean run`、`audit`、`system-restore` 需要 `--apply`；`backup` 直接写入仓库（`-n` 预览），但从不推送。

- 退出码：`0` 成功，`1` 失败或检查未通过，`2` 用法错误（未知目标、未知步骤等）。

- 输出到终端时带颜色；设置 `NO_COLOR` 或重定向时不带。

---

## 1. 环境检测：doctor

只读。依次检测：

| 分组 | 内容 |
|---|---|
| 系统 | 发行版与内核 / Windows 版本，桌面会话（Wayland/X11），架构，GPU 与驱动，Python |
| 包管理器 | Linux：pacman、yay、paru、nix、flatpak；Windows：winget、scoop、choco |
| 工具 | 必需：git、chezmoi、uv；推荐：gh、pre-commit、ssh-keygen、just，以及 sudo、zsh（Linux）或 pwsh（Windows）。age 可选（chezmoi 内置 age） |
| 网络 | git 的 `http.proxy` 是否可连接，代理环境变量 |
| chezmoi | 源目录是否就是本仓库，配置文件是否与模板生成的结果一致，加密方式，私钥是否存在、是否与仓库公钥匹配、权限，待部署的变更 |
| 仓库 | 分支、与上游的差距、未提交改动、hooks 是否安装、推送地址 |
| 软件包 | 清单中缺少的软件包数量；Windows 上还会提示需要手动安装的程序数量 |

每项为 ok / warn / fail / info；fail 表示缺少必需条件，退出码为 1。`--json` 输出结构化结果，便于脚本使用。

---

## 2. 重装后恢复：restore

```
dotfiles restore [--apply] [--only 步骤,…] [--skip 步骤,…] [--key-file 路径] [-y/--yes]
```

### 步骤

| 步骤 | 平台 | 检测 | 执行 |
|---|---|---|---|
| `key` | 两端 | 模板将选用的私钥路径上有文件，且推导出的公钥与仓库模板中的 recipient 一致 | 从 `--key-file` 或交互输入（文件路径，或直接粘贴私钥行，输入不回显）读取，写入后再次校验；不匹配则删除写入的文件 |
| `config` | 两端 | chezmoi 源目录是本仓库；`~/.config/chezmoi/chezmoi.toml` 与模板渲染结果一致 | 备份原配置后运行 `chezmoi init` |
| `dotfiles` | 两端 | `chezmoi status`（不含 externals）是否还有待部署项 | 完整预演 → 备份将被覆盖的已有文件 → `chezmoi apply --force` |
| `system` | Linux | `system/etc` 是否存在 | 见 [3.6](#36-linux-etc) 的恢复规则（需要确认） |
| `packages` | 两端 | 清单中缺少的软件包 | Linux：官方/已配置仓库的包用 `sudo pacman -S --needed`，其余交给 paru/yay，没有 AUR 助手时列出；Windows：`winget import`、`scoop import`（需要确认） |
| `externals` | 两端 | externals 的目标目录是否存在 | `chezmoi apply --include externals`；git 代理连不上时临时直连 |
| `ssh` | 两端 | `~/.ssh/id_ed25519` 与 `~/.ssh/config` 中 `IdentityFile` 引用的私钥是否存在 | `ssh-keygen` 生成缺失的密钥（交互设置口令）；`gh` 已登录时可把 GitHub 用的公钥登记上去 |
| `shell` | Linux | 登录 shell 是否为 zsh | `chsh -s`（需要确认） |
| `hooks` | 两端 | pre-commit / pre-push hook 是否安装；GitHub HTTPS 远程的推送地址是否为 SSH | 必要时 `uv tool install pre-commit`，`pre-commit install`，`git remote set-url --push origin git@github.com:…` |
| `verify` | 两端 | 总是执行 | `chezmoi verify`、权限审计、`check` |

### 执行语义

- 不带 `--apply` 时只做检测并列出计划。后面的步骤依赖前面的结果（例如没有私钥时 `dotfiles` 显示"阻塞"），这是正常的。

- `--apply` 时按顺序处理：检测为"已完成"的跳过；"阻塞"或执行失败时立即停止；执行后再检测一次，确认确实完成。

- 不记录状态文件：所有判断都基于当前系统状态，因此重跑总是安全的，可以从中断处继续。

- 需要确认的步骤在非交互终端中会被跳过，除非加 `--yes`。

### 私钥位置

`home/.chezmoi.toml.tmpl` 按以下顺序选取私钥路径，取最后一个存在的：

1. `~/.config/chezmoi/key.txt`（默认；新机器上 `restore` 写到这里）

2. `~/secret/chezmoi_age_key.txt`（旧 Windows 位置）

3. `~/vg101/secret/chezmoi_age_key.txt`（旧 Linux 位置）

已有机器因此不需要搬动私钥。私钥写入时：Linux 权限 600（新建的父目录 700）；Windows 去掉继承，只允许当前用户、SYSTEM、Administrators 访问。

### 网络与代理

- bootstrap 通过 HTTPS 克隆公开仓库，此时不需要 SSH 密钥；externals 也使用 HTTPS。推送地址在 `hooks` 步骤改为 SSH。

- 部署后的 `~/.gitconfig` 设置了 `http.proxy = 127.0.0.1:7897`。新机器上代理软件尚未安装时，`externals` 步骤检测到代理不可连接会临时用 `GIT_CONFIG_*` 环境变量把它覆盖为空（直连），不修改任何文件。

- 如果直连也无法访问 GitHub，先在终端中设置 `HTTPS_PROXY` 再运行 bootstrap；uv 下载 Python 时同样遵循该变量。

---

## 3. 备份：status / backup / check / audit

### 3.1 backup 流程

1. **预检**：已启用 age 加密、配置了 recipient、私钥存在且不在仓库内；Windows 上检查"文档"文件夹是否被重定向（例如到 OneDrive）。

2. **re-add 已托管文件**：明文托管的文件如果出现了密钥特征，先收紧为 600 并改为加密托管，再对全部托管文件执行 `chezmoi re-add`。源文件带 `private_`、但本地权限比 600 宽松的文件（例如被某个工具重写成了 644）会先收紧为 600——`re-add` 会把本地权限写回源状态，否则会去掉 `private_`。使用 `-n` 时，chezmoi 的输出会被捕获（不会打开 pager），`.age` 文件的 diff 每个只显示一行，因为密文本身不可读。

3. **模板目标的漂移**：`re-add` 不处理模板（如 `.gitconfig.tmpl`）。有本地修改的模板目标会被列出，需要手动合并回 `.tmpl`（[6.3](#63-需要手动合并的场景)）。

4. **添加候选**：把本平台候选清单中尚未托管的文件加入 chezmoi，并登记到 `platforms.toml` 的当前平台。

5. **/etc**（Linux，`--no-system` 时跳过）：见 [3.6](#36-linux-etc)。

6. **软件包清单**：Linux 写入 `packages/archpkg.txt`（`pacman -Qqe`）；Windows 写入 `scoopfile.json`、`winget.json`（归一化后只在软件包增减时变化）与 `winget-unavailable.txt`。

7. **安全检查**：同 `check`；不通过则返回 1，不提交。

8. **提交**：仅在 `--commit` 时执行，不推送。

### 3.2 候选的处理规则

对候选清单中的每个文件，按顺序判断：

| 条件 | 处理 |
|---|---|
| 已托管 | 跳过（由 re-add 维护） |
| 目标归属另一平台 | 跳过并提示：两端都需要时改为模板并登记到 `shared` |
| 内容含当前家目录的绝对路径 | 跳过并提示改写为模板（`{{ .chezmoi.homeDir }}`） |
| 在"一律加密"清单中，或内容匹配密钥特征 | Linux 上先收紧为 600，再 `chezmoi add --encrypt` |
| `chezmoi add --secrets error` 被 chezmoi 内置扫描拦下 | 同上，改为加密 |
| 其余 | 明文 `chezmoi add` |

候选清单在 `src/dotfiles/backup.py` 的 `LINUX` / `WINDOWS` 中：`track`（推荐托管，目录展开为其中小于1 MiB 的普通文件）、`secret`（一律加密）、`exclude`（展开目录时跳过的文件名模式）。

### 3.3 平台归属

`home/.chezmoidata/platforms.toml` 为每个托管目标声明归属：`shared`、`linux` 或 `windows`。条目是相对 `~` 的路径，目录条目覆盖其下所有文件。

- `.chezmoiignore` 是一个模板：忽略其他平台的条目，以及这些条目中只属于该平台的父目录。例如 Linux 上会忽略 `Documents`（否则 Windows 的 `readonly_Documents/` 会把 `~/Documents` 改成只读），但不会忽略 `.config`（两端都有目标在其中）。

- 在一个平台上对另一平台的目标执行 `chezmoi add` 时，chezmoi 只会警告 "ignoring"，不会改写源文件。

- `check` 要求两个平台视角下的每个托管目标都恰好被一个列表覆盖。

手动新增一个只在 Linux 部署的文件：

```bash
chezmoi add ~/.config/foo/bar.conf                 # 或 add --encrypt
# 然后在 platforms.toml 的 linux 列表中加入 ".config/foo"（或具体文件）
uv run dotfiles check
```

两端都需要但内容不同的文件：改为模板（例如 `dot_gitconfig.tmpl` 中用 `{{ if eq .chezmoi.os "windows" }}` 区分），登记到 `shared`。

### 3.4 加密与模板

- 加密文件的源文件名形如 `encrypted_private_dot_zshrc.age`。`private_` 使部署后的权限为 600；备份时会自动处理。

- 加密文件也可以是模板，但 `re-add` 不处理模板；需要两端共享的密文，可以在模板中用 `{{ include "…" | decrypt }}` 引用（目前未使用）。

### 3.5 check

1. chezmoi 配置的私钥没有被 git 跟踪；

2. `guard tree`：对全部已跟踪及未忽略的新文件做提交检查中的同一套检查，见 [5](#5-提交检查pre-commit)；

3. 平台归属完整。

### 3.6 Linux /etc

`/etc` 使用一个独立的 chezmoi 实例：源目录是仓库根目录的 `system/`，目标是 `$XDG_RUNTIME_DIR` 下的临时暂存区（tmpfs，用完即删）。root 才能读的文件通过 `sudo cat` 读入暂存区，因此需要在交互终端中运行；没有终端时这些文件会被跳过并给出警告。

收集的目标（`src/dotfiles/system_etc.py`）：

- pacman 标记为 `[modified]` 的软件包配置文件；

- `ETC_EXTRA` 中的路径（目录中只取不属于任何软件包的文件，例如自建的 systemd 单元、pacman hook，以及 `/etc/profile.d`、`/etc/ssh/sshd_config.d` 这类 drop-in 目录中你自己的文件）。本地设置尽量写成 drop-in 文件，而不是直接改软件包的配置文件：软件包文件保持原样，升级时就不会产生需要合并的 `.pacnew`；

- `ETC_SECRET` 中的路径，一律加密；非所有人可读的文件以及匹配密钥特征的文件同样加密；

- 排除 `ETC_EXCLUDE`：账户数据库、`machine-id`、SSH 主机密钥、`*.pacnew`、`*.bak` 等。

恢复（`system-restore` 与 restore 的 `system` 步骤）：

- 解密到暂存区后逐个与 `/etc` 比较，显示差异（加密文件只提示有差异，不显示内容）。

- `HARDWARE_BOUND` 中的文件（`fstab`、`crypttab`、`default/grub`、`mkinitcpio.conf`、`mkinitcpio.d/*`、`X11/xorg.conf.d/*`、`modprobe.d/*`）只显示差异，必须在命令行中显式指定路径才会安装：新系统的磁盘 UUID、加密布局和硬件通常不同。

- `sudoers` 与 `sudoers.d/*` 安装前用 `visudo -cf` 校验，失败则不安装。

- 安装时保持目标现有的权限；目标不存在时使用备份中的权限，属主 `root:root`。修改了引导或 initramfs 相关文件后，记得运行 `grub-mkconfig -o /boot/grub/grub.cfg` 或 `mkinitcpio -P`。

### 3.7 audit

检查一律加密清单中的文件、`.pypirc`、`.netrc`、`.git-credentials`、`~/.ssh`（及其中的私钥）、`~/.gnupg`、私钥文件及其目录。Linux 要求文件 600、目录 700；Windows 要求 ACL 中只有当前用户、SYSTEM、Administrators。`--apply` 时收紧。

---

## 4. 缓存清理：clean

```
dotfiles clean scan [--deep]
dotfiles clean list
dotfiles clean run [--apply] [--deep] [-x 目标]… [目标|default|opt|all]…
dotfiles clean leftovers
```

- `scan`：各目标的占用与状态（可清理 / 有跳过 / 无数据 / 不适用 / 未统计），缓存根目录中未被任何目标覆盖的大目录，残留目录报告，以及只报告不清理的项目（孤立包、coredump、WSL 虚拟磁盘等）。

- `run`：不指定目标时等同 `default`；`opt` 层必须点名或用 `opt` / `all`。`-x` 排除目标，可重复或用逗号分隔。

- `leftovers`：已卸载应用的残留目录与久未使用的 Electron 配置目录。只报告，从不删除。

### 4.1 安全措施

- 所有删除都经过范围断言：路径必须是绝对路径，位于允许的根目录之下（Linux：家目录及显式设置的工具目录；Windows：家目录、scoop 目录、Windows Update 下载目录），且不能是受保护的目录本身（家目录、各 XDG/AppData 根目录、Documents、Desktop、Downloads、`.ssh`、本仓库等）。断言失败会终止整个运行。

- 删除不跟随符号链接与 Windows 重解析点，只删除链接本身。

- 应用运行时跳过其缓存：Chromium/Electron 的 `SingletonLock`（Linux，检查其中的 PID 是否存活）或 `lockfile`（Windows，检查是否被独占打开）、VS Code 的 `code.lock`、Firefox 的 `lock` / `parent.lock`，没有锁文件时按进程名判断。

- 优先使用工具自带的清理命令（`uv cache prune`、`npm cache clean`、`pnpm store prune` 等），没有命令时才删除目录。

- 不碰离线数据：Electron/浏览器的 Service Worker、IndexedDB、Local Storage；已安装的工具（uv tools、rustup、VS Code 扩展）；KDE 的 ksycoca 与 `*.kcache`。

### 4.2 两端共用的目标

| 目标 | 层级 | 动作 | `--deep` |
|---|---|---|---|
| `uv` | default | `uv cache prune` | `uv cache clean` |
| `pip` | default | `pip cache purge` | |
| `pnpm` | default | `pnpm store prune` + 元数据缓存 | |
| `npm` | default | `npm cache clean --force` + `_logs` | 额外删除 `_npx` |
| `bun` | default | `bun pm cache rm` | |
| `yarn` | default | 删除 classic 与 berry 的全局缓存目录 | |
| `go` | default | `go clean -cache`；Go 已卸载时删除缓存目录 | 额外 `go clean -modcache` |
| `cargo` | default | `$CARGO_HOME/registry/src`、`git/checkouts`（cargo 运行时跳过） | 额外 `registry/cache`、`git/db` |
| `nuget` | default | `dotnet nuget locals http-cache --clear` | |
| `vcpkg` | default | `$VCPKG_ROOT/buildtrees`、`packages` | 额外 `downloads` |
| `precommit` | default | `pre-commit gc` + 删除 1 小时前的 stash 补丁 | `pre-commit clean` |
| `gradle` | opt | `gradle --stop` 后删除 `caches`、`daemon` | |
| `maven` | opt | `~/.m2/repository`（Maven 运行时跳过） | |
| `docker` | opt | `docker builder prune -f` + `docker image prune -f`（scan 中的占用为可回收总量） | |

### 4.3 Linux 目标

| 目标 | 层级 | 动作 |
|---|---|---|
| `yay` | default | `~/.cache/yay`、`~/.cache/paru/clone`：已安装包的克隆 `git clean -ffdx`（保留 PKGBUILD），已卸载包的克隆整体删除（yay/paru/makepkg 运行时跳过） |
| `firefox` | default | 各配置文件的 `cache2`、`startupCache`、`thumbnails` |
| `electron` | default | `~/.config/<App>` 下的 Cache、Code Cache、GPUCache 等；VS Code 系还有 CachedData、CachedExtensionVSIXs、logs |
| `lsp` | default | `~/.cache/typescript`、`~/.cache/jedi`（语言服务运行时跳过） |
| `wine` | default | wine-mono/gecko 安装包与 winetricks 缓存 |
| `kde` | default | DrKonqi 崩溃报告、KRunner 书签副本、AppStream/Discover 缓存（Discover 运行时保留） |
| `thumbnails` | default | 超过 `THUMBNAIL_DAYS` 天未访问的缩略图（`--deep`：全部） |
| `zcompdump` | default | 旧 zsh 版本遗留的 `~/.zcompdump-*` |
| `shader` | opt | Mesa/RADV/Qt 着色器缓存 |
| `trash` | opt | 清空回收站 |
| `pacman` | opt | `sudo paccache -rk$PACCACHE_KEEP` 与 `-ruk0` |
| `journal` | opt | `sudo journalctl --vacuum-size=$JOURNAL_MAX` |

### 4.4 Windows 目标

| 目标 | 层级 | 动作 |
|---|---|---|
| `temp` | default | `%TEMP%` 中超过 `TEMP_DAYS` 天未修改的顶层条目（目录中有新文件则保留；被占用的文件跳过） |
| `scoop` | default | `scoop cleanup -a -k`（`--deep` 额外 `scoop cache rm -a`） |
| `electron` | default | `%APPDATA%\<App>` 下的 Chromium 缓存；VS Code 系（含 Cursor、Windsurf、Kiro、Trae）还有 CachedData 等 |
| `browser` | default | Edge / Chrome / Brave 各配置文件的缓存与着色器缓存 |
| `firefox` | default | `%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*` 的缓存 |
| `updaters` | default | `%LOCALAPPDATA%\*-updater` 中 electron-updater 下载的安装包 |
| `crashdumps` | opt | `CrashDumps` 与 WER 报告 |
| `cloudmusic` | opt | 网易云音乐播放缓存 |
| `shader` | opt | DirectX / NVIDIA / AMD / Intel 着色器缓存 |
| `nugetpkgs` | opt | `dotnet nuget locals global-packages --clear` |
| `recycle` | opt | `Clear-RecycleBin -Force` |
| `wu` | opt | 停止 wuauserv、bits 后清空 `SoftwareDistribution\Download`（需要管理员） |
| `dism` | opt | `DISM /Online /Cleanup-Image /StartComponentCleanup`（需要管理员） |

### 4.5 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `THUMBNAIL_DAYS` | 90 | Linux `thumbnails` 的天数阈值 |
| `TEMP_DAYS` | 7 | Windows `temp` 的天数阈值 |
| `STALE_DAYS` | 180 | `leftovers` 判定久未使用的天数 |
| `JOURNAL_MAX` | 1G | `journal` 清理后保留的大小 |
| `PACCACHE_KEEP` | 1 | `pacman` 每个包保留的版本数 |
| `UNCOVERED_MIN_MB` | 10（Linux）/ 100（Windows） | `scan` 列出未覆盖项的最小体积 |

同时遵循各工具自身的位置变量：`XDG_*`、`CARGO_HOME`、`VCPKG_ROOT`、`GRADLE_USER_HOME`、
`PRE_COMMIT_HOME`、`BUN_INSTALL`、`NUGET_HTTP_CACHE_PATH`、`NUGET_PACKAGES`、`SCOOP` 等。

### 4.6 定期运行（Linux，可选）

`~/.config/systemd/user/cache-clean.service`：

```ini
[Unit]
Description=Weekly cache cleanup

[Service]
Type=oneshot
ExecStart=uv run --project %h/.local/share/chezmoi --frozen --no-dev dotfiles clean run --apply
```

`~/.config/systemd/user/cache-clean.timer`：

```ini
[Unit]
Description=Weekly cache cleanup

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=1h

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now cache-clean.timer
```

default 层不需要 sudo，运行中的应用会被自动跳过。如果 uv 不在 systemd 用户会话的 PATH 中，把 `uv` 改为其绝对路径。

---

## 5. 提交检查（pre-commit）

仓库使用 [pre-commit](https://pre-commit.com) 在 `git commit` 与 `git push` 时自动检查，防止明文密钥进入仓库。只依赖 Python 与 git，两端使用同一份配置。restore 的 `hooks` 步骤会自动安装；手动安装：

```bash
uv tool install pre-commit        # 或 pacman -S pre-commit
pre-commit install                # 同时安装 pre-commit 与 pre-push
pre-commit install-hooks          # 可选：预先构建环境（首次会下载 Go 并编译 gitleaks）
```

`.git/hooks/*` 中写有本机 Python 的路径，每台机器各自生成，不要复制或提交。首次构建 gitleaks 需要访问 Go 模块代理；直连失败时临时设置 `HTTPS_PROXY`。

### 5.1 涉及的文件

| 文件 | 作用 |
|---|---|
| `.pre-commit-config.yaml` | hook 列表，远程 hook 固定到具体版本 |
| `.gitleaks.toml` | 沿用 gitleaks 内置规则，补充 WakaTime 规则，跳过 `encrypted_*.age` 密文 |
| `.gitattributes` | 统一 LF 换行（不受 Windows `core.autocrlf` 影响）；`*.age`、`*.ptb` 不做转换；`*.bat`、`*.cmd` 用 CRLF |
| `src/dotfiles/guard.py` | 本仓库专用规则；既是 Python 包的模块，也由 pre-commit 直接执行，因此只用标准库 |

### 5.2 检查内容

`git commit` 时（只检查暂存区）：

| hook | 检查内容 |
|---|---|
| `check-illegal-windows-names`、`check-case-conflict` | Windows 上无法检出的路径 |
| `check-added-large-files` | 新增文件大于 1 MiB |
| `check-merge-conflict`、`check-yaml`、`check-toml` | 冲突标记与语法错误（`.tmpl` 不检查） |
| `gitleaks` | 200 多种通用密钥规则，输出中的密钥值显示为 `REDACTED` |
| `prune-precommit-stash` | 删除 1 小时前遗留的 stash 补丁，并把缓存目录权限收紧为 700（从不阻止提交） |
| `dotfiles-guard` | 见下表 |

`git push` 时，`dotfiles-guard-push` 检查**每一个**即将推送的提交中新增或修改的文件，因此能拦下用
`--no-verify` 跳过检查的提交，即使后续提交删掉了密钥也一样。

`dotfiles-guard` 的规则（`staged`、`push`、`tree` 三种模式相同）。部署目标按 chezmoi 规则换算：
`.chezmoiroot` 指定的 `home/` 下是 `~` 的目标，`system/` 下是系统路径，其余文件（工具代码、软件包清单）不是部署目标，但同样做密钥扫描。

| 规则 | 级别 | 说明 |
|---|---|---|
| `age-not-ciphertext` / `age-armor-truncated` | 错误 | `*.age` 文件不是完整的 age 密文 |
| `age-without-encrypted-prefix` / `encrypted-prefix-without-age` | 错误 | `encrypted_` 前缀与 `.age` 后缀没有成对出现 |
| `must-encrypt(<目标>)` | 错误 | 目标属于必须加密的类别却没有加密（`ENCRYPT_REQUIRED` / `ENCRYPT_EXEMPT`） |
| 密钥规则名（如 `github-token`） | 错误 | 明文匹配 `SECRET_RULES`；`backup` 判断是否加密时用的是同一套规则 |
| `absolute-home-path` | 警告 | 含本机的家目录（字面路径，或同一用户的 `C:\Users\<用户>`、JSON 转义形式、`/c/Users/<用户>`），在另一台机器上无效。`/home/username` 这类通用示例不会报告 |

`ENCRYPT_REQUIRED` 覆盖：`.ssh/*`（`*.pub`、`known_hosts*` 除外）、`.gnupg/*`、`.pypirc`、`.npmrc`、`.netrc`/`_netrc`、`.git-credentials`、`.docker/config.json`、gh 的 `hosts.yml`、`.wakatime.cfg`、`.aws/credentials`、`.kube/config`、rclone 配置、`*.pem`/`*.key`/`*.p12`/`*.pfx`、`id_rsa`/`id_ecdsa`/`id_ed25519`、`.env*`（`.env.example` 除外），以及 `system/` 下的 `/etc/shadow*`、`/etc/sudoers*`、`/etc/crypttab`、NetworkManager 连接、WireGuard、SSH 主机密钥、mihomo 配置。

### 5.3 被拦下时

输出只包含 `文件:行号` 和规则名。

| 情况 | 处理 |
|---|---|
| 敏感文件被明文添加了 | `git restore --staged <源文件>`，`chezmoi forget <目标>`，再 `chezmoi add --encrypt <目标>` |
| 明文配置里混进了 token | 把 token 移到加密文件或环境变量中，或把整个文件改为加密托管 |
| 误报 | 在该行末尾加 `gitleaks:allow`（两者都认），或调整 `guard.py` / `.gitleaks.toml` |
| 用 `--no-verify` 提交但未推送 | pre-push 会拦下；`git reset --soft <上一个干净的提交>` 后重新整理 |
| 已经推送 | 立即**轮换该凭据**；改写 git 历史无法撤回已公开的内容 |

临时跳过某个 hook：Linux `SKIP=gitleaks git commit …`；Windows
`$env:SKIP = "gitleaks"; git commit …; Remove-Item Env:SKIP`。

### 5.4 stash 补丁

pre-commit 运行 hook 前会把**未暂存**的改动保存为 `$PRE_COMMIT_HOME/patch*`，而且从不删除；如果当时工作区里有尚未加密的敏感内容，它会以明文留在补丁中。`prune-precommit-stash` 每次提交时删除 1 小时前的补丁（最近的补丁要留给正在进行的提交恢复工作区），`clean run precommit` 也会删除它们。急需清除时（确认没有正在进行的提交）：

```bash
uv run dotfiles guard prune-stash --max-age-minutes 0
```

---

## 6. 兼容性问题

本仓库直接克隆到 `~/.local/share/chezmoi`，即 chezmoi 的默认源目录，因此检出目录就是工作目录.

`chezmoi init`、`apply`、`update` 按默认设置工作，日常同步就是 `git pull` + `chezmoi apply`。

由于 `.chezmoiroot` 指定 `home/` 为源状态，仓库顶层的工具文件（`src/`、`tests/`、`Justfile` 等）永远不会被部署进 `~`。新机器也直接克隆到该路径（见 `bootstrap.sh`）；克隆到独立目录再从那里 apply 会留下两份会漂移的副本。

### 6.1 传统 chezmoi 命令

所有传统命令透明可用；chezmoi 通过 `.chezmoiroot` 解析真实源路径：

| 命令 | 行为 |
|---|---|
| `chezmoi edit <目标>` | 打开 `home/` 下的源文件；加密文件自动解密编辑、保存时重新加密 |
| `chezmoi edit --apply <目标>` | 编辑并一步部署 |
| `chezmoi re-add` | 重新导入改动的明文目标、重新加密改动的加密目标（`dotfiles backup` 内部就是包的它） |
| `chezmoi add <文件>` | 照常工作，但新目标必须登记到 `platforms.toml`（[3.3](#33-平台归属)）；`dotfiles check` 会强制校验 |
| `chezmoi managed / diff / verify / forget` | 不受影响；`chezmoi source-path` 解析为 `~/.local/share/chezmoi/home` |

两点注意：

- `re-add` 不处理模板（`.tmpl`）；模板目标漂移时 `backup` 会列出，需要手动合并回 `.tmpl`——具体步骤见 [6.3](#63-需要手动合并的场景)。

- 不要直接手改 `.age` 源文件——一律通过 `chezmoi edit`（[6.3](#63-需要手动合并的场景)）。

### 6.2 拉取提交后的重新同步

拉取（例如 `dev` 合并进 `main` 之后）不需要任何重新初始化——源目录没有变化。把真实系统同步到新的源状态：

1. `git fetch && git pull`（或切换到 `main` 跟随共享分支）。

2. `dotfiles backup -n`：检查漂移——上次 apply 之后本地改过的文件（apply 会覆盖它们）。要保留就用 `dotfiles backup --commit` 收回仓库，或直接改源文件。

3. `dotfiles restore --apply --only dotfiles`：部署源状态；将被覆盖的文件先备份到 `~/.local/state/dotfiles/pre-apply-*`。

4. 需要收紧权限时 `dotfiles audit --apply`，最后用 `dotfiles doctor` 与 `dotfiles check` 确认。

第 2 步中"保留改动"还是"让 apply 覆盖"要逐个文件决定。下节给出文档中所有"手动合并"处的具体操作。

### 6.3 需要手动合并的场景

#### 明文目标漂移

本地文件改过，想保留改动：

```bash
chezmoi diff ~/.config/foo/bar.conf   # 查看 apply 会改什么
chezmoi add ~/.config/foo/bar.conf    # 把本地内容收回源状态
uv run dotfiles check                 # 平台归属必须保持完整
```

`dotfiles backup` 会一次性对所有漂移的明文目标执行收回操作；先用 `backup -n` 预览会收回哪些内容。如果以源状态为准，则什么都不用做——apply 会覆盖本地文件（`restore` 会先把它备份到 `~/.local/state/dotfiles/pre-apply-*`）。

#### 模板目标漂移

例如 `~/.gitconfig`，由 `home/dot_gitconfig.tmpl` 渲染而来。`re-add` 与 `backup` 都跳过模板，本地改动必须手动并入 `.tmpl`。先从两侧看差异：

```bash
chezmoi diff ~/.gitconfig                        # 上次部署的状态 vs 本地文件
chezmoi cat ~/.gitconfig | diff - ~/.gitconfig   # 当前模板渲染结果 vs 本地文件
```

逐块审视差异，把需要保留的本地改动抄进 `home/dot_gitconfig.tmpl`——与平台相关的改动用模板逻辑区分（参见 [3.3](#33-平台归属)）。然后部署并确认合并完整：

```bash
chezmoi apply ~/.gitconfig
chezmoi cat ~/.gitconfig | diff - ~/.gitconfig   # 无输出 = 恢复同步
```

#### 加密目标漂移

以 `~/.ssh/config`为例：

```bash
chezmoi edit ~/.ssh/config            # 解密到临时文件、打开 $EDITOR、保存时重新加密
# 或者以本地文件为准时：
chezmoi add --encrypt ~/.ssh/config   # 重新导入；encrypted_ 前缀保持不变
```

不要直接编辑 `.age` 源文件；`encrypted_` 前缀与 `.age` 后缀不成对时 `dotfiles check` 会报错（[5.2](#52-检查内容)）。

#### 新增文件

另见 [3.3](#33-平台归属)：

```bash
chezmoi add ~/.config/foo/bar.conf         # 敏感内容用 `chezmoi add --encrypt`
# 然后在 platforms.toml 的 linux / windows / shared 中登记 ".config/foo"（或具体文件）
uv run dotfiles check
```

---

## 7. 开发与测试

```bash
uv run pytest                     # 或 just test
uvx ruff check src tests
```

测试不修改真实系统：Windows 清理目标的文件系统逻辑通过把 `%LOCALAPPDATA%` 等指向临时目录在任何平台上运行；平台归属测试用 `chezmoi --override-data '{"chezmoi":{"os":"windows"}}'` 渲染另一平台的视角。

在 Linux 上查看 Windows 视角下会部署什么：

```bash
chezmoi --source ~/.local/share/chezmoi --override-data '{"chezmoi":{"os":"windows"}}' managed
```

## 8. 已知限制

- **Windows 上尚未实际运行过 Python 版本**：Windows 的清理目标、备份（scoop/winget 导出、ACL 审计）、恢复流程与 `bootstrap.ps1` 是从原 PowerShell 脚本移植或新写的，只在 Linux 上做过文件系统层面的测试与语法解析。原脚本可在提交 `a15c9b5` 中找到（`git show a15c9b5:scripts/clean.ps1`）。首次在 Windows 上使用时，先运行`doctor`、`clean scan`、`status`、`restore`（不加 `--apply`）确认结果。

- **scoop 不会被自动安装**：它的官方安装方式是执行远程脚本，本工具不代为执行；restore 会在缺少 scoop 时停下并给出说明。

- **archlinuxcn 等第三方仓库**：新系统在恢复 `/etc/pacman.conf` 并安装对应 keyring 之前，这些仓库中的包会被列为"AUR 或未知"。按 restore 的提示处理后重跑 `--only packages` 即可。

- **两端内容不同的同名目标**（如 `.wakatime.cfg`、`.docker/config.json`）目前只由 Windows 托管；Linux 侧需要时，改为按平台区分的模板后登记到 `shared`。

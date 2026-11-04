# Kali 配置说明

本文件是给人和 AI agent 读的上下文，记录 Kali 这台机器在仓库中的位置：哪些配置部署、哪些不部署、哪些只在本机，以及在 Kali 上操作时的注意事项。两台 Linux 共用的同步规则见 [workflows_zh-CN.md](workflows_zh-CN.md) 第 6 节，常见陷阱见 [reference_zh-CN.md](reference_zh-CN.md) 6.5。Arch 侧的对应文档是 [arch.md](arch.md)。

迁移在 2026-11-03 完成，后续修剪和同步在 2026-11-04 完成。部署前审阅的完整计划和每项决策都在 git 历史里（`git log -- docs/kali.md`，例如 529e54c 版本），本文件不再保留。

## 1. 本机概况

| 项目 | 情况 |
|---|---|
| 角色 | 工作机，主要用于安全研究，**以稳定为先**。和 Arch（主力机）装在同一台实体机上 |
| 系统 | Kali Rolling，`ID=kali`，`ID_LIKE=debian`，实体机 |
| 桌面 | GNOME，Wayland（另外装了 XFCE，没有在用） |
| 用户 | 用户名和家目录与 Arch 不同，uid 相同（1000），umask 是 **002**（Arch 是 022） |
| 语言 | 系统 locale 是 `en_HK.UTF-8`，没有生成 zh_CN locale |
| 标签 | `kali`、`debian`、`gnome`、`xfce`（装了 XFCE，目前没有目标用到它）。没有 `arch`、`kde`、`konsole`、`zeroclaw`、`taskdog`。自己的标签可以写在 `~/.config/chezmoi/tags`（每行一个），`doctor` 会显示 |
| sudo | 需要密码。agent 在非交互 shell 里用不了 sudo，需要 sudo 的步骤要你自己在终端里运行 |
| gh | 没有登录（`gh auth status` 失败），agent 不能开 PR。dev 分支改完后直接在本地 `git merge --no-ff` 合并 |

## 2. 仓库中哪些内容在 Kali 上部署

规则都在 `home/.chezmoidata/platforms.toml`。Kali 上的结果：

| 规则 | 对 Kali 的影响 |
|---|---|
| `[requires] kde` | KDE 的配置**不部署**（kdeglobals、kwinrc、kglobalshortcutsrc、kxkbrc、dolphinrc、mimeapps.list、kwallet_auto_unlock.service） |
| `[requires] konsole` | Konsole 的配置（konsolerc、配色和配置方案）**不部署**，因为没装 konsole，见 4.2 |
| `[requires] arch` | `.config/user-dirs.dirs` **不部署**。Kali 保留自己的版本，因为 Kali 上 Templates、Public、Music、Videos 指向 `~` |
| `[requires] gnome` | **只在 Kali 上部署**：`.config/autostart/org.fcitx.Fcitx5.desktop`，见 4.1 |
| `[requires] zeroclaw` / `taskdog` | 两个用户单元**不部署**（Kali 上没有这两个程序）。原来部署过的单元文件已移到 `~/.local/state/dotfiles/kali-20261104-011810/` |
| `backup.linux.ignore` | `.config/autostart/fix-old-genmon-config.desktop`（XFCE 留下的）不备份，所以不会同步到 Arch |
| 模板 `config.kdl.tmpl` | 在有 `gnome` 标签并且装了 `wl-copy` 的机器上，zellij 配置会多出 `copy_command "wl-copy"`，见 4.2 |
| externals | `.config/nvim`、`.oh-my-zsh`（官方仓库的 clone，push 地址和 fetch 相同） |
| `system/kali/etc` | Kali 的 `/etc` 备份，见第 3 节。`system/arch` 在 Kali 上不使用 |
| `packages/aptpkg.txt` | `apt-mark showmanual` 的清单（230 个包）。backup 会更新它，restore 会安装缺少的包 |

除此以外的 linux 目标，两台机器共用**同一个源文件**：在 Kali 上 re-add 或 backup 的改动，会在 Arch 上 apply 时覆盖 Arch 的版本，反过来也一样。

## 3. `/etc` 备份（`system/kali/etc`）

2026-11-04 首次备份（03bf395）。收入的文件：

- 明文：`apt/sources.list.d/{kali,vscode}.sources`（kali 源用的是华为云镜像）、`environment`、`hostname`、`locale.conf`、`profile.d/{nmap,dotnet-cli-tools-bin-path}.sh`、`sddm.conf.d/kde_settings.conf`、`zsh/zshrc`。
- 加密：`crypttab`、`sudoers`、`sudoers.d/{kali-grant-root,ospd-openvas}`、WiFi 连接 `NetworkManager/system-connections/YM-5G-E559`。

排除和已知问题：

- 4 个 `apt.conf.d` 文件已加入 `etc.exclude`：`00CDMountPoint`、`00trustcdrom`、`00aptitude` 是安装程序生成的，`02autoremove-postgresql` 由 postgresql-common 自动生成。
- **软件包原样的文件不再收集**（0.6.0）：`zsh/zshrc` 和软件包里的版本完全一致，所以不再备份，`system/kali` 里原有的条目已在 2026-11-04 的 `backup` 中清理（d54cb3b）。`sudoers` 和两个 `sudoers.d` 文件只有 root 能读，要在终端里用 sudo 运行 `backup`，才能验证它们是不是软件包原样：是就清理，不是（或者验证不了）就保留。`sddm.conf.d/kde_settings.conf` 仍会备份（23 字节，无害）：kali-themes 把原版放在 `/usr/share/kali-themes/etc/` 下再复制过去，dpkg 里没有 `/etc` 路径对应的 md5。下面是 0.6.0 之前对这几个文件的记录：`sudoers`、两个 `sudoers.d` 文件、`zsh/zshrc`、`sddm.conf.d/kde_settings.conf` 在 Kali 上和软件包里的版本完全相同。`kde_settings.conf` 是 kali-themes 的 diversion，而 Kali 上没有装 sddm，用的是 gdm3。它们在 `etc.extra` / `etc.secret` 里是为 Arch 列的，而 `etc.exclude` 两个发行版共用，排除会影响 Arch。冗余但无害；改进的办法（跳过和软件包原样一致的文件）记在待办中。
- **重复加密**（0.5.4 已修复）：第二次 backup（ce6efa3）把内容没变的 `crypttab`、`sudoers` 和两个 `sudoers.d` 文件重新加密了一遍。原因已确认：0.5.2 靠 `chezmoi status` 判断是否变化，而 0440 用属性表示不出来，umask 002 又让 0644 被算成 0664。现在只比较解密后的内容和属性。
- 有 9 个 `.dpkg-new` / `.dpkg-old` 文件，**都不需要合并**（0.6.0 起 `dotfiles status` 不再列出它们，只在末尾给出数量）：8 个是 kali-themes / kali-menu 通过 diversion 换掉的软件包原版文件（plymouth、xfce 菜单和 xfconf 等，现用的是 Kali 的版本），1 个是 `update-ca-certificates` 重写 `ca-certificates.conf` 后留下的旧版本。保留它们没有影响。

## 4. 与 Arch 不同、需要特别处理的地方

### 4.1 fcitx5（输入法）

- **启动方式**：GNOME 下 fcitx5 由自启动项 `org.fcitx.Fcitx5.desktop` 启动；KDE Wayland 下由 KWin 启动，不需要这个文件。
- **语言**：fcitx5 退出时会把内存中的配置重写到文件里，注释使用会话的语言。Arch 的 KDE 会话通过 `plasma-localerc` 设了 `LANGUAGE=zh_CN:en_US`，Kali 是 `en_HK`。所以自启动项的 `Exec` 是 `env LANGUAGE=zh_CN:en_US /usr/bin/fcitx5`。在 `en_HK` 下，gettext 会读 `LANGUAGE`，不需要生成 zh_CN locale。没有这个设置时，两台机器会把注释在中英文之间来回改。
- **部署 fcitx5 配置**：直接 `dotfiles apply --apply`，它会让正在运行的 fcitx5 重新加载部署的文件（0.5.4 起，reference 6.4）。需要重启 fcitx5 时，用 `gio launch ~/.config/autostart/org.fcitx.Fcitx5.desktop` 启动（这样带上 `LANGUAGE`），直接在终端运行 `fcitx5 -d` 不会带上 `LANGUAGE`。检查写回：`busctl --user call org.fcitx.Fcitx5 /controller org.fcitx.Fcitx.Controller1 Save` 之后，`chezmoi status` 中应当没有 fcitx5 的条目。
- 列表型的值写回时顺序不固定，例如 `notifications.conf` 的 `HiddenNotifications`。所以只保留需要的项（现在只有 `wayland-diagnose-kde`）。
- GNOME 的 `switch-input-source` 两个快捷键已清空，把 `Super+space` 让给 fcitx5。kimpanel 扩展已启用，负责显示候选框。

### 4.2 终端和剪贴板

- 终端是 **gnome-terminal**，默认配置方案 "Arch Konsole"（gsettings 设置，不在仓库中）。它复刻了 Konsole 的字体（JetBrainsMono Nerd Font Mono Medium Italic 10）、Sweet-Ambar-Blue 配色和 150x40 窗口大小，没有透明效果。
- gnome-terminal（VTE）**不支持 OSC 52**。zellij 默认用 OSC 52 写剪贴板，开启 mouse_mode 后选区又属于 zellij，结果 Ctrl+Shift+C 复制不到内容。现在 zellij 配置里有 `copy_command "wl-copy"`：鼠标拖选、松开即复制；按住 Shift 拖选时用的是 gnome-terminal 自己的选区。其他依赖 OSC 52 的程序（tmux、nvim 远程复制）也要配置 `wl-copy`。
- 想换回 Konsole 的话：安装 `konsole`，然后重新运行 `chezmoi init`（`dotfiles restore --apply --only config`），得到 `konsole` 标签后，仓库里的 Konsole 配置（配色和配置方案）就会部署。GNOME 里 `Ctrl+Alt+T` 要打开 konsole 的话，另外在 gsettings 里设置自定义快捷键。

### 4.3 shell

- Kali 的 `~/.zprofile` 会用 `emulate sh` 加载 `.profile`。在 POSIX 模式下，`.` 一个不存在的文件会让登录 shell 直接退出，GDM 登录可能因此失败。所以共用的 shell 启动文件里，每个 `.` / `source` 都要先检查文件是否存在（cargo、asdf、go 等工具 Kali 上不一定有）。
- `.zshrc` 是明文。token 在加密的 `~/.config/zsh/secrets.zsh` 中，`.zshrc` 加载它之前也会先检查它是否存在。

### 4.4 权限

- umask 002：chezmoi 部署的不带 `private_` 的文件在 Kali 上是 664 / 775，Arch 上是 644 / 755。`chezmoi diff` 中只有权限位的差异就是这个原因，不用处理。
- `chezmoi re-add` 会把本地权限写回源状态。先 `chmod`，或者用 `dotfiles backup`（它会先收紧权限）。

## 5. 不在仓库中的本机状态

重装或换机器时要手动恢复的内容：

| 内容 | 位置和说明 |
|---|---|
| `~/.local/bin` 中的工具 | `gh`（官方 release tarball，已校验 sha256；man 手册在 `~/.local/share/man/man1`）、`zellij`（官方 musl release）、`uv` / `uvx`（pipx）。另外 `fd` → `/usr/bin/fdfind`、`bat` → `/usr/bin/batcat` 两个软链接是为了和 Arch 的命令名一致 |
| apt 安装的依赖 | 在 `aptpkg.txt` 中。`ncat` 是单独的包（`.ssh/config` 中 github.com 的 `ProxyCommand` 需要它，经 Clash 的 7897 端口转发） |
| 从 Arch 复制的文件 | 4 对服务器 SSH 密钥（webserver6、pwncollege、gpuserver、fjut）；合并后的 known_hosts（合并前的版本是 `known_hosts.pre-merge`）；`~/.vim`（vim-plug）；字体 `~/.local/share/fonts/arch`（JetBrainsMono Nerd、Consolas、微软雅黑）。GitHub 用的是 Kali 自己的 `id_ed25519` |
| IDA Pro 9.5 | `~/.local/share/ida-pro-9.5`、`~/.idapro`、`~/.local/bin/idapro`，以及 .desktop 文件。路径已改为 Kali 的家目录；IDAPython 用 `idapyswitch` 指向了 `libpython3.14`。遗留问题：`ida.reg` 中的许可证路径仍指向 Arch，每次启动都会提示一次。在 GUI 中通过 Help → License manager 重新选择一次许可证文件即可消除 |
| GNOME 设置 | 清空了 `switch-input-source` / `-backward`；启用 kimpanel；等宽字体 JetBrainsMono Nerd Font Mono 10；`caps:swapescape` 是 Kali 原有的设置。夜灯开过，现在是关闭的 |
| gnome-terminal | "Arch Konsole" 配置方案，见 4.2 |
| age 私钥 | `~/.config/chezmoi/key.txt`（600）。不在仓库中，要离线保存 |

## 6. 在 Kali 上的日常操作

- **以拉取为主**：`git pull`，然后 `uv run dotfiles apply` 预览，再 `uv run dotfiles apply --apply` 部署（fcitx5 的文件会自动重新加载，见 4.1）。
- **备份前先预览**：`uv run dotfiles backup -n`。`backup.linux` 的候选清单两台机器共用，Kali 上新出现的候选也会部署到 Arch。不想要的条目用 `dotfiles config add backup.linux.ignore <路径>` 排除，或者给它加上标签。`user-dirs.dirs`、`mimeapps.list` 在 `-n` 中显示为 `plain` 是正常的，它们会被 `[requires]` 排除。
- **`/etc`**：`dotfiles backup` 会读取 `/etc`，需要 sudo，要在终端里自己运行。不备份 `/etc` 时加 `--no-system`。
- **分支**：日常备份直接提交到 `main`。工具或规则的改动在主检出中 `git switch dev`，完成后切回 `main`，再 `git merge --no-ff dev`。停在 `dev` 上时，不要 `apply --apply` 或做日常 backup，因为主检出就是 chezmoi 生效中的源目录。
- **推送**：`.ssh/config` 让 GitHub 的连接走 `ncat` 代理，需要 Clash 在运行。Clash 没开时，用 `GIT_SSH_COMMAND='ssh -o ProxyCommand=none' git push` 直连。
- 2026-11-04 那次的一次性收尾脚本 `scripts/kali.sh` 已经执行过，之后从仓库删除（`git log -- scripts/kali.sh` 可以找到它的各个版本）。

## 7. 备份和回滚的位置

| 内容 | 位置 |
|---|---|
| apply 前被覆盖或删除的文件 | `~/.local/state/dotfiles/pre-apply-<时间>/`（迁移时是 `20261103-223642`，修剪时是 `20261104-012025`，fcitx5 是 `20261104-013608`） |
| 不再管理的用户单元 | `~/.local/state/dotfiles/kali-20261104-011810/` |
| 旧的 chezmoi 配置 | `~/.config/chezmoi/chezmoi.toml.bak-*` |
| 新建的文件 | `chezmoi managed` 会列出全部目标，删除对应文件即可 |
| GNOME 设置 | `gsettings reset <schema> <key>` |

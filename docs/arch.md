# Arch 配置说明

本文件是给人和 AI agent 读的上下文，记录 Arch 这台机器在仓库中的位置：哪些配置只在这里部署、哪些不在仓库中，以及在 Arch 上操作时的注意事项。两台 Linux 共用的同步规则见 [workflows_zh-CN.md](workflows_zh-CN.md) 第 6 节，常见陷阱见 [reference_zh-CN.md](reference_zh-CN.md) 6.5。Kali 侧的对应文档是 [kali.md](kali.md)。

内容按 2026-11-04 在本机核对的结果写成。

## 1. 本机概况

| 项目 | 情况 |
|---|---|
| 角色 | 主力机。和 Kali（工作机）装在同一台实体机上 |
| 系统 | Arch Linux，linux-zen 内核，实体机（虚拟机客户端工具已在修剪时卸载） |
| 桌面 | KDE Plasma 6，Wayland |
| 用户 | 用户名和家目录与 Kali 不同，uid 相同（1000），umask 是 **022**（Kali 是 002） |
| 语言 | 系统 locale 是 `en_US.UTF-8`；KDE 会话通过 `~/.config/plasma-localerc` 设为中文：`LANG=zh_CN.UTF-8`，`LANGUAGE=zh_CN:en_US` |
| 标签 | `arch`、`kde`、`taskdog`、`zeroclaw`。没有 `gnome`（找不到 gnome-shell），否则 Kali 的 fcitx5 自启动项会部署到这里 |
| 终端 | Konsole，默认配置方案"配置方案 1"：Sweet-Ambar-Blue 配色，JetBrainsMono Nerd Font Mono 10 Medium Italic，150x40 |
| sudo | 需要密码。agent 在非交互 shell 里用不了 sudo，读取 root 才能读的 `/etc` 文件、装卸软件包要你自己在终端里运行 |
| gh | 已登录，可以开 PR；不开 PR 时在本地 `git merge --no-ff dev` 合并 |
| Kali 分区 | 挂载在 `/mnt/kali`。**只读取，不写入**，可以用来核对 Kali 的实际状态（例如 dpkg 数据库） |

## 2. 只在 Arch 上部署的内容

规则都在 `home/.chezmoidata/platforms.toml`：

| 规则 | 对 Arch 的影响 |
|---|---|
| `[requires] kde` | KDE 和 Konsole 的配置只在这里部署：kdeglobals、kwinrc、kglobalshortcutsrc、kxkbrc、konsolerc、dolphinrc、mimeapps.list、konsole 配色和配置方案、`kwallet_auto_unlock.service` |
| `[requires] arch` | `.config/user-dirs.dirs`（Kali 保留自己的版本） |
| `[requires] zeroclaw` / `taskdog` | `zeroclaw.service`、`taskdog-server.service`，两个都已 enable。标签按 `~/.cargo/bin/zeroclaw`、`~/.local/bin/taskdog-server` 是否存在检测；卸载其中一个程序后重新运行 `chezmoi init`，对应的单元就不再部署 |
| `[requires] gnome` | 不部署 `.config/autostart/org.fcitx.Fcitx5.desktop` |
| `system/arch/etc` | Arch 的 `/etc` 备份：sddm、mkinitcpio、pacman 的配置和 hooks、timeshift、docker、mihomo、ufw、自建的 systemd 单元（clash-verge-service、shellcrash 等）、grub、网络连接（加密）、sudoers（加密）等。`dotfiles status` 的 "/etc backup targets" 列出全部 |
| `packages/archpkg.txt` | `pacman -Qqe` 的清单。backup 会更新它，restore 会安装缺少的包 |

其余的 linux 目标和 Kali 共用同一个源文件，见 workflows 第 6 节。

## 3. 与 Kali 不同、需要特别处理的地方

### 3.1 fcitx5

- **启动方式**：KDE Wayland 下 fcitx5 由 KWin 作为虚拟键盘启动（`kwinrc` 中的 `InputMethod`），不需要自启动项。不要在终端里用 `fcitx5 -d` 重启它，那样启动的实例不经过 KWin 的输入法协议。
- **写回**：fcitx5 退出时（以及收到 D-Bus `Save` 时）会把内存中的配置写回文件。`dotfiles apply` 部署了 fcitx5 的文件后会让它重新加载（reference 6.4）。2026-11-04 在本机验证过：只用 `fcitx5-remote -r`（全局的 `ReloadConfig`）不够，`notifications.conf` 这类插件配置要用 `ReloadAddonConfig`，否则 `Save` 会把旧内容写回来。
- **语言**：KDE 会话的 `LANGUAGE=zh_CN:en_US` 让 fcitx5 写回的注释是中文。Kali 的自启动项设置了同样的 `LANGUAGE`，所以两边写出的内容一致。

### 3.2 终端和剪贴板

- Konsole 的配置随 `kde` 标签部署。zellij 在这里不加 `copy_command`（模板只在 `gnome` 标签加 `wl-copy`），复制走 OSC 52。Konsole 26.08 是否放行 OSC 52 写剪贴板，本文档编写时没有在终端里核实；如果在 zellij 中选中后粘贴不出来，在 Konsole 配置方案的"高级"中检查剪贴板相关的设置，或者像 Kali 一样让模板在这里也加上 `copy_command "wl-copy"`。

### 3.3 `/etc`

- `etc.extra` / `etc.secret` 中的大部分条目是为 Arch 列的。Kali 上不存在的条目不收入；但 Kali 上存在、内容和软件包原样一致的（sudoers、`zsh/zshrc` 等）也会收进 `system/kali/etc`，见 kali.md 第 3 节。
- 不要为了 Kali 往 `etc.exclude` 里加 Arch 也有的路径：`exclude` 两边共用。
- `sudoers` 是 0440。0.5.4 之前，每次 backup 都会把它和其他 0440 / 0640 文件重新加密一遍，0.5.4 修复了这个问题。

## 4. 不在仓库中的本机状态

重装时要手动恢复的内容：

| 内容 | 位置和说明 |
|---|---|
| AUR 和其他外部的包 | `pacman -Qm` 有约 70 个（clash-verge-rev-bin、feishu-bin、consolas-font 等）。它们也在 `archpkg.txt` 中，restore 会交给 paru / yay |
| 字体 | 都来自软件包：`ttf-jetbrains-mono-nerd`、`consolas-font`（AUR）、`ttf-ms-win11-auto-zh_cn`（AUR，微软雅黑，从 Windows 镜像中提取）。Kali 上的副本是从这里复制的 |
| `/usr/local/bin` | 手动安装、不属于任何包：`git-xet`（`.gitconfig` 的 LFS xet 传输用它）、`chsrc`、`pwndbg`、`pixelterm` |
| 用户级工具 | `~/.cargo`（rustup 工具链，以及 zeroclaw、mdbook、just-lsp）；uv tools（taskdog-server / -ui / -mcp、demucs 等）；npm 全局包（`~/.npm-global`：cloudflare 的 `cf`、openclaw）。`zeroclaw`、`taskdog` 两个用户单元依赖其中的程序 |
| IDA Pro 9.5 | `~/.local/share/ida-pro-9.5`、`~/.idapro`、`~/.local/bin/idapro`。`.zshrc` 中的 `idapro` alias 会设置 `QT_QPA_PLATFORM=xcb` |
| 代理 | Clash Verge（`clash-verge-service`，单元在 `system/arch/etc`）；mihomo 的配置在 `system/arch/etc` 中（加密），服务没有在运行。`.gitconfig`、`.zshrc` 用的都是 7897 端口 |
| age 私钥 | `~/vg101/secret/chezmoi_age_key.txt`（600）。不在仓库中，要离线保存 |

## 5. 在 Arch 上的日常操作

- **apply 和 backup**：按 workflows 第 6 节，先预览再执行。`dotfiles backup` 会读取 `/etc`，需要 sudo，要在终端里自己运行；不备份 `/etc` 时加 `--no-system`。
- **分支**：日常备份直接提交到 `main`。工具或规则的改动在主检出中 `git switch dev`，完成后切回 `main` 合并（workflows 第 3 节）。
- 2026-11-04 那次的一次性收尾脚本 `scripts/arch.sh` 已经从仓库删除（`git log -- scripts/arch.sh` 可以找到它的各个版本）。它的第 3 步用 `fcitx5-remote -r` 让 fcitx5 重新加载，后来证明这样不够（见 3.1），现在由 `dotfiles apply` 处理。

## 6. 备份和回滚的位置

| 内容 | 位置 |
|---|---|
| apply 前被覆盖或删除的文件 | `~/.local/state/dotfiles/pre-apply-<时间>/`：shell 文件的检查 `20261103-233128`，修剪删除的 8 个文件 `20261104-002449`，aria2 `20261104-010002`，fcitx5 `20261104-020631` 及之后两次 |
| 2026-11-03 的 `/etc` 复核 | `~/.local/state/dotfiles/etc-review` |
| 新建的文件 | `chezmoi managed` 会列出全部目标，删除对应文件即可 |


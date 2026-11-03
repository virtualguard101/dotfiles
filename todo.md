# todo: Kali 迁移收尾与配置修剪

本文件是给人和 AI agent 读的上下文提示词, 记录两件事: Kali 迁移的结果, 以及接下来要在 Arch 上做的冗余配置修剪. 第 6 节的待办都处理完后, 删除本文件.

- Kali 迁移的完整变更清单和每一项决策: [docs/kali.md](docs/kali.md).
- 工具的结构: [docs/architecture_zh-CN.md](docs/architecture_zh-CN.md).
- 命令细节: [docs/reference_zh-CN.md](docs/reference_zh-CN.md).
- 分支用法: [docs/workflows_zh-CN.md](docs/workflows_zh-CN.md).

## 1. 背景

- 仓库是 chezmoi 的源目录 (`~/.local/share/chezmoi`, 由 `.chezmoiroot` 指向 `home/`). 附带一个由 uv 管理的 Python 工具 `dotfiles`, 负责 doctor / restore / apply / backup / clean.
- 仓库是**公开**的: 敏感文件都以 `encrypted_*.age` 形式保存. age 私钥不在仓库中, 需要离线保存; 提交时的检查会拦下明文密钥.
- 同一台机器上有两个 Linux 系统: Arch + KDE (主力), Kali + GNOME (工作, 主要用于安全研究, **以稳定为先**). 两者用户名不同, uid 相同. 第 2 节的 0.4.0 机制用来区分这两个系统.

## 2. 区分两个系统的机制 (0.4.0 起)

| 机制 | 作用 | 位置 |
|---|---|---|
| 平台归属 `[platforms]` | 每个目标只在 shared / linux / windows 中的一个平台部署 | `home/.chezmoidata/platforms.toml` |
| 自动登记 | `dotfiles backup` 会把没有归属的目标登记到当前平台; 提交检查 `no-platform-owner` 会拦下没有归属的目标 | `cli/backup.py`, `utils/common/guard/checks.py` |
| 环境标签 `[requires]` | 目标只部署到带有对应标签的机器. 标签在 `chezmoi init` 时检测, 来自 os-release 的 ID / ID_LIKE, 以及能否找到 plasmashell / gnome-shell. Arch 的标签是 `arch`, `kde`; Kali 的是 `kali`, `debian`, `gnome` | `platforms.toml`, `home/.chezmoi.toml.tmpl` |
| `/etc` 按发行版分目录 | `system/<ID>/etc`. Kali 上 restore 会跳过 `system/arch` | `cli/etc.py` |

目前的 `[requires]`:
- `kde`: KDE 配置和 Konsole 配置.
- `arch`: `.config/user-dirs.dirs`. Kali 保留自己的版本, 因为 Kali 上 Templates/Public/Music/Videos 指向 `~`.
- `zeroclaw`, `taskdog` (0.5.0 起, 程序标签): 对应的用户单元. 标签在 `~/.cargo/bin/zeroclaw`, `~/.local/bin/taskdog-server` 存在时才有, 所以 Kali 上不再部署这两个单元.

## 3. Kali 迁移结果 (2026-11-03 完成)

按 docs/kali.md 执行. 下面是与原计划不同的地方, 以及遇到的问题:

- **shell 启动文件必须先检查文件是否存在**. `.zshenv`, `.profile`, `.bash_profile` 原来无条件执行 `. "$HOME/.cargo/env"`, `.bashrc` 无条件加载 asdf, `.zshrc` 无条件调用 `go env`. Kali 的 `~/.zprofile` 会以 `emulate sh` 加载 `.profile`, 加载失败会中止登录 shell. 现在都已加上检查 (提交到 main).
- **oh-my-zsh 改为 chezmoi external** (`.oh-my-zsh`, git-repo). 原来仓库只管理 `.zshrc`, 新机器部署后会因为找不到 `$ZSH/oh-my-zsh.sh` 而报错.
- **用户目录**: `.config/user-dirs.dirs` 加上了 `[requires] arch`, 见第 2 节. Kali 上 `dotfiles backup` 碰到它时只会警告 "ignoring", 不会改动源文件 (已验证).
- **Kali 独有的自动启动项**: `fix-old-genmon-config.desktop` (XFCE), `org.fcitx.Fcitx5.desktop` (GNOME 下需要靠它启动 fcitx5; KDE Wayland 下由 KWin 启动, 不需要) 已加入 `backup.linux.ignore`, 避免同步到 Arch.
- **终端**: 为了兼容性, 用 gnome-terminal. 新建了一个 "Arch Konsole" 配置方案 (gsettings, 不在仓库中), 设为默认. 它复刻了 Konsole 的字体, Sweet-Ambar-Blue 配色和 150x40 窗口大小, 但没有透明效果. zellij 用官方 release 的二进制文件, 配置由仓库同步. gnome-terminal (VTE) 不支持 OSC 52, 而 zellij 默认用 OSC 52 写剪贴板, 且开启 mouse_mode 后选区属于 zellij, 导致 Ctrl+Shift+C 复制不到内容; `config.kdl` 已改为模板, 在 `gnome` 标签且有 `wl-copy` 时加上 `copy_command "wl-copy"` (Arch 上渲染结果不变).
- **gh**: 没有可用的 sudo, 所以没有添加 GitHub 的 apt 源, 改用官方 release 的 tarball 安装到 `~/.local/bin` (已校验 sha256). zellij 也装在这里.
- **从 Arch 直接复制、不进仓库的内容**: 4 个服务器 SSH 私钥, 合并 known_hosts, `~/.vim` (vim-plug), JetBrainsMono Nerd / Consolas / 微软雅黑字体 (放在 `~/.local/share/fonts/arch`), IDA Pro 9.5 (`~/.local/share/ida-pro-9.5`, `~/.idapro`, 启动链接和 .desktop 文件中的路径已改为 Kali 的家目录; IDAPython 用 `idapyswitch` 指向了 Kali 的 libpython3.14).
- **fcitx5 退出时会写回内存中的配置**. 在 fcitx5 运行时 apply 了它的配置, 之后要执行 `fcitx5-remote -r` 让它重新加载, 否则下次退出时会覆盖刚部署的文件.
- 已核实: chezmoi 中 os-release 的键名是 `idLike` (模板依赖这个键名); `dotfiles doctor` 只剩 "pacman is required" 一项失败 (apt 还不支持, 见第 6 节).
- 部署前被覆盖的 Kali 原有文件备份在 Kali 的 `~/.local/state/dotfiles/pre-apply-*`.

## 4. 日常同步规则

- **Kali 以拉取为主**: `git pull`, 然后 `uv run dotfiles apply` 预览, 再 `uv run dotfiles apply --apply` 部署.
- **在 Kali 上备份时先预览**: `backup.linux` 的候选清单 (`dotfiles.toml`) 两台 Linux 机器共用. Kali 上新加入的候选, 也会在 Arch 上部署. 先运行 `dotfiles backup -n` 审阅, 不想要的候选用 `dotfiles config add backup.linux.ignore <路径>` 屏蔽, 或者给它加上标签. `user-dirs.dirs` 和 `mimeapps.list` 在 `-n` 中显示为 `plain` 是正常的: 它们被 `[requires]` 排除, 实际运行时会被跳过.
- **通用文件在两台机器上共用同一个源文件**: 在 Kali 上 re-add 的改动, 会在 Arch 上 apply 时覆盖 Arch 的版本, 反过来也一样. 内容不同的文件, 要么改成模板 (按 `.chezmoi.osRelease.id` 或 `.tags` 区分), 要么用标签分开.
- **`/etc`**: Kali 上的 backup 会写入 `system/kali/etc`, 不会影响 `system/arch`. 如果不想备份 Kali 的 `/etc`, 用 `backup --no-system`.
- 日常备份直接提交到 `main`. 工具或规则的改动走 `dev` → PR → `main` (见 workflows 第 2 节), 开发时用 git worktree (workflows 第 3 节).

## 5. 常见陷阱

- 不要在配置文件中写 `$HOME`, 除非确认那个程序会展开它. 例如 zellij 的 `layout_dir` 不会展开, 而 zsh, systemd 的 `%h`, npm 的 `${HOME}` 会. 都不行时改用模板 `{{ .chezmoi.homeDir }}`.
- shell 启动文件中的 `.` / `source` 都要先检查文件是否存在. 两台机器装的工具不同, 而且失败的 `.` 会中止 POSIX 登录 shell.
- `chezmoi re-add` 会把本地文件的权限写回源状态. 某个程序把文件改写成 644 后直接 re-add, 源文件会丢掉 `private_`. 用 `dotfiles backup`, 它会先收紧权限; 或者先 `chmod 600` 再 re-add.
- `chezmoi diff` 输出很长时, 在 pager 中按 q 退出会让 chezmoi 卡住. 用 `--no-pager` 加 `| less -R`, 或者用 `dotfiles apply` 预览.
- 由模板生成的文件, re-add 不会处理. 需要手动把改动合并进 `.tmpl` (见 reference 第 6.3 节).
- 加密文件的内容不经过家目录路径检查. 在加密文件里写死路径不会被发现, `.zshrc` 和 VS Code / Cursor 的 `settings.json` 都出现过这种情况.
- 在 zsh 中, 把整条命令放进一个变量 (`C="chezmoi --config ..."; $C ...`) 不会按空格拆分. 改用函数, 或者 `${=C}`.
- 程序运行时会写回自己的配置的 (例如 fcitx5), apply 之后要让它重新加载, 或者先退出它再 apply.
- 依赖 OSC 52 的剪贴板功能 (zellij, tmux, nvim 的远程复制) 在 gnome-terminal 中无效, 要配置 `wl-copy` 之类的外部命令.

## 6. 待办

### 6.1 合并本次的 PR (在 Arch 或 Kali 上)

- [x] **Kali: `sudo apt install ncat`** (已安装, `/usr/bin/ncat`). `.ssh/config` 中 github.com 的 `ProxyCommand` 用的是 `ncat` (经 Clash 的 7897 端口转发). Arch 上 ncat 来自 nmap 包, Kali 上它是单独的包, 还没有安装. 装好之前, 推送要临时加上 `GIT_SSH_COMMAND='ssh -o ProxyCommand=none'` (直连可用).
- [x] 审阅并以 merge commit 方式合并 `dev` → `main` 的 PR. PR 内容: oh-my-zsh external, `[requires] arch`, docs/kali.md, 本文件, 以及两处测试修复 (`render_ignore` 改用空配置渲染, 不再读到本机的 `.tags`, 原来在没有 `kde` 标签的机器上会失败; `test_add_turns_inline_array…` 不再依赖仓库中 `backup.linux.ignore` 的当前内容).
- [x] 合并后在 Kali 上 (已完成, 4 个文件与 origin/main 逐字节一致后丢弃并 pull): 主检出中还保留着这些文件未提交的相同改动, 所以要先 `git checkout -- home/.chezmoidata/platforms.toml home/.chezmoiexternal.toml.tmpl docs/kali.md todo.md`, 再 `git pull`.
- [x] 合并后在 Arch 上 (2026-11-03): tags 为 `arch, kde`; `apply` 只有 4 个 shell 文件和 `.zshrc` (只有 `go` 那一行), 已部署, 新开的 zsh / bash 登录 shell / `.profile` 均无报错.

### 6.2 0.5.0 合并后 (dev → PR → main)

- [ ] **Arch**: `git pull`, 然后 `uv run dotfiles restore --apply --only config` 重新生成配置 (doctor 会提示配置与模板不一致), tags 应为 `arch, kde, taskdog, zeroclaw`. 在那之前两个用户单元被视为缺少标签而忽略 (文件不会被删除, backup 遇到它们只会警告). 然后 `uv run dotfiles apply` 预览: 应当只有 `.aria2/aria2.conf` (路径改为家目录下) 和新建的空文件 `.aria2/aria2.session`.
- [ ] **Kali**:
  - `git pull`, `uv run dotfiles restore --apply --only config` (tags 不变, 仍是 `kali, debian, gnome`), `uv run dotfiles apply --apply`. 两个用户单元从此不受管理, 之前部署的两个文件可以手动删除 (`~/.config/systemd/user/{zeroclaw,taskdog-server}.service`, 从未 enable);
  - 把 oh-my-zsh 的推送地址恢复原样 (0.4.0 的 hooks 步骤改过): `git -C ~/.oh-my-zsh config --unset remote.origin.pushurl`;
  - `uv run dotfiles doctor`: "pacman is required" 应当消失, `aptpkg.txt` 显示缺少的包数;
  - `/etc` 备份 (需要 sudo): 先 `uv run dotfiles status` 看 "/etc backup targets", 再 `uv run dotfiles backup -n` 预览, 确认后 `uv run dotfiles backup` 写入 `system/kali/etc`. 从 Arch 只读预览的结果: `etc.extra` 中 Arch 专用的条目 (sddm.conf, mkinitcpio, pacman hooks, timeshift, docker, mihomo) 在 Kali 上不存在, 自动跳过; 会收入 `/etc/apt/sources.list.d` (kali.sources, vscode.sources), `/etc/apt/apt.conf.d` 中 4 个非软件包文件, `/etc/profile.d` 中 2 个 (nmap.sh, dotnet-cli-tools-bin-path.sh), `sddm.conf.d/kde_settings.conf`, 以及 hostname, locale.conf, vconsole.conf, environment, zsh/zshrc. 可读的 conffile 中没有被用户改过的 (14 个看似改过的都是 kali-defaults / kali-themes 的 dpkg-divert); 13 个只有 root 可读的 conffile 要到 Kali 上用 sudo 才能检查. 不需要的条目用 `dotfiles config add etc.exclude <路径>` 排除.

### 6.3 在 Arch 上修剪冗余配置

依据: 2026-11-03 从 Kali 只读检查 Arch 根分区和家目录的结果; 2026-11-03 在 Arch 上逐项复核 (标注 "复核"). 修剪时以 Arch 的实际情况为准. **删除类操作等待审阅**, 汇总在 6.4.

每删除一个目标, 需要同步修改:
- `home/` 中的源文件;
- `platforms.toml` 中的条目 (提交检查和 `dotfiles check` 要求两边一致);
- 如果它在 `dotfiles.toml` 的 `backup.linux.track` 中, 也要删掉, 否则下次 backup 又会加回来. 只想停止备份的话, 改为加入 `ignore`.

删除归属, `requires` 之类属于规则改动, 走 dev → PR. 只改 dotfile 内容的话, 可以直接提交到 main.

| 目标 | 检查结果 | 建议 |
|---|---|---|
| `.zimrc`, `.p10k.zsh` (以及 Arch 的 `~/.zim`) | `.zshrc` 用的是 oh-my-zsh + robbyrussell, 不加载 zim 和 p10k | 从仓库删除; 本地文件可以一起删掉 |
| `.config/ghostty/config` | Arch 和 Kali 都没装 ghostty, 文件只是自动生成的模板 | 删除 (以后真的用 ghostty 时再加) |
| `.config/autostart/vboxclient.desktop` | 两个系统都是实体机; `VBoxClient-all` 来自 virtualbox-guest-utils | 删除; 同时考虑卸载 Arch 的 `virtualbox-guest-utils` 和 `open-vm-tools` (虚拟机客户端工具, 在实体机上没有作用; `vmtoolsd` 也装着) |
| `.config/Cursor/User/*` | Arch 上没有 cursor 程序 (`~/.local/bin` 中只有 cursor-agent), 只剩配置目录 | 不再使用 Cursor 的话就删除, `dotfiles clean` 的 leftovers 规则也会把它列出来 |
| `.zshrc` 中的 conda/mamba 初始化块 | Arch 上没有 `~/miniforge3`, 这段代码不起作用 | 待审阅 (见 6.4). `.zshrc` 已是明文 (6.5), 改完直接 re-add. 被注释掉的旧 `UV_PUBLISH_TOKEN` 已随拆分移到 `~/.config/zsh/secrets.zsh`, 是否删除也在 6.4 |
| `.local/share/fcitx5/rime/default.custom.yaml` | fcitx5 的 profile 中只有 pinyin, keyboard-us, keyboard-jp, 没有启用 rime | 不用 rime 的话就删除, 以及 Arch 的 fcitx5-rime, rime-ice-git, rime-pinyin-moegirl; 想用的话把 rime 加进 profile |
| `.aria2/aria2.conf` | `input-file`/`save-session` 指向不存在的 `/etc/aria2/aria2.session`; `dir=~/downloads` 也不存在 (实际是 `~/Downloads`). 复核: aria2 **不展开** `~` 和 `$HOME` | **已修复 (0.5.0)**: 改为模板, 路径用 `{{ .chezmoi.homeDir }}`; 会话文件由 `create_empty_aria2.session` 在缺失时创建, 内容不进仓库. 另: shell 中的 `all_proxy=socks://...` 会让 aria2 报一条 "unrecognized proxy format", 不影响下载 (走 `http(s)_proxy`) |
| `.gitconfig` 的 `[lfs "customtransfer.xet"]` | ~~Arch 上没有 `git-xet`~~ 复核: `/usr/local/bin/git-xet` 存在 (手动安装, 不在 pacman 中), `git lfs env` 已列出 xet 传输 | **保留** |
| `.tool-versions` 和 `.bashrc` 中的 asdf | asdf (旧的 bash 版) 只在 `.bashrc` 中加载, 而登录 shell 是 zsh, 所以 erlang/elixir 平时不在 PATH 中 | 不再使用的话, 删除 `.tool-versions`, `.bashrc` 中的 asdf 部分和 `~/.asdf`; 还要用的话, 在 `.zshrc` 中也加载 asdf |
| VS Code / Cursor `settings.json` (加密) | 写死了 Arch 的家目录 (kubectl/helm 路径, leetcode / lcpr 的 workspaceFolder), 在 Kali 上无效; 还混有 Windows 专用的设置. 复核: 加密的原因是第 28 / 38 行有 API key. kubectl / helm 两项: vscode-kubernetes 扩展已不在 `~/.vscode/extensions` 中, 只剩它在 2025 年下载的工具 (`~/.local/state/vs-kubernetes/tools`), 属于残留; leetcode / lcpr 两项: 扩展默认值为空 (未设置), 两个目录在 Arch 上都存在 | 待审阅 (见 6.4). 不建议改成模板: `re-add` 不处理模板, VS Code 设置经常变化, 之后每次都要手动合并 |
| `dotfiles.toml` 中 `backup.linux.track` 的 `.config/spectaclerc` | 复核: 被跳过是因为含有家目录路径 (`lastImageSaveLocation` 等上次保存的位置). 其余是少量偏好: 标注工具的颜色, OCR 语言, 查看器窗口尺寸 | 待审阅 (见 6.4) |
| `archpkg.txt` | 由 backup 导出. 修剪 Arch 上的软件包之后, 再运行一次 backup 就会更新 | — |

保留: `zeroclaw.service`, `taskdog-server.service`. 它们在 Arch 上已 enable, 对应的程序也在; 0.5.0 起挂在程序标签下 (第 2 节).

### 6.4 待审阅的删除项

审阅结论记录在这里, 然后按 6.3 开头的规则执行. 这些都还没有执行.

### 6.5 后续版本

- [x] apt 软件包清单 (0.5.0): backup 导出 `aptpkg.txt`, restore 用 apt 安装缺少的包, doctor 检查 apt.
- [ ] Kali 的 `/etc` 备份: 工具部分已完成 (0.5.0: dpkg 的软件包文件归属, 改过的 conffile, diversion); 实际备份在 Kali 上执行, 见 6.2.
- [x] 为依赖特定程序的目标增加标签 (0.5.0): `zeroclaw`, `taskdog`.
- [x] 把 `.zshrc` 中的密钥拆到单独的加密文件 (main, 2daf2e0): `UV_PUBLISH_TOKEN` 移到 `~/.config/zsh/secrets.zsh` (加密), `.zshrc` 改为明文 `private_dot_zshrc`.
- [x] restore 的 `hooks` 步骤只改同一 GitHub 账号的仓库 (0.5.0). Kali 上 ohmyzsh 的推送地址要手动恢复, 见 6.2.
- [x] 没有 sudo 时的建议 (0.5.0): bootstrap 失败时给出 release 二进制文件装到 `~/.local/bin` 的做法, 并预先把它加进 `PATH`; doctor 对 gh, just 给出同样的提示.
- [ ] 想在 Kali 上换回 Konsole 时 (docs/kali.md C1 的选项 (a)): 新增 `konsole` 标签 (按能否找到 konsole 检测), 把 `.config/konsolerc` 和 `.local/share/konsole` 从 `kde` 移到这个标签下.
- [ ] Windows 系统配置备份: 用 `system/windows/` 保存导出的注册表等系统设置 (目录已预留, 功能尚未实现).
- [ ] 检测更多标签的需求 (例如 `xfce`, 手动设置的本机标签), 有实际用途时再加.
- [ ] 全部完成后删除本文件.

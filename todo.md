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

### 6.2 0.5.0 (已合并, #11) 与修剪 (0.5.1) 合并后

- [ ] **Arch** (修剪已在本机执行, 见 6.3):
  - 合并 0.5.1 的 PR 后 `git switch main && git pull`. 合并之前不要在 `main` 上 `dotfiles apply --apply`: `main` 上还没有 `.chezmoiremove`, 会把已删除的 8 个文件重新部署回来;
  - `uv run dotfiles restore --apply --only config` 重新生成配置, tags 应为 `arch, kde, taskdog, zeroclaw`. 在那之前两个用户单元被视为缺少标签而忽略 (文件不会被删除, backup 遇到它们只会警告);
  - `uv run dotfiles apply` 预览: 应当只有 `.aria2/aria2.conf` (路径改为家目录下), 新建的空文件 `.aria2/aria2.session`, 以及 fcitx5 自己改写的 `notifications.conf` (想保留本机版本就先 `dotfiles backup`);
  - 卸载虚拟机客户端工具和 rime (需要 sudo, 共 21 个包, 其中 16 个是不再需要的依赖):
    ```bash
    sudo systemctl disable vboxservice.service
    sudo pacman -Rns virtualbox-guest-utils open-vm-tools fcitx5-rime rime-ice-git rime-pinyin-moegirl
    uv run dotfiles backup          # 在 main 上: archpkg.txt 随之更新, 再提交
    ```
- [x] **Kali** (2026-11-04 完成): 第 1-4 步由脚本完成 (单元移到 `~/.local/state/dotfiles/kali-20261104-011810`); 第 5 步因 apply 演练缺少 `--force` 而失败 (本地改过的目标会让 chezmoi 询问, `--no-tty` 下读到 EOF), 什么都没写入. 修复在 dev 的 0.5.3 (620e604, 待开 PR); 部署改在 worktree 中用 0.5.3 完成 (dev 与 main 的 `home/` 无差异), 20 项全部部署, 备份在 `pre-apply-20261104-012025`, 之后 `fcitx5-remote -r`, 删除 4 个空目录. 被覆盖的 6 个 `MM` 项: 5 个 fcitx5 文件是 fcitx5 写回的默认值 (丢了 keyboard-jp 和云拼音), VS Code 只是删掉 Arch 路径. 第 2 步的 "already done" 不是问题: 渲染结果与现有配置相同, 只是模板哈希变了, 手动 `chezmoi init` 后警告消失. 原计划:
  - 重新生成配置;
  - 恢复 oh-my-zsh 的推送地址;
  - 把两个不再管理的用户单元移到 `~/.local/state/dotfiles/kali-*` (`.chezmoiignore` 优先于 `.chezmoiremove`, 被忽略的目标不会被删除, 所以不能靠 `.chezmoiremove`);
  - `dotfiles apply`: 部署新的 `.zshrc` 和 `secrets.zsh`, aria2 模板, 并删除 `.chezmoiremove` 中的 8 个目标 (先备份到 `pre-apply-*`);
  - 删除因此变空的目录;
  - 运行 `dotfiles doctor`: "pacman is required" 应当消失, `aptpkg.txt` 显示缺少的包数.
- [ ] **Kali 的 `/etc` 备份** (需要交互式 sudo, 要在终端里自己运行): 已审阅 `dotfiles status` 的清单, 并把 4 个安装程序 / postgresql 生成的 `apt.conf.d` 文件 (00CDMountPoint, 00trustcdrom, 00aptitude, 02autoremove-postgresql) 加入 `etc.exclude`. 剩下要做的: `uv run dotfiles backup -n` (sudo 会检查 11 个只有 root 可读的 conffile), 确认后 `uv run dotfiles backup`, 再提交 `system/kali` 并推送. 已知的冗余: `sudoers`, `sudoers.d/{kali-grant-root,ospd-openvas}`, `zsh/zshrc`, `sddm.conf.d/kde_settings.conf` (kali-themes 的 diversion, Kali 没装 sddm) 在 Kali 上都是软件包原样, 但它们在 `extra` / `secret` 中是为 Arch 列的, `etc.exclude` 两边共用, 不能排除, 见 6.4. 原记录: 先 `uv run dotfiles status` 看 "/etc backup targets", 再 `uv run dotfiles backup -n` 预览, 确认后 `uv run dotfiles backup` 写入 `system/kali/etc`. 从 Arch 只读预览的结果: `etc.extra` 中 Arch 专用的条目 (sddm.conf, mkinitcpio, pacman hooks, timeshift, docker, mihomo) 在 Kali 上不存在, 自动跳过; 会收入 `/etc/apt/sources.list.d` (kali.sources, vscode.sources), `/etc/apt/apt.conf.d` 中 4 个非软件包文件, `/etc/profile.d` 中 2 个 (nmap.sh, dotnet-cli-tools-bin-path.sh), `sddm.conf.d/kde_settings.conf`, 以及 hostname, locale.conf, vconsole.conf, environment, zsh/zshrc. 可读的 conffile 中没有被用户改过的 (14 个看似改过的都是 kali-defaults / kali-themes 的 dpkg-divert); 13 个只有 root 可读的 conffile 要到 Kali 上用 sudo 才能检查. 不需要的条目用 `dotfiles config add etc.exclude <路径>` 排除.

### 6.3 修剪冗余配置 (2026-11-04 完成)

依据: 2026-11-03 从 Kali 只读检查 Arch 的结果, 并在 Arch 上逐项复核. 停用一个目标的步骤见 reference 3.3 "停用一个目标": 删除源文件和归属, 从 `backup.linux.track` 中删除, 列进 `home/.chezmoiremove`.

| 目标 | 处理 | 提交 |
|---|---|---|
| `.zimrc`, `.p10k.zsh` | 停用 (`.zshrc` 用 oh-my-zsh); Arch 的 `~/.zim` (86M) 已删除 | dev |
| `.config/ghostty/config` | 停用 (两边都没装 ghostty) | dev |
| `.config/autostart/vboxclient.desktop` | 停用 (实体机); 卸载 virtualbox-guest-utils, open-vm-tools 见 6.2 | dev |
| `.config/Cursor/User/*` | 停用 (没装 Cursor); Arch 的 `~/.config/Cursor` (7.1G) 已删除. `cursor-agent` 不受影响 | dev |
| `.local/share/fcitx5/rime/default.custom.yaml` | 停用 (rime 未启用); Arch 的 rime 数据 (40M) 已删除; 卸载 rime 的包见 6.2 | dev |
| `.tool-versions`, `.bashrc` 中的 asdf | 停用 / 删除; Arch 的 `~/.asdf` (192M) 已删除 | dev / main |
| `.zshrc` 中的 conda/mamba 初始化块 (95 行) | 删除; Jupyter 和 `~/.local/bin` 两行保留 | main (5d37765) |
| `secrets.zsh` 中注释掉的旧 `UV_PUBLISH_TOKEN` | 删除. **如果它在 PyPI 上还没有作废, 去 PyPI 作废它**: 它曾以加密形式存在于仓库历史中 | main (5d37765) |
| VS Code `settings.json` 的 kubectl / helm 路径, leetcode / lcpr 的 workspaceFolder | 删除, 文件中不再有家目录路径; Arch 的 `~/.local/state/vs-kubernetes` (111M) 已删除 | main (5d37765) |
| `backup.linux.track` 的 `.config/spectaclerc` | 移出 track (含家目录路径, 从未被备份) | dev |
| `.aria2/aria2.conf` | 0.5.0 已修复: 改为模板, 会话文件由 `create_empty_aria2.session` 创建 | — |
| `.gitconfig` 的 `[lfs "customtransfer.xet"]` | 保留: `/usr/local/bin/git-xet` 存在 | — |
| `zeroclaw.service`, `taskdog-server.service` | Arch 保留 (程序标签); Kali 上由 `scripts/kali.sh` 移走 | — |

Arch 上被 `.chezmoiremove` 删除的 8 个文件备份在 `~/.local/state/dotfiles/pre-apply-20261104-002449`. 修剪中发现但未处理: `~/.local/share/mamba` (mamba 的根目录, 不在本次清单中, 需要的话另行决定).

### 6.4 后续版本

- [x] apt 软件包清单 (0.5.0): backup 导出 `aptpkg.txt`, restore 用 apt 安装缺少的包, doctor 检查 apt.
- [ ] Kali 的 `/etc` 备份: 工具部分已完成 (0.5.0: dpkg 的软件包文件归属, 改过的 conffile, diversion); 实际备份在 Kali 上执行, 见 6.2.
- [x] 为依赖特定程序的目标增加标签 (0.5.0): `zeroclaw`, `taskdog`.
- [x] 把 `.zshrc` 中的密钥拆到单独的加密文件 (main, 2daf2e0): `UV_PUBLISH_TOKEN` 移到 `~/.config/zsh/secrets.zsh` (加密), `.zshrc` 改为明文 `private_dot_zshrc`.
- [x] restore 的 `hooks` 步骤只改同一 GitHub 账号的仓库 (0.5.0). Kali 上 ohmyzsh 的推送地址要手动恢复, 见 6.2.
- [x] 没有 sudo 时的建议 (0.5.0): bootstrap 失败时给出 release 二进制文件装到 `~/.local/bin` 的做法, 并预先把它加进 `PATH`; doctor 对 gh, just 给出同样的提示.
- [ ] 想在 Kali 上换回 Konsole 时 (docs/kali.md C1 的选项 (a)): 新增 `konsole` 标签 (按能否找到 konsole 检测), 把 `.config/konsolerc` 和 `.local/share/konsole` 从 `kde` 移到这个标签下.
- [ ] Windows 系统配置备份: 用 `system/windows/` 保存导出的注册表等系统设置 (目录已预留, 功能尚未实现).
- [ ] `/etc` 备份: `extra` / `secret` 中直接列出的**文件**, 如果是软件包原样 (pacman / dpkg 的 md5 一致), 应当跳过; 或者支持按发行版的 `exclude`. 现在 Kali 会收入 sudoers 等 5 个软件包原样的文件. 注意这会改变 Arch 现有 `system/arch` 的内容, 走 PR 并先在 Arch 上 `backup -n` 核对.
- [ ] 检测更多标签的需求 (例如 `xfce`, 手动设置的本机标签), 有实际用途时再加.
- [ ] 全部完成后删除本文件.

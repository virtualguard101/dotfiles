# todo: Kali 迁移与同步

本文件是给在 Kali 上继续工作的人和 AI agent 读的上下文提示词: 把仓库已有的配置迁移到同一台机器上的 Kali, 并建立两个 Linux 系统之间的日常同步. 迁移完成, 下面的待办处理完后, 删除本文件.

本次改动的实现计划见 [.claude/plans/0.4.0-ownership-and-kali.md](.claude/plans/0.4.0-ownership-and-kali.md). 工具的结构见 [docs/architecture_zh-CN.md](docs/architecture_zh-CN.md), 命令细节见 [docs/reference_zh-CN.md](docs/reference_zh-CN.md), 分支用法见 [docs/workflows_zh-CN.md](docs/workflows_zh-CN.md).

## 1. 背景

- 仓库是 chezmoi 的源目录 (`~/.local/share/chezmoi`, 由 `.chezmoiroot` 指向 `home/`). 附带一个由 uv 管理的 Python 工具 `dotfiles`, 负责 doctor / restore / apply / backup / clean.
- 仓库是**公开**的: 敏感文件都以 `encrypted_*.age` 形式保存. age 私钥不在仓库中, 需要离线保存; 提交时的检查会拦下明文密钥.
- 原有的 Linux 机器是 Arch + KDE. 0.4.0 之前, "linux" 实际上等同于 "Arch + KDE", 所以需要先做下面这些改动, 才能迁移到 Kali.

## 2. 0.4.0 为迁移提供的机制

| 机制 | 作用 | 位置 |
|---|---|---|
| 平台归属 `[platforms]` | 每个目标只在 shared / linux / windows 中的一个平台部署 | `home/.chezmoidata/platforms.toml` |
| 自动登记 (issue #8) | `dotfiles backup` 会把没有归属的目标登记到当前平台; 提交检查 `no-platform-owner` 会拦下没有归属的目标 | `cli/backup.py`, `utils/common/guard/checks.py` |
| 环境标签 `[requires]` | KDE 配置只部署到带 `kde` 标签的机器. 标签在 `chezmoi init` 时检测, 来自 os-release ID / ID_LIKE, 以及能否找到 plasmashell / gnome-shell | `platforms.toml`, `home/.chezmoi.toml.tmpl` |
| `/etc` 按发行版分目录 | `system/arch/etc` 只属于 Arch. Kali 上 restore 会自动跳过它; Kali 上 backup 会写入 `system/kali/etc` | `cli/etc.py` |
| bootstrap 支持 apt | Debian / Kali 上用 apt 安装 git, chezmoi, pipx, 再用 pipx 安装 uv | `bootstrap.sh` |

## 3. Kali 的已知情况

迁移前从 Arch 一侧只读检查的结果. 迁移时以实际情况为准.

- Kali Rolling, Debian 系, 内核为 kali-amd64. 桌面是 GNOME (gdm3), 另外装了 XFCE. 默认输入法是 ibus, 已经装了 fcitx5 和 fcitx5-chinese-addons.
- 用户名和 Arch 上不同, uid 相同. 默认 shell 已经是 zsh. 已有 SSH 密钥 `id_ed25519`.
- 已安装: git, curl, zsh, chezmoi, tmux, pipx, fcitx5, cc-switch, clash-verge.
- apt 中有: neovim, htop, btm (即 bottom), aria2, fcitx5-rime, just, ripgrep, fd-find, fzf, eza, bat, age, pre-commit.
- apt 中**没有**: uv (bootstrap 会改用 pipx 安装), zellij, gh, ghostty.
- home 根目录有一份 age 私钥副本 `~/chezmoi_age_key.txt`, 权限是 755. 它对应的公钥和仓库一致.

## 4. 迁移步骤 (在 Kali 上执行)

1. 安装常用工具:

    ```bash
    sudo apt install neovim htop btm aria2 fcitx5-rime just ripgrep fd-find fzf age pre-commit
    ```

2. 运行 bootstrap. 先只看计划, 确认无误后再执行:

    ```bash
    curl -fsSLO https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.sh
    less bootstrap.sh
    bash bootstrap.sh                                         # 只显示计划
    bash bootstrap.sh --apply --key-file ~/chezmoi_age_key.txt
    ```

    预期结果:
    - `key` 步骤把私钥写到 `~/.config/chezmoi/key.txt`, 权限 600;
    - `config` 步骤生成的配置中, tags 应包含 `kali`, `debian`, `gnome`;
    - `system` (本发行版没有备份) 和 `packages` (不是 Arch) 两个步骤自动跳过;
    - Kali 原有的、将被覆盖的文件 (例如它自带的 `.zshrc`), 会先备份到 `~/.local/state/dotfiles/pre-apply-*`.

3. 确认 `~/.config/chezmoi/key.txt` 已就位后, 删除私钥副本: `rm ~/chezmoi_age_key.txt`.

4. 验证:

    ```bash
    cd ~/.local/share/chezmoi
    uv run dotfiles doctor        # tags, 私钥, 配置
    chezmoi data | grep -A3 osRelease   # 核对 ID_LIKE 在 chezmoi 中的键名是否为 idLike (模板依赖这个键名)
    uv run dotfiles check
    uv run dotfiles apply         # 应当没有待部署项
    ```

5. 安装 apt 中没有的工具:
    - zellij, ghostty: 用官方 release 或 cargo 安装;
    - gh: 使用 GitHub 官方的 apt 源.

6. 输入法: 如果想换成 fcitx5 (仓库里有 fcitx5 和 rime 的配置), 在 GNOME 中需要安装 kimpanel 扩展, 并把输入法切换为 fcitx5 (`im-config`). 否则继续使用 ibus.

7. 要从 Kali 推送提交的话, 把 Kali 的 SSH 公钥登记到 GitHub. restore 的 `hooks` 步骤已经把 push 地址改成了 SSH.

## 5. 部署到 Kali 的目标

| 分类 | 目标 |
|---|---|
| 会部署 (通用) | `.zshrc` `.zshenv` `.zimrc` `.p10k.zsh` `.bashrc` `.profile` `.bash_profile` `.vimrc` `.tmux.conf` `tmux/` `.tool-versions` `.config/nvim` (externals) `.config/zellij` `.config/htop` `.config/bottom` `.config/gh` `.config/fontconfig` `.config/environment.d` `.config/user-dirs.dirs` `.config/Code` `.config/Cursor` `.gitconfig` `.ssh/config` `.npmrc` `.pypirc` `.aria2/aria2.conf` `.agents/ccs_config.sql`; 自启动项 CC Switch, Clash Verge |
| 会部署, 但要看情况 | `.config/fcitx5`, `.local/share/fcitx5/rime` (见第 4 节第 6 步); `.config/ghostty` (先装 ghostty); `zeroclaw.service`, `taskdog-server.service` (缺少对应程序时, 单元只是放在那里, 不会启动); `vboxclient.desktop` (只对 VirtualBox 客户机有意义, 可能是 Arch 上的残留) |
| 不会部署 (`kde` 标签) | `kdeglobals` `kwinrc` `kglobalshortcutsrc` `kxkbrc` `konsolerc` `dolphinrc` `mimeapps.list` `.local/share/konsole` `kwallet_auto_unlock.service` |
| 不迁移 | `system/arch/` (Arch 的 `/etc`), `packages/archpkg.txt` (Arch 的软件包清单) |

预览 Kali 视角下会部署什么 (在任意机器上都可以运行):

```bash
chezmoi --override-data '{"chezmoi":{"os":"linux"},"tags":["kali","debian","gnome"]}' managed
```

## 6. 日常同步规则

- **Kali 以拉取为主**: `git pull`, 然后 `uv run dotfiles apply` 预览, 再 `uv run dotfiles apply --apply` 部署.
- **在 Kali 上备份时先预览**: `backup.linux` 的候选清单 (`dotfiles.toml`) 两台 Linux 机器共用. Kali 上新加入的候选, 也会在 Arch 上部署. 先运行 `dotfiles backup -n` 审阅, 不想要的候选用 `dotfiles config add backup.linux.ignore <路径>` 屏蔽, 或者给它加上标签.
- **通用文件在两台机器上共用同一个源文件**: 在 Kali 上 re-add 的改动, 会在 Arch 上 apply 时覆盖 Arch 的版本, 反过来也一样. 内容不同的文件, 要么改成模板 (按 `.chezmoi.osRelease.id` 或 `.tags` 区分), 要么用标签分开.
- **`/etc`**: Kali 上的 backup 会写入 `system/kali/etc`, 不会影响 `system/arch`. 如果不想备份 Kali 的 `/etc`, 用 `backup --no-system`.
- 日常备份直接提交到 `main`. 工具或规则的改动走 `dev` → PR → `main` (见 workflows 第 2 节).

## 7. 常见陷阱

- 不要在配置文件中写 `$HOME`, 除非确认那个程序会展开它. 例如 zellij 的 `layout_dir` 不会展开, 而 zsh, systemd 的 `%h`, npm 的 `${HOME}` 会. 都不行时改用模板 `{{ .chezmoi.homeDir }}`.
- `chezmoi re-add` 会把本地文件的权限写回源状态. 某个程序把文件改写成 644 后直接 re-add, 源文件会丢掉 `private_`. 用 `dotfiles backup`, 它会先收紧权限; 或者先 `chmod 600` 再 re-add.
- `chezmoi diff` 输出很长时, 在 pager 中按 q 退出会让 chezmoi 卡住. 用 `--no-pager` 加 `| less -R`, 或者用 `dotfiles apply` 预览.
- 由模板生成的文件, re-add 不会处理. 需要手动把改动合并进 `.tmpl` (见 reference 第 6.3 节).
- 加密文件的内容不经过家目录路径检查. 在加密文件里写死路径不会被发现, `.zshrc` 曾经就是这样.

## 8. 待办 (后续版本)

- [ ] 在 Kali 上完成第 4 节的迁移, 并把结果 (遇到的问题, 需要调整的标签或模板) 回写到本文件或 issue.
- [ ] apt 软件包清单: `packages/aptpkg.txt` (由 `apt-mark showmanual` 导出), backup 和 restore 按包管理器处理. 目前只支持 pacman, 其他发行版的 packages 步骤会跳过.
- [ ] Windows 系统配置备份: 用 `system/windows/` 保存导出的注册表等系统设置 (目录已预留, 功能尚未实现).
- [ ] 为依赖特定程序的目标增加标签, 例如 `vboxclient.desktop`, `zeroclaw.service`, `taskdog-server.service`; 或者确认 `vboxclient.desktop` 是残留后删除它.
- [ ] 把 `.zshrc` 中的密钥拆到单独的加密文件, 让 `.zshrc` 本身变成明文, 以便审查和检查.
- [ ] 检测更多标签的需求 (例如 `xfce`, 手动设置的本机标签), 有实际用途时再加.
- [ ] 全部完成后删除本文件.

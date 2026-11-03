# Kali 迁移变更清单

本文件列出把仓库配置部署到 Kali 时**将要做的全部改动**, 供执行前审阅. 分为两部分:

- **A. 已在工作区完成的改动**: 只改了仓库文件, 未提交, 没有写入家目录以外的任何位置, 可以用 `git diff` / `git checkout` 审阅和撤销.
- **B. 审阅通过后才执行的改动**: 安装软件, 移动工作区, chezmoi 部署, 桌面设置. 这些会真正写入本机.

第 C 节列出需要你拍板的决策. 第 D 节是不迁移的内容, 第 E 节是回滚方法.

背景见 [todo.md](../todo.md). 工具用法见 [reference_zh-CN.md](reference_zh-CN.md).

## 执行记录 (2026-11-03)

本清单已经审阅并执行. 下文第 0 到 E 节保留为审阅时的计划; 实际执行与计划的差异见下面两张表.

最终决策:

| # | 决策 | 实际执行 |
|---|---|---|
| C1 | 以兼容性和稳定为先 → **(b) gnome-terminal** | 新建 gnome-terminal 配置方案 "Arch Konsole" 并设为默认: 字体 JetBrainsMono Nerd Font Mono Medium Italic 10, 按 Sweet-Ambar-Blue 设置的 16 色调色板, 背景和前景色, 150x40, 光标闪烁, 隐藏菜单栏. Kali 原有的配置方案保留. 仓库规则不变. zellij 0.45.1 (官方 release, 已校验 sha256) 装在 `~/.local/bin`, 配置由仓库同步, `zellij setup --check` 通过 |
| C2 | 其他选择: 保留 Kali 自己的 `user-dirs.dirs` | `platforms.toml` 新增 `[requires] arch = [".config/user-dirs.dirs"]`: Arch 继续部署和备份, Kali 不部署. 已验证 Kali 上 `chezmoi add` 碰到它只会警告, 不会改动源文件. **没有**用 `backup.linux.ignore`, 因为这个清单两台 Linux 共用, 加进去会让 Arch 也不再备份它 |
| C3 | 默认 | 覆盖为 Arch 的 fcitx5 配置, 然后 `fcitx5-remote -r` 重新加载 (fcitx5 退出时会写回内存中的配置) |
| C4 | 默认 (GitHub 用 Kali 自己的密钥, 已登记) | 复制了 4 对服务器密钥 (都没有 passphrase); known_hosts 从 3 行合并为 51 行, 合并前的版本存为 `known_hosts.pre-merge` |
| C5, C6, C7 | 默认 | vboxclient.desktop 照样部署; aria2 不处理; 字体复制到 `~/.local/share/fonts/arch` (55 个文件, 166M) |
| C8 | 其他选择 | dotfile 内容, `dotfiles.toml` 的 ignore 条目和 `packages/aptpkg.txt` 直接提交到 main. 规则改动 (`platforms.toml`, externals) 和文档走 dev → PR |
| C9 | 其他选择: 不合并 zsh 历史 | 没有合并 |
| D | IDA Pro 一起迁移 | 见下表 |

与计划不同的地方, 以及计划之外新增的内容:

| 项目 | 说明 |
|---|---|
| gh | 没有可用的 sudo, 不能添加 GitHub 的 apt 源. 改用官方 release tarball 安装到 `~/.local/bin/gh` (2.103.3, 已按 checksums.txt 校验), man 手册放在 `~/.local/share/man/man1` |
| IDA Pro 9.5 | 从 Arch 复制 `~/.local/share/ida-pro-9.5` (1014M, 与源目录逐字节一致). 复制 `~/.idapro/ida.reg` 和 proccache (其中保存了字体设置和历史记录). `ida-config.json`, `~/.local/bin/idapro` 链接和 `.desktop` 文件中的路径改为本机家目录. 用 `idapyswitch` 把 IDAPython 指向 `/usr/lib/x86_64-linux-gnu/libpython3.14.so.1.0`. 依赖的共享库都已具备, 包括 xcb 平台插件. 批处理模式 (`idat -A`) 验证通过: 许可证有效, IDAPython 3.14.8 可用. 遗留问题: ida.reg 中的许可证路径仍指向 Arch 的家目录, 每次启动都会提示一次, 然后改用安装目录中的许可证文件. 在 GUI 中通过 Help → License manager 重新选择一次许可证文件即可消除 |
| `backup.linux.ignore` | B7 的预览发现两个 Kali 独有的自动启动项会被登记为 linux, 从而也部署到 Arch: `fix-old-genmon-config.desktop` (XFCE) 和 `org.fcitx.Fcitx5.desktop` (KDE Wayland 下 fcitx5 由 KWin 启动). 已把它们加入 ignore |
| `packages/aptpkg.txt` | `apt-mark showmanual` 导出, 229 个包. 工具现在还不会读取它 |
| ncat | 计划漏掉了: `.ssh/config` 中 github.com 的 `ProxyCommand` 需要 `ncat`. Arch 上 ncat 来自 nmap 包, Kali 上要单独安装 `ncat` 包 (需要 sudo, 留给你执行). 在那之前, 推送时临时用 `GIT_SSH_COMMAND='ssh -o ProxyCommand=none'` 直连 |
| `/etc` 备份 | 没有做 (`backup --no-system`, 读取 root 才能读的文件需要 sudo). 留作待办 |
| GNOME | 两个 `switch-input-source` 快捷键 (正向和反向) 都清空了 (Arch 的 fcitx5 配置同时使用 `Super+space` 和 `Shift+Super+space`); kimpanel 已加入 `enabled-extensions`, **注销并重新登录后生效**; 夜灯已开启; 等宽字体改为 JetBrainsMono Nerd Font Mono 10. 没有创建 Templates 等目录 (C2) |
| 收尾 | 删除了 `~/chezmoi_age_key.txt` (删除前确认 `~/.config/chezmoi/key.txt` 内容一致, 权限 600); 删除了 `/tmp` 中的临时目录; `dotfiles audit --apply` 把 `~/.wakatime.cfg` 的权限收紧为 600 |
| 验证 | restore 部署了 61 项, 被覆盖的 16 个文件已备份到 `~/.local/state/dotfiles/pre-apply-20261103-223642`; `chezmoi verify` 和安全检查通过; tags = `kali, debian, gnome`; `idLike` 键名正确; 新开的 zsh (robbyrussell 主题), bash 和登录时的 `.profile` 都没有报错; doctor 只剩 "pacman is required" (apt 还不支持) |

## 0. 本机现状 (只读检查的结果)

| 项目 | 结果 |
|---|---|
| 系统 | Kali Rolling 2026.3, `ID=kali`, `ID_LIKE=debian`, kali-amd64 内核, 实体机 (`systemd-detect-virt` = none) |
| 桌面 | GNOME, Wayland (另装了 XFCE). 没有 plasmashell. 预期 tags: `kali`, `debian`, `gnome` |
| 用户 | 用户名与 Arch 不同 (家目录路径也不同), uid 同为 1000, 登录 shell 已是 zsh |
| 输入法 | **fcitx5 已在运行** (拼音, 不是 ibus), `~/.config/environment.d/fcitx5.conf` 已设好 IM 变量. 没有 kimpanel 扩展 |
| GNOME 设置 | 已有 `caps:swapescape` (和 Arch 的 kxkbrc 一致); `Super+space` 被 GNOME 的 "切换输入源" 占用; 夜灯关闭; 等宽字体为 Fira Code |
| 字体 | 没有 JetBrainsMono Nerd Font, Consolas, Noto CJK. 中文只靠 Droid Sans Fallback |
| chezmoi | v2.73.1 (apt). 还没有 `~/.config/chezmoi` 和 `~/.local/share/chezmoi` |
| 工作区 | `~/dotfiles`, 已 fast-forward 到 `origin/main` (1c0957f) |
| age 私钥 | `~/chezmoi_age_key.txt`, 权限 755, 与仓库公钥匹配 (已用它在临时目录解密验证) |
| Clash Verge | mixed-port 7897, 与 `.gitconfig` / `.zshrc` / VS Code 中的代理端口一致 |
| 缺少的工具 | uv, age, nvim, htop, btm, aria2, just, ripgrep, fd, fzf, eza, bat, pre-commit, git-lfs, go, zellij, gh, wl-clipboard, konsole |
| npm | 全局前缀现在是 `/usr/local`. 部署 `.npmrc` 后改为 `~/.npm-global`, 已经装在 `/usr/local` 的全局包不受影响 |
| Arch 分区 | `@` 挂在 `/mnt/arch`, `@home` 也已挂载. 但 `@home` 被嵌套挂了 3 层, Arch 家目录实际位于 `/mnt/arch/home/<用户>/<用户>/<用户>`. 下文的 "Arch 家目录" 都指这个最内层目录. 只读取, 不写入. 多余的两层可以用 `sudo umount` 卸掉 |

## 0.1 与 Arch 实机的核对结果

用临时配置把 chezmoi 的目标目录指向 Arch 家目录 (标签为 `arch`, `kde`), 运行 `chezmoi status` / `diff` (只读):

- **仓库和 Arch 实机内容一致**. 有内容差异的只有 A 节的 5 处改动, 以及 `.gitconfig` 中 `ida` alias 的路径 (模板按当前机器的家目录渲染, 这是预期行为). 其余差异都只是权限位 (本机 umask 是 002, Arch 是 022).
- **shell**: 实际在用的是 **oh-my-zsh + robbyrussell 主题** (`plugins=(git)`). `~/.oh-my-zsh` 是官方仓库的干净 clone, `custom/` 里只有示例文件. `~/.zim` 存在, 但 `.zshrc` 不加载它, 确认 `.zimrc` 和 `.p10k.zsh` 是遗留文件.
- **终端**: Arch 用的是 **Konsole**. 默认配置方案是 "配置方案 1": 配色 Sweet-Ambar-Blue, 字体 JetBrainsMono Nerd Font Mono 10 Medium Italic, 150x40, 命令 `/usr/bin/zsh`, 光标闪烁, 隐藏菜单栏. **Arch 上没有装 ghostty** (不在软件包清单中, 也没有 `/usr/bin/ghostty`), 它的配置文件只是一份模板.
- **SSH**: `.ssh/config` 引用的 4 个私钥 (webserver6, pwncollege, gpuserver, fjut) 都在 Arch 家目录中.
- **vim**: Arch 上装了 vim-plug (`~/.vim/autoload/plug.vim`), 插件 vim-airline 和 vim-airline-themes 已经下载.
- **字体**: Arch 家目录中没有字体. JetBrainsMono Nerd Font (48 个文件, 118M), Consolas 和微软雅黑 (共 49M) 都在 Arch 的 `/usr/share/fonts/TTF` 中, 来自系统软件包.
- **nvim 配置**: 和 origin/main 同步, 没有本地改动. external 在 Kali 上 clone 后, 结果和 Arch 相同.
- **用户目录**: Arch 上 Templates/Public/Music/Videos 都存在.
- **VS Code 扩展**: 两边都是 91 个, 列表完全相同.
- **aria2**: Arch 上也没有 `/etc/aria2/aria2.session`, 所以这个配置在 Arch 上也一样不能直接用.
- **只在 Arch 上有的用户级工具**: `~/.cargo` (rustup 工具链, 以及 zeroclaw, mdbook, just-lsp), `~/.asdf` (erlang, elixir), uv tools (taskdog, demucs 等), npm 全局包 (cloudflare, openclaw), `~/.local/bin` 中的 claude, cursor-agent, zed, idapro 等. 见第 D 节.
- **zsh 历史**: Arch 上有 12117 行 (extended 格式), Kali 上有 128 行.

## A. 已在工作区完成的改动 (未提交)

```
 M home/.chezmoidata/platforms.toml
 M home/.chezmoiexternal.toml.tmpl
 M home/dot_bash_profile
 M home/dot_bashrc
 M home/dot_profile
 M home/dot_zshenv
 M home/encrypted_private_dot_zshrc.age
?? docs/kali.md
```

这些文件 Arch 和 Kali 共用, **合并后在 Arch 上 apply 也会生效**. 每一项都只在缺少依赖时改变行为, 在 Arch 上的效果和原来一样 (`.profile` 的 PATH 一项除外, 见 A1).

### A1. shell 启动文件: 加上存在性检查

问题: 这 4 个文件都无条件执行 `. "$HOME/.cargo/env"`, `.bashrc` 还无条件加载 `~/.asdf`. Kali 上这两个都不存在.
- 每次启动 zsh/bash 都会报 `no such file or directory`.
- **更严重的是**: Kali 的 `~/.zprofile` 会用 `emulate sh -c '. "$HOME/.profile"'` 加载 `.profile`. 在 POSIX 模式下, `.` 一个不存在的文件会让登录 shell 直接退出, GDM 登录可能因此失败.

| 文件 | 改动 |
|---|---|
| `home/dot_zshenv` | `. "$HOME/.cargo/env"` → `if [ -f "$HOME/.cargo/env" ]; then . "$HOME/.cargo/env"; fi` |
| `home/dot_bash_profile` | 同上 |
| `home/dot_bashrc` | 同上; asdf 两行改为 `if [ -f "$HOME/.asdf/asdf.sh" ]; then ... fi`, 顺便给 `$HOME` 加上引号 |
| `home/dot_profile` | 同上, 并加上注释; **新增**: `~/.local/bin` 存在且不在 PATH 中时把它加进 PATH (这是 Kali 原版 `.profile` 的行为, 覆盖后不想丢; 在 Arch 上也会生效, 只会让图形会话多一个 PATH 项) |

已用 `sh -n` / `bash -n` 做过语法检查.

### A2. `.zshrc` (加密文件): 给 `go` 调用加上检查

- 第 111 行 `export PATH="$PATH:$(go env GOPATH)/bin"` → `command -v go >/dev/null 2>&1 && export PATH="$PATH:$(go env GOPATH)/bin"`.
- 原因: 没装 go 时, 每次打开终端都会报 `command not found: go`, 还会往 PATH 里加一个 `/bin`. (第 B1 步也会装 go, 但不应该依赖这一点.)
- 方法: 在临时目录解密 → 改这一行 → `zsh -n` 检查 → `chezmoi encrypt` 重新加密 → 再解密, 确认和修改后的明文一致 (257 行). 只改了这一行, 其余内容 (包括密钥) 不变. 因为重新加密, 密文整体都会变化.
- 其他行在 Kali 上不需要改: conda/mamba 块在找不到 miniforge 时不报错 (`2> /dev/null`, 然后退回到 alias); pnpm, bun, pixi, vcpkg 只是往 PATH 里加了不存在的目录.

### A3. oh-my-zsh 改为 chezmoi external

- `home/.chezmoiexternal.toml.tmpl` 新增 `.oh-my-zsh`: `type = "git-repo"`, 地址为 `https://github.com/ohmyzsh/ohmyzsh.git`, `refreshPeriod = "168h"`, 只做 `--ff-only` pull.
- `home/.chezmoidata/platforms.toml` 的 `linux` 中登记 `.oh-my-zsh` (按字母顺序). Windows 上会被忽略.
- 原因: `.zshrc` 的核心是 `source $ZSH/oh-my-zsh.sh`, 但仓库原来没有管理 oh-my-zsh. 新机器 (包括这次的 Kali) 部署后, zsh 会直接报错. 做成 external 后, restore 的 `externals` 步骤会自动 clone.
- **对 Arch 的影响**: 已核实 Arch 的 `~/.oh-my-zsh` 是官方仓库的干净 clone (origin 为 `https://github.com/ohmyzsh/ohmyzsh.git`, 没有本地改动). chezmoi 每 7 天会在里面执行一次 `git pull --ff-only`, 不会影响 `custom/`.
- 附带效果: restore 的 `hooks` 步骤会把 external 中 GitHub 仓库的 push 地址改成 SSH, 也包括 ohmyzsh. 只影响 push, 没有实际影响.

### A4. 验证结果

| 检查 | 结果 |
|---|---|
| `guard tree` (加密, 敏感文件, 密钥扫描, 平台归属, 家目录路径) | 通过 |
| `chez.check_platforms()` (每个目标恰好有一个归属) | 无问题 |
| Kali 视角的 externals | `.config/nvim`, `.oh-my-zsh` |
| Windows 视角的 externals | 无 |

以上检查都用临时目录中的 chezmoi 配置运行, 没有写入 `~/.config/chezmoi`.

## B. 审阅通过后执行的改动

按顺序执行. 每一步都会先预览, 再执行.

### B1. apt 安装软件 (需要 sudo)

**必需** (仓库中的配置直接依赖它们):

```bash
sudo apt install neovim ripgrep fd-find fzf eza bat htop btm aria2 just age pre-commit \
    git-lfs golang-go wl-clipboard fonts-noto-cjk gnome-shell-extension-kimpanel
```

| 包 | 原因 |
|---|---|
| neovim | `EDITOR=nvim`, zellij 的 `scrollback_editor`, nvim 配置 (external) |
| ripgrep, fd-find, fzf | nvim 配置中的搜索插件; Arch 的常用命令 |
| eza, bat | Arch 的常用命令 |
| htop, btm | 仓库中有 `htoprc`, `bottom.toml`; tmux 启动脚本会运行 htop |
| aria2 | 仓库中有 `aria2.conf` |
| just, pre-commit | 仓库的 `Justfile` 和提交检查 |
| age | 命令行解密 (chezmoi 自带 age 实现, 这个包只是方便手动操作) |
| git-lfs | `.gitconfig` 的 `[filter "lfs"]` 设置了 `required = true`, 没有 git-lfs 时, 用到 LFS 的仓库 checkout 会失败 |
| golang-go | Arch 上装了 go, `.zshrc` 会把 GOPATH/bin 加进 PATH |
| wl-clipboard | Wayland 下 zellij 和 nvim 的剪贴板 |
| fonts-noto-cjk | 现在中文只靠 Droid Sans Fallback; 对应 Arch 的 noto-fonts-cjk |
| gnome-shell-extension-kimpanel | 在 GNOME Wayland 下正确显示 fcitx5 的候选框 (todo 第 4 节第 6 步) |
| konsole (C1 选 (a) 时) | Arch 上的终端 |

**可选** (和 Arch 的软件包清单对齐, 配置不依赖它们, 不需要可以删掉):

```bash
sudo apt install tealdeer grc lnav tokei autojump chafa lolcat cmatrix sl unar \
    fcitx5-material-color fonts-wqy-zenhei syncthing keepassxc
```

Debian 上 `fd` 和 `bat` 的命令名是 `fdfind` 和 `batcat`. 为了和 Arch 保持一致, 会建两个符号链接:
`ln -s /usr/bin/fdfind ~/.local/bin/fd`, `ln -s /usr/bin/batcat ~/.local/bin/bat`.

### B2. apt 里没有的工具 (写入 `~/.local`, 不需要 sudo, gh 除外)

| 工具 | 方式 | 写入位置 |
|---|---|---|
| uv | `pipx install uv` (和 bootstrap.sh 的做法相同) | `~/.local/bin/uv` |
| zellij | GitHub release `zellij-x86_64-unknown-linux-musl.tar.gz`, 下载到新建的空临时目录, 解压出单个二进制文件 | `~/.local/bin/zellij` |
| gh | GitHub 官方 apt 源 (todo 第 4 节第 5 步): 写入 keyring `/etc/apt/keyrings/githubcli-archive-keyring.gpg` 和 `/etc/apt/sources.list.d/github-cli.list`, 然后 `apt install gh`. **需要 sudo, 会新增一个第三方 apt 源** | `/usr/bin/gh` |
| ghostty | **不装**. Arch 上也没有装 (见 0.1). 配置文件照样部署, 不会产生影响 | — |

字体不再从网上下载, 改为在 B3.5 中从 Arch 分区复制.

### B3.5. 从 Arch 家目录和分区复制 (只读取 Arch, 写入 Kali 家目录)

这些内容不在仓库中 (私钥和字体本来就不应该进入公开仓库), 所以直接从 Arch 复制. 在 B4 之前执行, 这样 restore 的 `ssh` 步骤会显示已完成.

| 内容 | 来源 (Arch) | 目标 (Kali) | 说明 |
|---|---|---|---|
| SSH 私钥和公钥 | `~/.ssh/{webserver6,pwncollege,gpuserver,fjut}{,.pub}` | `~/.ssh/` | 私钥 600, 公钥 644. **不复制 `id_ed25519`**, Kali 继续用自己的密钥 (见 C4) |
| known_hosts | `~/.ssh/known_hosts` | 合并进 `~/.ssh/known_hosts` | 把 Arch 中有, Kali 中没有的行追加进去, 不删除 Kali 原有的行. 这样连接这些服务器时不会再提示确认主机指纹 |
| vim 插件 | `~/.vim/` (vim-plug 和 airline 两个插件) | `~/.vim/` | 让部署的 `.vimrc` 能直接使用 |
| 字体 | `/usr/share/fonts/TTF/JetBrainsMonoNerdFont*`, `Consolas-*.ttf`, `msyh*.ttc` | `~/.local/share/fonts/arch/` | 约 167M. 然后执行 `fc-cache -f`. 对应 VS Code (`Consolas, JetBrainsMono Nerd Font Mono`) 和 KDE (微软雅黑) 中用到的字体 |
| zsh 历史 (可选, 见 C9) | `~/.zsh_history` | 合并进 `~/.zsh_history` | 把 Arch 的历史放在前面, Kali 的接在后面, 两边都是 extended 格式. 权限 600 |

### B3. 把工作区移动到 chezmoi 源目录

chezmoi 默认的源目录是 `~/.local/share/chezmoi`. 生成的配置中没有 `sourceDir`, 而 `dotfiles restore` 会检查 chezmoi 的 source-path 是否等于仓库所在位置, 所以仓库必须放在那里.

```bash
mv ~/dotfiles ~/.local/share/chezmoi
ln -s ~/.local/share/chezmoi ~/dotfiles      # VS Code 中打开的路径继续可用
```

工具通过 `Path(__file__).resolve()` 定位仓库, 会解析符号链接, 所以从 `~/dotfiles` 运行也能正确识别. 第 A 节中未提交的改动会随目录一起移动.

### B4. `dotfiles restore` (写入 chezmoi 配置和家目录)

先运行 `uv run dotfiles restore` 查看计划, 再执行:

```bash
cd ~/.local/share/chezmoi
uv run dotfiles restore --apply --key-file ~/chezmoi_age_key.txt
```

B3.5 已经把私钥放好了, 所以 `ssh` 步骤会显示已完成, 不需要再 `--skip ssh`. 如果 C4 选择不复制私钥, 就加上 `--skip ssh`.

| 步骤 | 预期 | 写入 |
|---|---|---|
| key | 从 `~/chezmoi_age_key.txt` 复制 | `~/.config/chezmoi/key.txt` (600, 目录 700) |
| config | `chezmoi init` 渲染模板; tags = `kali`, `debian`, `gnome` | `~/.config/chezmoi/chezmoi.toml` |
| dotfiles | 先演练解密 → 把将被覆盖的文件备份到 `~/.local/state/dotfiles/pre-apply-<时间>/` → `chezmoi apply` | 见下表 |
| system | 跳过: 没有 `system/kali` | — |
| packages | 跳过: 不是 Arch | — |
| externals | clone nvim 配置和 oh-my-zsh | `~/.config/nvim`, `~/.oh-my-zsh` |
| ssh | 已完成 (B3.5 复制了私钥) | — |
| shell | 已是 zsh, 不需要改 | — |
| hooks | 在仓库中执行 `pre-commit install`; 仓库, nvim, ohmyzsh 的 push 地址改为 SSH | `.git/hooks/`, 各仓库的 `.git/config` |
| verify | `chezmoi verify`, 安全检查, 权限审计 | — |

**dotfiles 步骤会部署的目标** (已在临时配置下用 `chezmoi status` 确认. KDE 相关目标因为没有 `kde` 标签, 不会部署):

新建 (Kali 上原来没有):

| 目标 | 说明 |
|---|---|
| `.zshenv` `.bash_profile` | A1 中改过的版本 |
| `.zimrc` `.p10k.zsh` | Arch 上的遗留文件, 不会被加载, 没有影响 |
| `.vimrc` | vim-plug 配置; B3.5 已经复制了 `~/.vim` |
| `.tmux.conf`, `tmux/tmux-startup.sh` | `.zshrc` 中 `alias tmux='~/tmux/tmux-startup.sh'`; 脚本会 cd 到 `~/vg101/...`, 目录不存在时只是那个 pane 里报错 |
| `.tool-versions` | asdf 的版本文件 (erlang/elixir); 不装 asdf 时没有影响 |
| `.gitconfig` | 用户名 vg101, 代理 127.0.0.1:7897, LFS 设置, `ida` alias (路径在 Kali 上不存在, 不用就没有影响) |
| `.ssh/config` (加密) | github.com 以及 5 台服务器; 引用的 4 个私钥由 B3.5 从 Arch 复制 |
| `.npmrc` (加密, 600) | `prefix=${HOME}/.npm-global` |
| `.pypirc` (加密, 600) | testpypi |
| `.agents/ccs_config.sql` (加密) | CC Switch 配置 |
| `.aria2/aria2.conf` | 其中 `input-file`/`save-session` 指向 `/etc/aria2/aria2.session`, Arch 上也没有这个文件; 用 aria2c 时会报错, 见 C6 |
| `.config/Cursor/User/{settings,keybindings}.json` | 本机没装 Cursor, 只是放着 |
| `.config/bottom/bottom.toml`, `.config/htop/htoprc` | |
| `.config/environment.d/im.conf` | 与已有的 `fcitx5.conf` 内容重复, 多了一行 `GLFW_IM_MODULE=ibus`; 两个文件同时存在没有冲突 |
| `.config/fontconfig/fonts.conf` | 合成斜体和粗体, 次像素渲染, `~/.local/share/fonts` |
| `.config/gh/config.yml`, `.config/gh/hosts.yml` (加密) | gh 登录状态 (账号 virtualguard101, token 来自 Arch) |
| `.config/ghostty/config` | 只有模板注释, 相当于默认设置; Arch 和 Kali 都没装 ghostty |
| `.config/konsolerc`, `.local/share/konsole/*` | **只有在 C1 选 (a) 时才部署**: Konsole 配置方案 "配置方案 1" 和 Sweet 系列配色 |
| `.config/zellij/config.kdl`, `layouts/work.kdl` | 主题 tokyo-night-dark, 默认布局 work |
| `.config/systemd/user/{taskdog-server,zeroclaw}.service` | 只放置单元文件, **不会 enable**, 本机也没有对应程序 |
| `.config/autostart/vboxclient.desktop` | 指向 `/usr/bin/VBoxClient-all`, 本机没有这个程序, 登录时会静默失败, 见 C5 |
| `.local/share/fcitx5/rime/default.custom.yaml` | rime 没有启用, 只是放着 |

覆盖 (Kali 上已有, 旧文件会先备份到 `pre-apply-*`):

| 目标 | 改动 |
|---|---|
| `.zshrc` | Kali 自带的 zsh 配置 → Arch 的 oh-my-zsh 配置. 会失去 Kali 自带的提示符; 带有代理变量, 别名, 以及 conda/pnpm/bun 的 PATH |
| `.bashrc` | Kali 自带的 → Arch 的精简版 (`EDITOR=nvim`, PATH, A1 中加了检查的部分) |
| `.profile` | Kali 自带的 → A1 中的新版 (保留 `~/.local/bin` 进 PATH; 不再自动加载 `.bashrc`, 因为 `.bash_profile` 会负责) |
| `.config/Code/User/settings.json`, `keybindings.json` | 各只有 2 行不同 (`python.defaultInterpreterPath`, `leetcode-problem-rating.useVscodeNode` 等), 基本一致. 注意: 设置中有几处写死了 Arch 的家目录 (kubectl/helm 路径, leetcode 工作区), 在 Kali 上找不到. Kali 现在的版本里也有这些路径, 这次不处理 (加密文件不经过家目录路径检查, 见 todo 第 7 节) |
| `.config/fcitx5/config`, `profile`, `conf/*.conf` | Kali 当前的 → Arch 的. 切换键改为 `Super+space`, `Shift_L` 作为备用切换键; 输入法组加上 `keyboard-jp`; 拼音开启云拼音; 关闭全角标点; 新增 `classicui.conf` (主题 `plasma` 在本机不存在, 会退回默认主题). 见 C3 |
| `.config/user-dirs.dirs` | 恢复 Templates/Public/Music/Videos, `XDG_PROJECTS_DIR` 改为 `$HOME/`. 见 C2 |
| `.config/autostart/Clash Verge.desktop` | 只有 Comment 一行不同 |

权限变化 (只改 mode, 内容不变): `~/.config` 改为 700 (源中是 `private_dot_config`, 和 Arch 一致); `~/.config/autostart`, `~/.local/share` 按本机 umask 002 变为 775.

### B5. 部署后调整 GNOME (一次性, 不进仓库)

这些对应 Arch 上 KDE 的设置. KDE 配置文件不能用在 GNOME 上, 所以用 gsettings 手动设置:

| 命令 | 对应的 Arch 设置 |
|---|---|
| `gsettings set org.gnome.desktop.wm.keybindings switch-input-source "[]"` 和 `... switch-input-source-backward "[]"` | 把 `Super+space` 让给 fcitx5 (Arch 的 fcitx5 config 用它切换输入法). 不改的话, GNOME 会先截获这个组合键 |
| `gnome-extensions enable kimpanel@kde.org` (注销并重新登录后生效) | Wayland 下 fcitx5 候选框的显示 |
| `gsettings set org.gnome.settings-daemon.plugins.color night-light-enabled true` | kwinrc `[NightColor] Active=true` |
| `gsettings set org.gnome.desktop.interface monospace-font-name 'JetBrainsMono Nerd Font Mono 10'` | Konsole 配置方案的字体 (JetBrainsMono Nerd Font Mono 10) |
| (C1 选 (a) 时) 在 GNOME 中设置 `Ctrl+Alt+T` 打开 konsole: 通过 `org.gnome.settings-daemon.plugins.media-keys custom-keybindings` 新增一个自定义快捷键 | Arch 上的终端是 Konsole |
| `mkdir -p ~/Templates ~/Public ~/Music ~/Videos` | 让 B4 部署的 `user-dirs.dirs` 保持不变, 见 C2 |
| 不改 | `caps:swapescape` 已经设置好了 |

### B6. 收尾

1. 确认 `~/.config/chezmoi/key.txt` 存在, 且 `uv run dotfiles doctor` 通过后, 删除 `~/chezmoi_age_key.txt` (todo 第 4 节第 3 步).
2. 删除这次检查留在 `/tmp` 中的临时目录. 里面有解密后的 `.zshrc` (权限 700).
3. 验证 (todo 第 4 节第 4 步):
   - `uv run dotfiles doctor`
   - `chezmoi data | grep -A3 osRelease`, 确认键名是 `idLike`
   - `uv run dotfiles check`
   - `uv run dotfiles apply`, 应当没有待部署项
   - 打开一个新的 zsh, 确认没有报错.

### B7. 记录 Kali 的系统信息 (只做预览, 你确认后再写入)

- `uv run dotfiles backup -n`: 预览 Kali 上新出现的备份候选, 以及 `system/kali/etc`. 候选清单两台 Linux 机器共用 (todo 第 6 节), 所以只预览, 把结果汇报给你.
- `packages/aptpkg.txt`: 用 `apt-mark showmanual` 导出 (B1 之后约 230 个). 现在的工具还不会读取它 (todo 第 8 节), 只作为记录保存.

## C. 需要你确认的决策

每一项都写出了默认做法. 如果你没有特别说明, 就按默认做法执行.

| # | 问题 | 默认做法 | 其他选择 |
|---|---|---|---|
| C1 | 终端: Arch 上用的是 Konsole ("配置方案 1" + Sweet-Ambar-Blue 配色). 但 `konsolerc` 和 `.local/share/konsole` 现在挂在 `kde` 标签下, Kali 上不会部署 | **(a) 安装 Konsole 并部署它的配置** (最接近 Arch). 需要改规则: 在 `.chezmoi.toml.tmpl` 中, 能找到 `konsole` 时加上 `konsole` 标签; 在 `platforms.toml` 中把 `.config/konsolerc` 和 `.local/share/konsole` 从 `[requires] kde` 移到新的 `[requires] konsole`. apt 安装 `konsole` 会带上一部分 KDE Frameworks 库. **对 Arch 的影响**: Arch 上装了 konsole, 所以照样会部署; 但合并后要在 Arch 上重新运行一次 `chezmoi init` (doctor 会提示), 在那之前 Arch 的配置中没有 `konsole` 标签, 这两个目标会被暂时忽略 | (b) 继续用 gnome-terminal, 手动设置字体和深色配色, 做到大致相似, 不改仓库; (c) 不处理 |
| C2 | `user-dirs.dirs`: Kali 现在的 Templates/Public/Music/Videos 指向 `$HOME/`, `XDG_PROJECTS_DIR=$HOME/Projects` | **采用 Arch 的版本**, 并创建这 4 个目录 (Arch 上这 4 个目录都存在. 不创建的话, `xdg-user-dirs-update` 登录时会改回去, 以后在 Kali 上 backup 时又会覆盖 Arch 的版本). `~/Projects` 目录保留, 只是不再是 XDG 目录 | 在 Kali 上 `dotfiles config add backup.linux.ignore .config/user-dirs.dirs`, 并且不部署这个文件 (需要再加一个标签规则) |
| C3 | fcitx5: 覆盖成 Arch 的配置 (`Super+space` 切换, 云拼音, 关闭全角标点, 加上日语键盘布局) | **覆盖**, 并按 B5 释放 `Super+space` | 保留 Kali 当前的配置: 从部署中排除 `.config/fcitx5` |
| C4 | SSH: `.ssh/config` 引用的 4 个私钥在 Kali 上不存在, 但 Arch 上有 | **从 Arch 复制这 4 个私钥** (B3.5). `id_ed25519` 保留 Kali 自己的, 需要另外登记到 GitHub (todo 第 4 节第 7 步) | 把 Arch 的 `id_ed25519` 也复制过来, 覆盖 Kali 的, 这样 GitHub 上不用再登记; 或者不复制, 生成 4 个新密钥 (restore 的 ssh 步骤会交互询问 passphrase), 再逐个登记到服务器 |
| C5 | `vboxclient.desktop`: 两个系统都是实体机, 它应该是 virtualbox-guest-utils 留下的 | **照样部署** (没有影响). 是否删除留给后续处理 (todo 第 8 节) | 现在就从仓库中删掉 (Arch 上也会一起消失) |
| C6 | `aria2.conf` 指向不存在的 session 文件 `/etc/aria2/aria2.session` | **不处理** (Arch 上也是这样). vim 的问题已由 B3.5 复制 `~/.vim` 解决 | 创建 session 文件, 或者把配置改到家目录下 (Arch 上也会一起生效) |
| C7 | Consolas 和微软雅黑 (Arch 上来自 ttf-ms-win11-auto, 是从 Windows 镜像中提取的字体) | **从 Arch 的 `/usr/share/fonts/TTF` 复制到 `~/.local/share/fonts/arch/`** (B3.5), 只给本用户使用 | 不复制: VS Code 字体列表里的 Consolas 会退回到 JetBrainsMono Nerd Font, 中文会退回到 Noto CJK |
| C8 | 提交方式: A 节中有规则改动 (`platforms.toml`, externals; C1 (a) 还会改 `.chezmoi.toml.tmpl`) | **先不提交**. 你审阅后再决定 | 按 workflows 第 2 节, 规则改动走 `dev` → PR → `main`; 只改 dotfile 内容的话可以直接提交到 `main` |
| C9 | zsh 历史: 是否把 Arch 的 12117 行合并进 Kali | **合并** (B3.5): 上箭头和 Ctrl+R 能搜到 Arch 上用过的命令. 历史里可能有命令行中出现过的 token, 但文件只留在本机 (600), 不进仓库 | 不合并 |

## D. 不迁移的内容

| 内容 | 原因 |
|---|---|
| KDE 配置 (`kdeglobals`, `kwinrc`, `kglobalshortcutsrc`, `kxkbrc`, `konsolerc`, `dolphinrc`, `mimeapps.list`, konsole 配色, `kwallet_auto_unlock.service`) | 需要 `kde` 标签, 本机没有. 能对应到 GNOME 的部分见 B5 |
| `system/arch/etc`, `packages/archpkg.txt` | 只属于 Arch. restore 会自动跳过 |
| asdf (erlang/elixir), pixi, vcpkg, bun, pnpm, rustup/cargo (含 zeroclaw, mdbook, just-lsp), uv tools (taskdog, demucs 等), npm 全局包 (cloudflare, openclaw), `~/.local/bin` 中的二进制文件 (cursor-agent, zed, idapro 等), IDA Pro | 这些是程序本身, 不是配置. 用到时再装. A1 和 A2 保证缺少它们时 shell 不会报错. Arch 上并没有 miniforge, `.zshrc` 中的 conda/mamba 块在 Arch 上同样不起作用. 不直接从 Arch 复制二进制文件: 有些依赖 Arch 的库版本, 有些是安装器管理的 |
| Arch 上的图形应用 (Obsidian, Discord, Telegram, WPS, 飞书, Steam 等) | 不在这次范围内. 需要的话另外列清单 |

## E. 回滚方法

| 改动 | 回滚 |
|---|---|
| A 节 (仓库) | `git checkout -- home/ && rm docs/kali.md` |
| B3 (移动工作区) | `rm ~/dotfiles && mv ~/.local/share/chezmoi ~/dotfiles` |
| B4 (被覆盖的文件) | 从 `~/.local/state/dotfiles/pre-apply-<时间>/` 中复制回来 |
| B4 (新建的文件) | 删除对应文件. `chezmoi managed` 会列出全部目标 |
| chezmoi 配置 | `rm -r ~/.config/chezmoi` (私钥先另外保存) |
| B5 (GNOME) | `gsettings reset <schema> <key>` |
| B1, B2 (软件) | `sudo apt remove ...`; 删除 `~/.local/bin` 中对应的文件, 以及 gh 的 apt 源和 keyring |
| B3.5 (从 Arch 复制) | 删除复制过来的 4 对密钥, `~/.vim`, `~/.local/share/fonts/arch` (然后 `fc-cache -f`). known_hosts 和 zsh 历史在合并前会先备份为 `*.pre-merge` |

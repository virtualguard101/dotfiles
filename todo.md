# todo: Linux 双系统同步的收尾

本文件是给人和 AI agent 读的上下文，记录两台 Linux 共用的同步规则、当前的备份状态和剩下的待办。第 6 节的待办都处理完后，先把第 4、5 节的通用规则移进 workflows / reference，再删除本文件。

- Kali 的核心上下文和注意事项：[docs/kali.md](docs/kali.md)。
- Arch 的对应文档：`docs/arch.md`（待写，见 6.1）。
- 工具的结构：[docs/architecture_zh-CN.md](docs/architecture_zh-CN.md)。
- 命令细节：[docs/reference_zh-CN.md](docs/reference_zh-CN.md)。
- 分支用法：[docs/workflows_zh-CN.md](docs/workflows_zh-CN.md)。第 3 节推荐用 worktree，已经不适用，以第 4 节为准，见 6.2。

## 1. 背景

- 仓库是 chezmoi 的源目录（`~/.local/share/chezmoi`，`.chezmoiroot` 指向 `home/`）。附带一个由 uv 管理的 Python 工具 `dotfiles`，负责 doctor / restore / apply / backup / clean。
- 仓库是**公开**的：敏感文件都以 `encrypted_*.age` 形式保存。age 私钥不在仓库中，需要离线保存；提交时的检查会拦下明文密钥。
- 同一台实体机上有两个 Linux 系统：Arch + KDE（主力），Kali + GNOME（工作，主要用于安全研究，**以稳定为先**）。两者用户名不同，uid 相同。第 2 节的机制用来区分这两个系统。

## 2. 区分两个系统的机制

| 机制 | 作用 | 位置 |
|---|---|---|
| 平台归属 `[platforms]` | 每个目标只在 shared / linux / windows 中的一个平台部署 | `home/.chezmoidata/platforms.toml` |
| 自动登记 | `dotfiles backup` 把没有归属的目标登记到当前平台；提交检查 `no-platform-owner` 会拦下没有归属的目标 | `cli/backup.py`，`utils/common/guard/checks.py` |
| 环境标签 `[requires]` | 目标只部署到带有对应标签的机器。标签在 `chezmoi init` 时检测：os-release 的 ID / ID_LIKE，能否找到 plasmashell（`kde`）/ gnome-shell（`gnome`），以及程序标签 | `platforms.toml`，`home/.chezmoi.toml.tmpl` |
| `/etc` 按发行版分目录 | `system/<ID>/etc`，restore 只使用本机发行版的目录 | `cli/etc.py` |

两台机器的标签：Arch 是 `arch, kde, taskdog, zeroclaw`，Kali 是 `kali, debian, gnome`。目前的 `[requires]`：

- `kde`：KDE 和 Konsole 的配置。
- `arch`：`.config/user-dirs.dirs`。Kali 保留自己的版本。
- `gnome`：`.config/autostart/org.fcitx.Fcitx5.desktop`。GNOME 靠它启动 fcitx5，并设置 `LANGUAGE=zh_CN:en_US`，见 kali.md 4.1。
- `zeroclaw`、`taskdog`（程序标签）：对应的两个用户单元。只有 `~/.cargo/bin/zeroclaw`、`~/.local/bin/taskdog-server` 存在时才有这两个标签。

## 3. 当前状态（2026-11-04）

| 项目 | Arch | Kali |
|---|---|---|
| 仓库检出 | 停在 3add77c，**还没有拉取**之后的提交，见 6.1 | 和 `origin/main` 同步 |
| 家目录 | 修剪（0.5.1）已部署；之后的 `notifications.conf` 改动还没有部署 | `chezmoi status` 为空 |
| `/etc` 备份 | `system/arch/etc` | `system/kali/etc`（03bf395 首次备份），内容见 kali.md 第 3 节 |
| 软件包清单 | `packages/archpkg.txt`（虚拟机工具和 rime 已卸载） | `packages/aptpkg.txt`（230 个包） |
| 工具版本 | 0.5.2（拉取后变为 0.5.3） | 0.5.3 |

这次之后 main 上影响 Arch 的改动，`scripts/arch.sh` 都会处理：

- 0.5.3：apply 的演练在本地有改动的目标上不再失败。
- `gnome` 标签：Arch 不部署对应的文件。
- zellij 配置改为模板：Arch 上渲染结果不变。
- `notifications.conf` 删掉了 rime 的条目。
- `etc.exclude` 新增 4 个 apt 文件：Arch 上不存在这些文件。

## 4. 日常同步规则

- **apply 前先预览**：`git pull`，`uv run dotfiles apply` 预览，再 `uv run dotfiles apply --apply` 部署。被覆盖或删除的文件会先备份到 `~/.local/state/dotfiles/pre-apply-*`。
- **备份前先预览**：`uv run dotfiles backup -n`。`backup.linux` 的候选清单（`dotfiles.toml`）两台机器共用，一台上新出现的候选也会部署到另一台。不想要的条目用 `dotfiles config add backup.linux.ignore <路径>` 排除，或者给它加上标签。
- **通用文件两台机器共用同一个源文件**：一台上 re-add 的改动，会在另一台 apply 时覆盖。内容需要不同的文件，要么改成模板（按 `.chezmoi.osRelease.id` 或 `.tags` 区分），要么用标签分开。
- **`/etc`**：backup 只写入本机发行版的 `system/<ID>/etc`。`etc.extra` / `etc.secret` / `etc.exclude` 两边共用，排除一条路径之前，先确认另一台机器上没有同名文件。读取 `/etc` 需要 sudo，不备份时加 `--no-system`。
- **分支**：日常备份直接提交到 `main`。工具或规则的改动（`platforms.toml`、`[requires]`、externals、`src/`）在主检出中 `git switch dev`，提交并推送，再切回 `main`，用 `git merge --no-ff dev` 合并，不再另开 worktree。停在 `dev` 上时，不要 `apply --apply` 或做日常 backup，因为主检出就是 chezmoi 生效中的源目录。
- **提交前跑测试**：`uv run pytest`，要检查它的退出码。不要接在 `| tail` 后面再用 `&&`，那样判断的是 `tail` 的退出码，失败会被吞掉。

## 5. 常见陷阱

Kali 专用的陷阱（gnome-terminal 和 OSC 52、fcitx5 的语言、`emulate sh`、umask 002）见 kali.md 第 4 节。

- 不要在配置文件中写 `$HOME`，除非确认那个程序会展开它。例如 zellij 的 `layout_dir` 不会展开，而 zsh、systemd 的 `%h`、npm 的 `${HOME}` 会。都不行时改用模板 `{{ .chezmoi.homeDir }}`。
- shell 启动文件中的 `.` / `source` 都要先检查文件是否存在。两台机器装的工具不同，而且失败的 `.` 会中止 POSIX 登录 shell。
- `chezmoi re-add` 会把本地文件的权限写回源状态。某个程序把文件改写成 644 后直接 re-add，源文件会丢掉 `private_`。用 `dotfiles backup`，它会先收紧权限；或者先 `chmod 600` 再 re-add。
- `chezmoi diff` 输出很长时，在 pager 中按 q 退出会让 chezmoi 卡住。用 `--no-pager` 加 `| less -R`，或者用 `dotfiles apply` 预览。
- 由模板生成的文件，re-add 不会处理。需要手动把改动合并进 `.tmpl`（见 reference 第 6.3 节）。
- 加密文件的内容不经过家目录路径检查。在加密文件里写死路径不会被发现，以前的 `.zshrc` 和 VS Code 的 `settings.json` 都出现过这种情况。
- 在 zsh 中，把整条命令放进一个变量（`C="chezmoi --config ..."; $C ...`）不会按空格拆分。改用函数，或者 `${=C}`。
- 程序运行时会写回自己的配置（例如 fcitx5）。先退出它再 apply，或者 apply 之后让它重新加载。列表型的值写回时顺序不固定，尽量只保留需要的项。
- `chezmoi apply` / `dotfiles apply` 的 diff 会把明文目标中的内容打印到终端。不要把 token 写进明文目标，`.zshrc` 的 token 已拆到加密的 `secrets.zsh`。
- `.chezmoiignore` 优先于 `.chezmoiremove`：被标签或平台忽略的目标，不会被 `.chezmoiremove` 删除，要在那台机器上手动移走。

## 6. 待办

### 6.1 Arch 侧

- [ ] **拉取并部署**：在 Arch 的终端里运行 `bash scripts/arch.sh` 预览，再运行 `bash scripts/arch.sh --apply`。脚本会依次处理：
  - pull；
  - 检查标签（应当没有 `gnome`）；
  - apply 后让 fcitx5 重新加载；
  - 检查修剪留下的软件包（已卸载，会跳过）；
  - backup 并推送；
  - doctor。
- [ ] **确认 fcitx5 的写回**：Arch 下次注销或重启后，`chezmoi status` 中应当没有 `.config/fcitx5` 的条目。arch.sh 用的是 `fcitx5-remote -r`，这个做法在 Arch 上还没有验证过；Kali 上验证过的是"退出 → apply → 启动"。
- [ ] **编写 `docs/arch.md`**：参照 [docs/kali.md](docs/kali.md) 的结构，记录 Arch 侧的核心上下文和注意事项。最好在 Arch 上写，这样可以直接核对实机。也可以从 Kali 只读查看 `/mnt/arch`，但看不到运行时的状态。至少包括：
  - 本机概况：KDE Plasma Wayland；标签 `arch, kde, taskdog, zeroclaw`；umask 022；系统 locale 是 `en_US.UTF-8`，KDE 会话通过 `plasma-localerc` 设为中文（`LANGUAGE=zh_CN:en_US`）；终端是 Konsole（"配置方案 1" + Sweet-Ambar-Blue）；sudo 和 gh 是否可用。
  - 只在 Arch 上部署的内容：`[requires] kde` / `arch` / 程序标签对应的目标，`system/arch/etc` 的主要条目（sddm、mkinitcpio、pacman hooks、timeshift、docker、mihomo 等），`archpkg.txt`。
  - 与 Kali 不同、需要特别处理的地方：fcitx5 由 KWin 启动（不需要自启动项），zellij 在 Konsole 中用默认的 OSC 52 复制（写的时候核实确实可用），`etc.extra` 中为 Arch 列的文件在 Kali 上会被收进冗余（见 6.2）。
  - 不在仓库中的本机状态：AUR 包、`ttf-ms-win11-auto` 字体、`/usr/local/bin/git-xet`、Clash Verge / mihomo、IDA Pro 原始安装、`~/.cargo` / uv tools / npm 全局包，以及 `zeroclaw`、`taskdog` 两个服务所依赖的程序。
  - 日常操作、备份和回滚的位置（`pre-apply-20261103-233128`、`pre-apply-20261104-002449`、`pre-apply-20261104-010002`，`~/.local/state/dotfiles/etc-review`）。
  - 未决定的事项：`~/.local/share/mamba`（mamba 的根目录，已经没有程序加载它）。

### 6.2 工具和规则（走 dev）

- [ ] **`/etc` 备份的重复加密**：Kali 上第二次 backup（ce6efa3）把内容没有变化的 `crypttab`、`sudoers`、`sudoers.d/{kali-grant-root,ospd-openvas}` 重新加密了一遍。`etc.py` 的 `unchanged()` 靠 `chezmoi status` 判断，推测是权限位不一致：0440 用 `readonly_` 表示不出来，chezmoi 会算成 0444；umask 002 又让 0644 的 `crypttab` 被算成 0664。同样加密保存的 WiFi 文件（0600）没有重复加密，符合这个推测。修复思路：只比较解密后的内容，权限另外比较；或者在暂存实例中固定 `umask = 0o022`。Arch 的 `sudoers` 也是 0440，在 Arch 上用 `backup -n` 核对是否有同样的问题。
- [ ] **`/etc` 备份跳过软件包原样的文件**：`extra` / `secret` 中直接列出的文件，如果和软件包里的版本一致（pacman / dpkg 的 md5 相同），就跳过；或者支持按发行版的 `exclude`。现在 Kali 会收入 5 个这样的文件（kali.md 第 3 节）。这会改变 `system/arch` 的内容，先在 Arch 上 `backup -n` 核对。
- [ ] **更新 workflows 第 3 节**：改为"在主检出中切换到 dev"，并说明停在 dev 上时不能 apply（中英文两份）。
- [ ] 想在 Kali 上换回 Konsole 时：新增 `konsole` 标签（按能否找到 konsole 检测），把 `.config/konsolerc` 和 `.local/share/konsole` 从 `kde` 移到这个标签下。Arch 合并后要重新 `chezmoi init`。
- [ ] Windows 系统配置备份：用 `system/windows/` 保存导出的注册表等系统设置（目录已预留，功能尚未实现）。
- [ ] 检测更多标签（例如 `xfce`，或者手动设置的本机标签），有实际用途时再加。

### 6.3 其他

- [ ] **作废旧的 PyPI token**：修剪时从 `secrets.zsh` 删掉的那个注释掉的旧 `UV_PUBLISH_TOKEN`，曾以加密形式存在于仓库历史中。如果它在 PyPI 上还没有作废，去作废它。
- [ ] Kali：在 IDA 的 GUI 中通过 Help → License manager 重新选择一次许可证文件，消除每次启动时的提示（kali.md 第 5 节）。
- [ ] 可选，Kali：合并或删除 3 个 `.dpkg-new` / `.dpkg-old` 文件（`dotfiles status` 会列出来）。

### 6.4 已完成（摘要，细节见 git 历史）

- 0.4.0：平台归属、环境标签、`/etc` 按发行版分目录。Kali 迁移（2026-11-03）：oh-my-zsh 改为 external，shell 启动文件加上存在性检查，`[requires] arch`（PR #10）。
- 0.5.0（#11）：apt 软件包清单，dpkg 感知的 `/etc` 备份，程序标签，restore 的 hooks 只改自己的仓库，没有 sudo 时的安装建议。`.zshrc` 改为明文，token 拆到加密的 `secrets.zsh`（2daf2e0）。
- 修剪（0.5.1，#12；5d37765）：停用 `.zimrc`、`.p10k.zsh`、ghostty、vboxclient、Cursor、rime、`.tool-versions`，通过 `.chezmoiremove` 在两台机器上删除。另外删除了 `.zshrc` 的 conda/mamba 块，以及 VS Code 设置中的 Arch 路径。Arch 上卸载了虚拟机工具和 rime。
- 0.5.2（#13）：`/etc` 备份不再重新加密没有变化的文件（但见 6.2，还有遗漏的情况）。
- 0.5.3（a4f4779）：apply 的演练带上 `--force`，本地有改动的目标不再让它失败。
- Kali 收尾（2026-11-04）：
  - 运行了 `scripts/kali.sh`；
  - zellij 在 GNOME 上用 `wl-copy`；
  - fcitx5 用中文写配置（`gnome` 标签）；
  - `notifications.conf` 只保留一项；
  - `/etc` 首次备份，排除了 4 个 apt 文件；
  - 新增 `scripts/arch.sh`。

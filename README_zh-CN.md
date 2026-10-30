# dotfiles

基于 [chezmoi](https://www.chezmoi.io) 的 Arch Linux 与 Windows 配置仓库，附带一个由 uv 管理的
Python 工具 `dotfiles`，用来做环境检测、重装后恢复、日常备份与缓存清理。两个平台共用同一个分支、
同一套命令。

## 仓库布局

```
.chezmoiroot              内容为 home：chezmoi 只把 home/ 当作源状态
home/                     两个平台的 dotfiles（chezmoi 源状态）
  .chezmoi.toml.tmpl      chezmoi init 时生成配置（加密方式、私钥路径、公钥）
  .chezmoidata/platforms.toml   每个目标的平台归属；.chezmoiignore 据此生成
system/                   Linux /etc 的备份（独立的 chezmoi 实例，不部署到 ~）
packages/                 软件包清单：archpkg.txt / scoopfile.json / winget.json
src/dotfiles/             Python 工具（只用标准库）
bootstrap.sh / .ps1       新机器引导：装好 git、chezmoi、uv 并克隆仓库，然后交给 dotfiles restore
docs/reference.md         详细参考：各命令的行为、加密与平台归属规则、清理目标、提交检查
```

根目录下的工具文件不在 `home/` 里，不会被 `chezmoi apply` 部署到家目录。

## 设计原则

- **chezmoi 负责文件读写，Python 工具负责决策**：`~` 与源状态之间的所有转换（属性前缀、加密、模板渲染、忽略规则）都由 chezmoi 完成。`dotfiles` 决定交给 chezmoi 哪些文件、要不要加密、结果能不能提交，然后调用 chezmoi 执行。工具自己只写 `platforms.toml` 和 `packages/` 下的软件包清单。

- **源状态就是备份清单**：`home/` 下有的就是托管的，`backup` 会对它们全部执行 re-add。`src/dotfiles/backup.py` 里的 `track` / `secret` 清单只用来发现新文件；`platforms.toml` 只规定每个托管目标在哪个平台部署。

- **每个命令只有一个方向**：`backup` 只把 `~` 写进仓库，`restore` / `chezmoi apply` 只把仓库写进 `~`。`backup` 从不部署，从不从源状态删除（停止托管用 `chezmoi forget`，并删掉 `platforms.toml` 里的对应条目），从不合并模板（只报告漂移），也从不推送。

- **原生 chezmoi 用法同样适用**：`chezmoi add`、`re-add`、`edit` 产生的源文件和 `backup` 产生的完全一样。漏掉的步骤（目标没登记、敏感文件被明文添加）会被 `dotfiles check` 和提交 hook 拦下，不管改动是从哪条路径来的，规则都由它们执行。

- **有意不纳入范围**：externals，例如 `~/.config/nvim`（独立的 git 仓库）；另一平台的目标（在当前平台上 chezmoi 看不到它们）；SSH 私钥（由 `restore` 在每台机器上重新生成）。

## 重装后恢复

准备: **离线保存的 age 私钥文件**（`AGE-SECRET-KEY-1…`），以及能访问 GitHub 的网络。如果需要代理，先在当前终端设置 `HTTPS_PROXY`。

### Arch Linux

```bash
curl -fsSLO https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.sh
less bootstrap.sh                          # 先读一遍再运行
bash bootstrap.sh                          # 只显示恢复计划
bash bootstrap.sh --apply                  # 执行（会询问私钥文件路径，也可加 --key-file /路径）
```

### Windows

在普通（非管理员）PowerShell 中：

```powershell
irm https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.ps1 -OutFile bootstrap.ps1
notepad bootstrap.ps1                      # 先读一遍再运行
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1            # 只显示恢复计划
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 --apply    # 执行
```

不要用 `curl … | bash` 或 `irm … | iex` 直接执行：那样标准输入不是终端，无法交互输入私钥路径。

### 恢复流程概览

bootstrap 只负责安装 git、chezmoi、uv（Arch 用 pacman，Windows 用 winget），并通过 HTTPS 把仓库克隆到
`~/.local/share/chezmoi`，其余都由 `dotfiles restore` 完成：

| 步骤 | 内容 |
|---|---|
| `key` | 把私钥写到 `~/.config/chezmoi/key.txt`（仅本人可读），并校验它与仓库公钥匹配 |
| `config` | 由 `home/.chezmoi.toml.tmpl` 生成 chezmoi 配置 |
| `dotfiles` | 先完整预演一次解密（失败则一个文件都不写），再备份将被覆盖的已有文件到 `~/.local/state/dotfiles/pre-apply-*`，最后 `chezmoi apply` |
| `system` | Linux：恢复 `/etc`；`fstab`、`crypttab`、`default/grub` 等与硬件绑定的文件只显示差异，`sudoers` 先经 `visudo` 校验 |
| `packages` | Linux：pacman 安装官方仓库中的包，AUR 包交给 paru/yay；Windows：`winget import` 与 `scoop import` |
| `externals` | 拉取 nvim 配置等外部仓库；git 代理连不上时（代理软件还没装）临时改为直连 |
| `ssh` | 为 `~/.ssh/config` 引用的每把缺失的私钥生成新密钥（私钥不备份），并可用 gh 把 GitHub 用的公钥登记上去 |
| `shell` | Linux：把登录 shell 改为 zsh |
| `hooks` | 安装提交检查 hooks；仓库与 externals 拉取用 HTTPS、推送改为 SSH |
| `verify` | `chezmoi verify`、安全检查、权限审计 |

每一步执行前都会重新检测当前状态，已完成的步骤自动跳过；任何一步失败都会停下，解决后重跑同一条命令即可从那一步继续。装包、改 `/etc`、换 shell、登记公钥这类操作执行前会确认（`--yes` 跳过确认）。只想执行某几步时用 `--only`，例如 `--only key,config,dotfiles`；跳过某几步用 `--skip`。

恢复完成后需要手动处理的事项会在输出中列出，通常包括：其他服务器上的 `authorized_keys`（新生成的 SSH公钥）、`packages/winget-unavailable.txt` 中不在 winget 源里的程序。

## 日常使用

```bash
uv run dotfiles doctor                 # 检测环境：工具链、加密配置、仓库与 hooks、缺少的软件包
uv run dotfiles backup -n              # 预览备份
uv run dotfiles backup --commit        # 备份并提交（不推送）；之后 git push
uv run dotfiles clean scan             # 查看可清理的缓存
uv run dotfiles clean run --apply      # 清理 default 层
```

装了 [just](https://github.com/casey/just) 时可以用短命令：`just doctor`、`just backup -n`、`just clean scan` 等，见 `just --list`。

| 命令 | 作用 |
|---|---|
| `doctor [--json]` | 环境检测（只读），缺少必需条件时退出码为 1 |
| `restore [--apply] [--only …] [--skip …] [--key-file 路径] [--yes]` | 重装后恢复，默认只显示计划 |
| `status` | 备份状态：漂移、未托管的候选、软件包清单、权限 |
| `backup [-n] [--no-system] [--commit]` | re-add 已托管文件 → 添加候选 → `/etc`（Linux）→ 软件包清单 → 安全检查 |
| `check` | 安全检查：明文密钥、`.age` 格式、必须加密的目标、平台归属 |
| `audit [--apply]` | 敏感文件权限（Linux 600/700，Windows ACL） |
| `system-restore [--apply] [/etc/路径…]` | Linux：对比 `system/` 与当前 `/etc`，按需安装 |
| `clean scan / list / run / leftovers` | 缓存清理，`run` 默认只预览，`--apply` 执行 |

所有修改性操作默认只预览。完整说明见 [docs/reference_zh-CN.md](docs/reference_zh-CN.md)。

## 安全设计

- **私钥不进仓库**：仓库中只有公钥；`check` 与提交检查会拦下任何明文私钥（`AGE-SECRET-KEY-`、PEM 私钥块）以及被 git 跟踪的私钥文件。

- **敏感文件一律加密**：备份时按清单、密钥特征扫描和 chezmoi 内置扫描三重判断是否加密；SSH 配置、凭据文件、`sudoers` 等目标未加密时提交会被拒绝。加密托管的文件部署后权限为 600。

- **提交与推送双重检查**：pre-commit 阶段检查暂存区，pre-push 阶段检查每一个待推送的提交（`--no-verify`跳过的提交也会被拦下）。输出只有文件、行号和规则名，不显示密钥。

- **平台隔离**：每个目标在 `platforms.toml` 中声明归属，另一平台的目标（以及只属于它的父目录）会被忽略，一个平台上的备份不会改写另一平台的源文件。

- **恢复不会静默破坏现有文件**：先预演解密，再备份将被覆盖的文件；与硬件绑定的 `/etc` 文件不会自动安装。

- **临时明文只放在 tmpfs**：`/etc` 备份与恢复的暂存区位于 `$XDG_RUNTIME_DIR`，用完即删。

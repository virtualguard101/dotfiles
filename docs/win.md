# Windows 配置说明

本文件是给人和 AI agent 读的上下文，记录 Windows 这台机器在仓库中的位置：哪些配置在这里部署、哪些系统设置被备份、哪些不在仓库中，以及在 Windows 上操作时的注意事项。Linux 两台机器的对应文档是 [arch.md](arch.md) 和 [kali.md](kali.md)；命令细节见 [reference_zh-CN.md](reference_zh-CN.md)，分支用法见 [workflows_zh-CN.md](workflows_zh-CN.md)。

内容按 2026-11-04 在本机核对的结果写成。这是 Python 版 `dotfiles` 工具第一次在真正的 Windows 上运行，当天发现并修复的问题见第 7 节。

## 1. 本机概况

| 项目 | 情况 |
|---|---|
| 角色 | 独立的 Windows 笔记本。整块 1 TB NVMe 上只有 EFI 和 C: 两个分区，**和 Arch / Kali 不在同一台机器上**（那两个系统共用另一台实体机） |
| 系统 | Windows 11 Pro 10.0.26200，AMD64 |
| 硬件 | 笔记本，Intel UHD + NVIDIA RTX 4060 Laptop 双显卡 |
| 用户 | 本地账户，日常是**非提权**会话（属于 Administrators 组，但令牌没有提权）；内置 Administrator 已禁用。需要管理员的步骤要自己在提权终端运行 |
| 语言 | 中文（zh-CN）区域，时区 China Standard Time；输入语言 en-US、zh-Hans、ja |
| shell | PowerShell 7（pwsh 7.6）为主，5.1 也在。工具用 `just` 或 `uv run dotfiles` 调用 |
| 包管理器 | scoop 和 winget |
| 标签 | 没有。Windows 上 chezmoi 的 `.chezmoi.osRelease` 是空的，只有 Linux 才有标签；所有 `[requires]` 目标在这里都不部署 |
| 代理 | mihomo-party，127.0.0.1:7897（系统代理由它开关）。`.gitconfig` 的 `http.proxy` 用同一个端口 |
| gh | **没有登录**，agent 不能开 PR。dev 分支改完后在本地 `git merge --no-ff` 合并 |
| 远端 | `origin` 是 HTTPS（`https://github.com/virtualguard101/dotfiles.git`），推送走 `http.proxy`，需要代理在运行 |

## 2. 仓库中哪些内容在 Windows 上部署

规则都在 `home/.chezmoidata/platforms.toml`。`[platforms] windows` 目前有：

| 目标 | 说明 |
|---|---|
| `.claude/settings.json` | 加密 |
| `.docker/config.json` | 加密 |
| `.wakatime.cfg` | 加密 |
| `.config/scoop` | scoop 的配置 |
| `AppData` | Windows Terminal 的 `settings.json`、VS Code 的 `keybindings.json` |
| `Documents/PowerToys` | PowerToys 的备份（`.ptb`）。新机器要在 PowerToys 里手动点"还原" |
| `.gitconfig`（shared） | 模板：Windows 上的用户名、`ida` 别名指向 `C:/Program Files/IDA Professional 9.5/tools/teams/git-ida.exe` |

其余内容：

- 所有 `linux` 目标都不部署。**SSH 不由仓库管理**：`~/.ssh/id_ed25519` 是本机自己生成的，`~/.ssh/config` 是空的，仓库里的 `.ssh` 在 Windows 上被忽略。
- externals：没有（nvim 配置只在 Linux 上拉取）。
- `packages/scoopfile.json`（9 个应用，3 个 bucket）、`packages/winget.json`（69 个 winget 包和 4 个 msstore 包）、`packages/winget-unavailable.txt`（121 个 winget 导出不了的程序，见第 5 节）。backup 会更新它们，清单没变就没有改动；restore 的 packages 步骤会装缺少的包。
- `system/windows/registry`：见第 3 节。
- `dotfiles.toml` 的 `[backup.windows]` 里还有这台机器上不存在的候选（`.wslconfig`、两份 PowerShell profile、gh 的 `hosts.yml`、rclone 配置），它们出现时 backup 会自动收入。
- **VS Code 的 `settings.json` 不备份**：里面的 `chat.pluginLocations` 含家目录的绝对路径，backup 会跳过它并给出提示。要备份的话，先去掉这一项，或者改成 `{{ .chezmoi.homeDir }}` 模板。

## 3. 系统配置备份（`system/windows`）

这是 Windows 对应 Linux `/etc` 备份的部分（reference 3.9）：第二个 chezmoi 实例，源目录是 `system/windows/`，**需要在 `dotfiles.toml` 的 `[windows] registry` 里列出键才会运行**。目前只支持注册表键（每个键一个 `.reg` 文件，存成 UTF-8、LF，方便 git diff）；不支持复制文件或导出计划任务。

2026-11-04 加入了两个键，都是 HKCU，不需要管理员：

| 键 | 内容 |
|---|---|
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize` | 深色主题（应用和系统都是深色）、透明效果 |
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced` | 显示文件扩展名、任务栏左对齐、时钟显示秒、隐藏小组件、资源管理器打开到"此电脑"、显示电池百分比 |

检查过、**决定不备份**的内容：

| 内容 | 原因 |
|---|---|
| 用户环境变量和用户 `Path`（34 项） | `JAVA_HOME`、`PNPM_HOME`、`C_INCLUDE_PATH` 是 scoop 装软件时设的，`scoop import` 会重新生成；值里写死了家目录，提交检查会拦下 |
| `Internet Settings` 的系统代理 | mihomo-party 开关代理时写入的运行状态，备份下来会过期 |
| `Run` 开机启动项（HKCU 12 项、HKLM 2 项） | 都是程序自己写的，装回来会恢复 |
| `HKLM\SOFTWARE\Policies\Microsoft\Windows` | 看过内容：全是空键和 Windows 默认模板，没有手工设置的策略 |
| `HKCU\Control Panel\Desktop` | 含壁纸的家目录路径 |
| `Control Panel\International`、键盘、鼠标、控制台配色、电源计划 | 都是默认值 |
| `LongPathsEnabled`（`HKLM\...\Control\FileSystem`，值为 1） | 是 HKLM，见下面的限制；重装后用管理员终端 `reg add` 或在组策略里重新打开 |
| 计划任务 | 除了 `cua-driver-serve` 以外都是程序自己创建的（OneDrive、WPS、NVIDIA、Quark 等）。工具不能导出计划任务 |
| `hosts`（1 条自定义记录）、WSL 里的 `/etc/wsl.conf`、Git 的系统 `gitconfig`、防火墙规则（72 条未分组的启用规则） | 工具不备份文件；Git 的 `gitconfig` 由安装器生成；防火墙规则基本是程序安装时添加的 |

注意：

- **HKLM 键会让整个 `system-restore --apply` 要求管理员**：检查的是清单里是否有 HKLM / HKCR 键，不是命令行上指定的键。所以清单里现在只放 HKCU 键。要加 HKLM 键，要接受之后 `system-restore --apply` 总要在提权终端里运行；更好的办法是把检查改成只针对要恢复的键（见第 7 节的待办）。
- 导入是**合并**：备份之后新增的值不会被删掉。回滚用 git 历史里的旧版 `.reg`，再 `system-restore`。
- `Explorer\Advanced` 里有 `TaskbarStateLastRun` 这类带时间戳的值。连续导出两次（间隔 2 秒）没有差异，但跨重启没有验证过。如果 backup 在没改设置的情况下出现这个键的改动，可以直接提交，或者从清单里去掉这个键。
- 在 Git Bash 里手动运行 `reg export 键 文件 /y` 会失败：MSYS 把 `/y` 当成路径改写。用 PowerShell，或者设 `MSYS2_ARG_CONV_EXCL='*'`。`dotfiles` 自己通过 Python 调用 `reg`，不受影响。

## 4. 与 Linux 不同、需要特别处理的地方

### 4.1 PATH 和 shell

- 从 Git Bash 运行时，`C:\Program Files\Git\usr\bin` 排在 `System32` 前面，同名的命令会被 GNU 版替代：`whoami` 不认识 `/user`，`ssh-keygen` 也会解析到 Git 自带的那个（`doctor` 在 PowerShell 和 Git Bash 里给出的路径不一样）。`dotfiles` 里依赖这类命令的地方都要用绝对路径，`whoami` 已经这样处理（第 7 节）。
- 输出里的 ✓、· 等字符在控制台编码装不下时会被转义成 `\u2713`（`ui.py` 有意这样做，避免崩溃）。通过管道或在 cp936 的控制台里看到这样的输出时，设 `PYTHONIOENCODING=utf-8`。

### 4.2 权限（ACL）

- Windows 上 `audit` 检查的是 ACL：只有当前用户、SYSTEM 和 Administrators 可以访问敏感文件。`audit --apply` 和 restore 写入私钥时，用 `icacls` 去掉继承并只授权这三个主体。
- 当前只有一项：`~/.wakatime.cfg` 对 `CodexSandboxUsers`（Codex 沙箱创建的本地组）可读。**没有执行 `audit --apply`**，因为那会同时移除沙箱的访问；不再用 Codex 沙箱时再收紧。
- 其余的敏感文件（`id_ed25519`、`.claude/settings.json`、`.docker/config.json`、`~/secret`）核对过，ACL 都是正常的。

### 4.3 符号链接和联接

- 普通账户（没开开发者模式）创建不了符号链接（`WinError 1314`）。scoop 的 `current` 目录是联接（junction），不需要特权。
- `clean` 的删除**不跟随联接和符号链接**，只删链接本身。测试里在 Windows 用 `mklink /J` 构造联接，这条路径现在在 Windows 上有测试覆盖。

### 4.4 管理员

- scoop 拒绝在管理员会话里安装，`bootstrap.ps1` 也拒绝。所以恢复一台新机器时，用普通会话运行。
- 需要管理员的只有：`clean` 的 `wu`、`dism` 两个 opt 目标，以及导入 HKLM / HKCR 的注册表键。

### 4.5 正在运行的程序

`clean` 会跳过正在运行的程序的缓存（Electron 应用、浏览器、updater）。这台机器上经常开着：VS Code、WakaTime、mihomo-party、Notion、QQ、uTools、Edge、Firefox、网易云音乐，所以 `clean scan` 的 electron、browser、firefox、updaters 四项大多显示 `skips`，合计约 1.7 GB。想一次清完，先退出这些程序。

### 4.6 换行

仓库里的文本一律是 LF（`.gitattributes`），Git 的系统配置里 `core.autocrlf=true`。提交时出现 "LF will be replaced by CRLF" 的提示是正常的，不用处理。`chezmoi diff` 里只有行尾差异的条目，`apply` 之后会变成仓库的版本。

### 4.7 路径的比较

`chezmoi managed -p absolute` 在 Windows 上输出正斜杠（`C:/Users/...`），而 `str(Path)` 是反斜杠。代码里比较路径时要把两边都转成 `Path`，不能直接比较字符串（第 7 节的泄密问题就是这样来的）。

## 5. 不在仓库中的本机状态

重装或换机器时要手动恢复的内容：

| 内容 | 位置和说明 |
|---|---|
| age 私钥 | `~/secret/chezmoi_age_key.txt`，ACL 只有本人、SYSTEM、Administrators。和 Arch / Kali 共用同一把（公钥和仓库一致）。不在仓库中，要离线保存 |
| winget 导出不了的程序 | 121 个，列在 `packages/winget-unavailable.txt`：Store 应用、游戏、驱动、厂商工具（ASUS、Universal Audio 等）。要手动安装 |
| IDA Pro 9.5 | `C:\Program Files\IDA Professional 9.5`，用户数据在 `~\AppData\Roaming\Hex-Rays`。用户环境变量 `IDAPYTHON_VENV_EXECUTABLE` 指向它的 venv |
| WSL2 | 两个发行版：Ubuntu（默认，`/etc/wsl.conf` 里 `systemd=true`，默认用户和 Windows 同名）和 `docker-desktop`。`~/.wslconfig` 不存在。Docker 的虚拟磁盘约 5.8 GB，要先 `wsl --shutdown` 再手动压缩 |
| 代理 | mihomo-party（有自己的计划任务和 updater），配置不在仓库中 |
| 手工创建的计划任务 | `cua-driver-serve`。其余计划任务都是程序自己创建的 |
| `hosts` | 有 1 条自定义记录，没有备份（工具不备份文件） |
| 用户环境变量 | `JAVA_HOME`、`PNPM_HOME`、`C_INCLUDE_PATH` 是 scoop 设的，`scoop import` 后会重建；`GOPATH`（`~\go`，Go 的默认值）和 `IDAPYTHON_VENV_EXECUTABLE` 是手工设的，重装后需要时再设 |
| PowerToys | 备份在仓库里，但要在 PowerToys 中手动还原 |

## 6. 在 Windows 上的日常操作

- **环境检查**：`just doctor`（或 `uv run dotfiles doctor`）。当前全绿。
- **拉取后**：`dotfiles apply` 预览，再 `dotfiles apply --apply` 部署。
- **备份前**：`dotfiles backup -n` 预览。`backup` 依次做：re-add 已管理的文件 → 候选配置 → 注册表键 → scoop / winget 清单 → 提交检查。注册表和包清单没变化时不产生改动。
- **清理**：`dotfiles clean scan`（约 17 秒）→ `dotfiles clean run` 预览默认档 → `dotfiles clean run --apply` 执行。默认档目前约 4.6 GB（含当前被跳过的）。opt 档要单独指定：网易云音乐的缓存（`AppData\Local\NetEase` 共约 20 GB，其中播放缓存约 18 GB）、回收站（约 700 MB）、nuget 全局包等。`scan` 末尾列出的残留：`~\AppData\Roaming\Cursor`（2.3 GB，Cursor 已卸载，确认没用后手动删除）。
- **分支**：日常备份直接提交到 `main`（workflows 2.1）。工具或规则的改动在主检出中 `git switch dev`，完成后切回 `main` 再 `git merge --no-ff dev`。停在 `dev` 上时不要 `apply --apply` 或做日常 backup，因为主检出就是 chezmoi 生效中的源目录。
- **推送**：远端是 HTTPS，要代理在运行。`pre-commit install-hooks` 下载 gitleaks 时如果超时，设 `HTTPS_PROXY=http://127.0.0.1:7897`。
- **测试**：`uv run --with pytest python -m pytest tests`，当前 146 个通过，24 个跳过（都是 Linux 专属的 `/etc` 备份测试）。

## 7. 2026-11-04 在本机上的核对结果

工具之前只在 Linux 上做过文件系统层面的测试。当天在这台机器上依次运行了 `doctor`、`apply`、`backup -n`、`backup`、`restore`（不带 `--apply`）、`clean scan`、`clean run`（预览）、`audit` 和 `system-restore`，都能运行。注册表备份用一个临时键（含中文、emoji、二进制和多字符串值）走完了"备份 → 修改 → 预览 → 导入 → 复核"，Unicode 原样保存；临时键和对应文件已删除。

发现并修复的问题：

- **`audit` 误报，`--apply` 会把用户锁在自己的文件外**：`current_sid()` 靠 PATH 里的 `whoami`，Git Bash 下解析到 GNU 版，返回空；于是用户自己的 SID 被当成"别人"，而且 `restrict_acl` 会跳过空值，只给 SYSTEM 和 Administrators 授权。restore 写入 age 私钥和 SSH 私钥时用的也是它。现在用 `%SystemRoot%\System32\whoami.exe` 的绝对路径，取不到 SID 时 `restrict_acl` 拒绝执行，`audit` 直接报错。有回归测试。
- **加密的注册表键在预览里被显示内容**：`system-restore` 靠字符串判断一个文件是不是加密的，Windows 上路径分隔符不一样，永远判断为"不是"，于是 diff 把 token 打印到了终端。改成 `Path` 比较，原来的测试 `test_restore_never_shows_the_content_of_an_encrypted_key` 现在通过。
- **测试夹具在 Windows 上不可用**：写 TOML 时把 Windows 路径放进双引号字符串，`\U` 是非法转义，chezmoi 配置解析失败（改用正斜杠）；`os.symlink` 需要特权（改用联接）；`/home/alice` 的测试在 Windows 上被转成反斜杠路径（改用 `PurePosixPath`）；Linux 专属的 `/etc` 测试在 Windows 上跳过。改动前是 24 个失败、7 个错误，现在全部通过或跳过。

未处理的事项：

- `system-restore --apply` 只要清单里有 HKLM 键就要求管理员，应该改成只检查要恢复的键（`winsys.cmd_restore`）。改了之后才适合把 `LongPathsEnabled` 这类 HKLM 键加进清单。
- `~/.wakatime.cfg` 对 `CodexSandboxUsers` 可读（4.2）。
- VS Code 的 `settings.json` 因家目录路径没有备份（第 2 节）。
- `clean run --apply` 没有执行过，默认档的预览是正常的。
- `hosts`、WSL 的 `wsl.conf`、`cua-driver-serve` 计划任务：需要工具支持备份文件和导出计划任务才能收入 `system/windows`。

## 8. 备份和回滚的位置

| 内容 | 位置 |
|---|---|
| apply 前被覆盖或删除的文件 | `~/.local/state/dotfiles/pre-apply-<时间>/`（本机现有 `20261102-201557`） |
| 旧的 chezmoi 配置 | `~/.config/chezmoi/chezmoi.toml.bak-*`（本机现有 `20261102201234`） |
| 注册表键 | git 历史里的 `system/windows/registry/*.reg`；回滚用旧版本的文件再 `system-restore --apply`（合并，不会删除备份之后新增的值） |
| 新建的文件 | `chezmoi managed` 会列出全部目标，删除对应文件即可 |

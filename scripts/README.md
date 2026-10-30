# scripts/（Windows）

本目录存放两个相互独立的维护脚本（PowerShell），以及一个跨平台的提交检查脚本（Python）。接口与 `archlinux` 分支的 `clean.sh`、`backup.sh` 保持一致：

| 文件 | 职责 | 是否修改系统 |
|---|---|---|
| [`clean.ps1`](clean.ps1) | 清理 `%TEMP%`、本机工具的包缓存与应用缓存；报告残留目录 | `run -Apply` 时删除缓存 |
| [`backup.ps1`](backup.ps1) | 基于 chezmoi 备份 `%USERPROFILE%` 下的配置，敏感文件用 age 加密；导出 scoop / winget 软件包清单 | `backup` 时写入 chezmoi 源目录 |
| [`hooks/dotfiles_guard.py`](hooks/dotfiles_guard.py) | pre-commit / pre-push 检查。**与 `archlinux` 分支共用同一份文件** | 否 |

共同约定：

- **破坏性操作默认只预览**：`clean.ps1 run` 需加 `-Apply`，`backup.ps1 audit` 需加 `-Apply`。`backup` 直接写入源目录（可用 `-n` 预览），不会自动 `git push`。
- 退出码：`0` 成功，`1` 出错或检查未通过，`2` 用法错误。
- PowerShell 5.1 与 7 通用。两个 `.ps1` 以 **UTF-8 带 BOM** 保存，否则 5.1 会按系统代码页（中文系统为 GBK）读取中文字符串。
- `scripts/` 已写入 `.chezmoiignore`，不会被 `chezmoi apply` 部署到 `~`。

运行方式（执行策略拦截时用 `-ExecutionPolicy Bypass`）：

```powershell
Set-Location (chezmoi source-path)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\clean.ps1 scan    # 或 pwsh
```

下文示例省略前缀，写作 `.\scripts\clean.ps1 scan`。

---

## clean.ps1

### 快速开始

```powershell
.\scripts\clean.ps1 scan                  # 只读：各目标占用、未覆盖的大目录、残留目录
.\scripts\clean.ps1 run                   # 预览 default 层会执行的命令
.\scripts\clean.ps1 run -Apply            # 执行 default 层
.\scripts\clean.ps1 run -Apply recycle    # 执行指定的 opt 层目标
```

### 子命令

| 子命令 | 说明 |
|---|---|
| `scan [-Deep]` | 只读。依次输出：各驱动器空间与 `%TEMP%` 总占用；每个目标的层级、占用、状态；`%LOCALAPPDATA%` 与 `~/.cache` 下未被任何目标覆盖的大目录（≥ `UNCOVERED_MIN_MB`）；残留目录报告（同 `leftovers`）；缩略图缓存与 WSL 虚拟磁盘大小。 |
| `list` | 列出全部目标及其层级与说明，不计算大小。 |
| `run [选项] [目标…]` | 执行清理，默认只预览。不指定目标时等同于 `default`。 |
| `leftovers` | 只读。列出已卸载应用的残留目录，以及超过 `STALE_DAYS` 天无写入的 Electron 配置目录。**脚本永远不会删除这些目录。** |

`run` 的参数：

| 参数 | 说明 |
|---|---|
| `-Apply` | 实际执行。不加时只打印将要执行的命令与删除的路径。 |
| `-Deep` | 对支持的目标使用更彻底的清理方式。 |
| `-Exclude 目标,目标` | 排除目标（别名 `-x`），逗号分隔或重复使用。 |
| `default` / `opt` / `all` / `<目标 id>` | 选择目标，可写多个。 |

`scan` 的状态列：`可清理`、`有跳过`（下一行 `↳` 说明原因）、`无数据`、`不适用`（工具未安装或目录不存在）、`未统计`（如 `dism`，无法预先计算）。

### 目标

**default 层**：可再生缓存，删掉的代价最多是重新下载或重建。

| 目标 | 清理动作 | `-Deep` | 跳过条件 |
|---|---|---|---|
| `temp` | `%TEMP%` 顶层条目中，自身及其内部所有文件都超过 `TEMP_DAYS` 天未修改的 | — | 被占用的文件删除失败时跳过并计数 |
| `scoop` | `scoop cleanup -a -k`：删除旧版本与非当前版本的安装包 | 额外 `scoop cache rm -a` | — |
| `electron` | `%APPDATA%\<App>`（含 `Default`、`Profile*`、`Partitions\*`）中的 `Cache`、`Code Cache`、`GPUCache`、`Dawn*Cache`、`GPUPersistentCache`、`GrShaderCache`、`GraphiteDawnCache`、`ShaderCache`；VS Code 系应用另删 `CachedData`、`CachedExtensionVSIXs`、`CachedProfilesData`、`logs` | — | 应用正在运行，见“运行检测” |
| `browser` | Edge、Chrome、Brave 各配置文件的 `Cache`、`Code Cache`、`GPUCache`、`Dawn*Cache`，以及 `User Data` 下的着色器缓存 | — | `User Data\lockfile` 被占用 |
| `firefox` | `%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*` 的 `cache2`、`startupCache`、`thumbnails`、`jumpListCache` | — | `%APPDATA%` 中同名配置目录的 `parent.lock` 被占用 |
| `updaters` | `%LOCALAPPDATA%\*-updater` 中 electron-updater 下载的 `installer.exe` 与 `pending\`（例如 Notion 会在这里累积多个旧版 MSIX） | — | 对应应用正在运行（可能正等待退出后安装更新） |
| `uv` | `uv cache prune` | `uv cache clean` | — |
| `pip` | `pip cache purge` | — | — |
| `pnpm` | `pnpm store prune`，删除 `%LOCALAPPDATA%\pnpm-cache` | — | — |
| `npm` | `npm cache clean --force`，删除 `_logs` | 额外删除 `_npx` | — |
| `bun` | `bun pm cache rm` | — | — |
| `yarn` | 删除 `%LOCALAPPDATA%\Yarn\Cache` 与 `~\.yarn\berry\cache` | — | — |
| `go` | `go clean -cache`；Go 已卸载但 `%LOCALAPPDATA%\go-build` 还在时直接删除该目录 | 额外 `go clean -modcache` | — |
| `cargo` | 删除 `%CARGO_HOME%\registry\src` 与 `git\checkouts` | 额外删除 `registry\cache` 与 `git\db` | cargo 正在运行 |
| `nuget` | `dotnet nuget locals http-cache --clear` | — | — |
| `precommit` | `pre-commit gc`，删除 60 分钟前遗留的 stash 补丁 `patch*`（可能含明文） | `pre-commit clean`（之后提交需要重新下载并构建 gitleaks） | — |

**opt 层**：只有点名或使用 `opt` / `all` 时才执行。

| 目标 | 清理动作 | 不放进 default 的原因 |
|---|---|---|
| `crashdumps` | `%LOCALAPPDATA%\CrashDumps`、`WER\ReportArchive`、`WER\ReportQueue` | 调试崩溃时可能需要 |
| `cloudmusic` | 网易云音乐播放缓存 `NetEase\CloudMusic\Cache`（不含下载的歌曲；运行时跳过） | 删除后重新听歌需要重新缓存；更好的做法是在客户端设置中限制缓存大小 |
| `shader` | `D3DSCache`、NVIDIA / AMD / Intel 着色器缓存 | 首次启动游戏会卡顿 |
| `nugetpkgs` | `dotnet nuget locals global-packages --clear` | 下次构建重新下载全部包；Visual Studio / MSBuild / dotnet 运行时跳过 |
| `maven` | `~\.m2\repository` | 下次构建重新下载全部依赖；Maven 运行时跳过 |
| `gradle` | `gradle --stop` 后删除 `caches` 与 `daemon` | 同上 |
| `recycle` | `Clear-RecycleBin -Force`（所有驱动器） | 不可恢复 |
| `docker` | `docker builder prune -f`、`docker image prune -f`（只删 dangling 镜像） | 需要 Docker Desktop 在运行 |
| `wu` | 停止 `wuauserv` 与 `bits`，清空 `%WINDIR%\SoftwareDistribution\Download`，再启动服务 | 需要管理员 PowerShell |
| `dism` | `DISM /Online /Cleanup-Image /StartComponentCleanup` | 需要管理员，耗时较长 |

### 运行检测

Windows 上被占用的文件通常删不掉，但只删掉一半的缓存同样会出问题，所以先判断应用是否在运行：

1. **Chromium 浏览器的 `User Data\lockfile`**、**Firefox 的 `parent.lock`**：程序运行时以独占方式打开。脚本尝试独占打开，失败即视为运行中。
2. **VS Code 系的 `code.lock`**：内容是 PID，检查该进程是否存在。
3. 其余应用：按进程名匹配目录名（去掉空格），以及脚本中 `$ProcessAlias` 的别名（例如 `LarkShell` → `Feishu`）。

### 安全措施

- 所有删除都经过 `Assert-Deletable`：路径必须是绝对路径，位于 `%USERPROFILE%`、scoop 目录或 Windows Update 下载目录之下，并且不能是 `%USERPROFILE%`、`%APPDATA%`、`%LOCALAPPDATA%`、`%TEMP%`、`Documents` 等目录本身。路径为空或不符合时直接报错，不会删除。
- 递归删除**不跟随**联接点与符号链接（只删除链接本身），避免删到链接指向的目录。
- 被占用的文件跳过并在结尾报告数量。

### 不清理的内容

- Electron / 浏览器的 `Service Worker`、`IndexedDB`、`Local Storage`：离线数据或登录状态。
- `%LOCALAPPDATA%\Package Cache`：安装程序缓存，卸载、修复软件时需要。
- 缩略图缓存 `thumbcache_*.db`：被资源管理器占用，用“磁盘清理”（`cleanmgr`）的“缩略图”项清理。
- WSL / Docker 虚拟磁盘：只报告大小；压缩需要先 `wsl --shutdown`，建议手动执行。

### 残留目录

`$LeftoverMap` 每行格式为 `检测名;检测名|路径;路径`（路径相对 `%USERPROFILE%`）。检测名任一满足即视为已安装：`PATH` 中的命令、“应用和功能”中的程序名（ASCII 名按整词匹配，中文名按子串匹配）、scoop 应用名、或含 `/` 的相对路径存在。目录正被占用时也视为在用（例如应用改了名）。

### 环境变量

| 变量 | 默认值 | 作用 |
|---|---|---|
| `TEMP_DAYS` | `7` | `temp` 目标的天数阈值 |
| `STALE_DAYS` | `180` | `leftovers` 判定“久未使用”的天数 |
| `UNCOVERED_MIN_MB` | `100` | `scan` 列出未覆盖项的最小体积（MB） |

另外遵循 `SCOOP`、`XDG_CACHE_HOME`、`CARGO_HOME`、`GRADLE_USER_HOME`、`PRE_COMMIT_HOME`、`BUN_INSTALL`、`BUN_INSTALL_CACHE_DIR`、`NUGET_HTTP_CACHE_PATH`、`NUGET_PACKAGES`。

### 定期运行（可选）

用任务计划程序每周执行一次 default 层（不需要管理员，运行中的应用会被跳过）：

```powershell
$src = chezmoi source-path
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$src\scripts\clean.ps1`" run -Apply"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 12:00
Register-ScheduledTask -TaskName 'dotfiles-clean' -Action $action -Trigger $trigger -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

---

## backup.ps1

### 前提

- 已安装 `chezmoi`、`git`、`python`（3.9 及以上）。age 使用 chezmoi 内置实现，不需要单独安装。
- chezmoi 配置中设置了 `encryption = "age"`、`age.identity` 与 `age.recipient`。模板是 [`dot_config/chezmoi/chezmoi.toml.tmpl`](../dot_config/chezmoi/chezmoi.toml.tmpl)，私钥位置为 `~\secret\chezmoi_age_key.txt`；修改模板后执行 `chezmoi apply ~/.config/chezmoi/chezmoi.toml` 生效。
- **age 私钥必须放在源目录之外，并另行离线备份**。本分支与 `archlinux` 分支共用同一把密钥。
- `.chezmoiignore` 中包含 `scripts/`、`scoopfile.json`、`winget.json`、`winget-unavailable.txt`，缺少任一项时脚本直接退出。

### 快速开始

```powershell
.\scripts\backup.ps1 status          # 只读：看有什么待备份、有哪些风险
.\scripts\backup.ps1 backup -n       # 预览
.\scripts\backup.ps1 backup -Commit  # 备份并提交（不 push）
git push                             # 确认 diff 后手动推送
```

### 子命令

| 子命令 | 说明 |
|---|---|
| `status` | 只读。输出加密配置、`chezmoi status` 漂移、未托管的备份候选及其处理方式（plain / encrypt / 跳过）、软件包清单的更新时间、权限审计。 |
| `backup [-n] [-Commit]` | 执行备份，流程见下文。`-n` 时 chezmoi 以 `--dry-run --verbose` 运行，软件包清单也不写入。 |
| `check` | 只运行安全检查，失败时退出码为 1。 |
| `audit [-Apply]` | 检查敏感文件的 ACL：除当前用户、SYSTEM、Administrators 外还有谁能访问。`-Apply` 时用 `icacls` 去掉继承并只保留这三者。 |

### backup 流程

1. **预检**：确认启用了 age 加密、配置了接收者、身份文件存在且不在源目录内、`.chezmoiignore` 完整；“文档”被重定向（例如到 OneDrive）时给出警告。
2. **re-add 已托管文件**：明文托管的文件若出现了密钥特征，先用 `chezmoi add --encrypt` 转为加密，再对全部文件执行 `chezmoi re-add`。
3. **添加候选**：把 `$HomeSecret` 与 `$HomeTrack` 中尚未托管的文件加入 chezmoi。目录展开为其中小于 1 MiB 的普通文件，跳过重解析点与 `$HomeExclude`。
4. **软件包清单**：见下文。
5. **安全检查**：同 `check`。
6. **提交**：仅在指定 `-Commit` 时执行 `git add -A` 并提交（pre-commit hook 会再检查一次）。

### 加密判定

命中任一条即加密：在 `$HomeSecret` 中；内容匹配 `hooks/dotfiles_guard.py` 的 `SECRET_RULES`（guard 出错时按命中处理）；以 `chezmoi add --secrets error` 明文添加时被 chezmoi 内置规则拦下。

内容中含有绝对家目录路径（`C:\Users\<名>`、`C:/Users/<名>`、JSON 中的 `C:\\Users\\<名>`）的文件会被**跳过**并给出警告，需要改写为模板后手动添加。

### 软件包清单

| 文件 | 来源 | 还原 |
|---|---|---|
| `scoopfile.json` | `scoop export` | `scoop import scoopfile.json` |
| `winget.json` | `winget export` | `winget import -i winget.json --accept-package-agreements` |
| `winget-unavailable.txt` | `winget export` 报告的“不在任何源中”的程序 | 手动安装 |

导出结果经过归一化：scoop 去掉 `Updated` 等每次都会变化的字段（`Version` 只对 `<auto-generated>` 来源保留，`scoop import` 只在这种情况下使用它），winget 去掉 `CreationDate`，并按名称排序。因此清单只在软件包增减时变化。序列化使用 Python，PowerShell 5.1 与 7 生成的文件完全一致。

### 自定义清单

| 变量 | 作用 |
|---|---|
| `$HomeTrack` | 推荐托管的配置（相对 `%USERPROFILE%`，用 `/`，可以是目录） |
| `$HomeSecret` | 一律加密的配置 |
| `$HomeExclude` | 展开目录时跳过的文件名模式 |

### 已知事项

- **VS Code `settings.json`**：其中的 `chat.pluginLocations` 含绝对家目录路径，因此被跳过；文件中还有密钥特征，托管时必须加密。如果接受这一行与本机绑定，可以手动 `chezmoi add --encrypt` 托管，之后由 `re-add` 维护。
- **换行**：`.gitattributes` 让仓库内统一为 LF。`~\.gitconfig` 等 Windows 上为 CRLF 的文件，`chezmoi apply` 后会变为 LF，不影响使用。
- 新文件第一次被 `backup` 加入后由 `re-add` 维护；从清单中删除某个路径后还需要 `chezmoi forget` 才能停止托管。

---

## 提交检查（pre-commit）

规则与文件说明见 `archlinux` 分支的 `scripts/README.md`“提交检查”一节；两端共用 `.pre-commit-config.yaml`、`.gitleaks.toml`、`.gitattributes`、`.gitignore`、`scripts/hooks/dotfiles_guard.py`，修改时两个分支要同步：

```powershell
git fetch origin
git checkout origin/archlinux -- .pre-commit-config.yaml .gitleaks.toml .gitattributes .gitignore scripts/hooks/dotfiles_guard.py
```

Windows 上的安装（每台机器一次）：

```powershell
uv tool install pre-commit
Set-Location (chezmoi source-path)
pre-commit install                 # 同时安装 pre-commit 与 pre-push
# 首次构建 gitleaks 需要下载 Go 模块；直连 proxy.golang.org 失败时临时走本机代理（地址同 git config http.proxy）：
$env:HTTPS_PROXY = "http://$(git config http.proxy)"; pre-commit install-hooks; Remove-Item Env:HTTPS_PROXY
```

临时跳过某个 hook：`$env:SKIP = 'gitleaks'; git commit -m '...'; Remove-Item Env:SKIP`。不要用 `--no-verify`；即使用了，pre-push 也会再检查一次。

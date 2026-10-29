# scripts/

本目录存放两个相互独立的维护脚本（Arch Linux，Bash），以及一个跨平台的提交检查脚本（Python）：

| 文件 | 职责 | 是否修改系统 |
|---|---|---|
| [`clean.sh`](clean.sh) | 清理 `~/.cache` 及本机已安装工具的包缓存、应用缓存；报告残留目录 | `run --apply` 时删除缓存 |
| [`backup.sh`](backup.sh) | 基于 chezmoi 备份 `~` 与 `/etc`，敏感文件用 age 加密，提交前做安全检查 | `backup` 时写入 chezmoi 源目录 |
| [`hooks/dotfiles_guard.py`](hooks/dotfiles_guard.py) | pre-commit / pre-push 检查：age 密文、必须加密的目标、密钥特征。Windows 与 Linux 通用，见[提交检查](#提交检查pre-commit) | 否（只删 pre-commit 自己遗留的 stash 补丁） |

`clean.sh` 与 `backup.sh` 互不依赖，可以单独复制、单独运行；`backup.sh` 的密钥识别调用 `hooks/dotfiles_guard.py`，与提交检查共用一套规则。`scripts/` 已写入 `.chezmoiignore`，不会被 `chezmoi apply` 部署到 `~`。

共同约定：

- **所有破坏性操作默认都是预览**：`clean.sh run` 需加 `--apply`，`backup.sh` 的 `audit`、`restore-etc` 需加 `--apply`。`backup` 例外，它直接写入源目录（可用 `-n` 预览），但不会自动 `git push`。
- 输出到终端时带颜色；重定向到文件或管道时自动去掉颜色。
- 退出码：`0` 成功，`1` 出错或检查未通过，`2` 用法错误。
- `clean.sh` 与 `backup.sh` 只支持 Linux。Windows checkout 需要另写 `.ps1` 入口，不要直接复用这两个脚本；提交检查则两端通用。

---

## clean.sh

### 快速开始

```bash
./scripts/clean.sh scan              # 只读：看各目标占多少、有哪些残留
./scripts/clean.sh run               # 预览 default 层会执行的命令
./scripts/clean.sh run --apply       # 执行 default 层
./scripts/clean.sh run --apply pacman journal docker   # 执行系统级目标（需要 sudo）
```

### 子命令

| 子命令 | 说明 |
|---|---|
| `scan [--deep]` | 只读。依次输出：磁盘与 `~/.cache` 总占用；每个目标的层级、占用、状态；`~/.cache` 里未被任何目标覆盖的大项；残留目录报告（同 `leftovers`）；孤立包与 coredump 概况。加 `--deep` 时按深度清理的路径范围统计占用。 |
| `list` | 列出全部目标及其层级与说明，不计算大小，速度很快。 |
| `run [选项] [目标…]` | 执行清理，默认只预览。不指定目标时等同于 `default`。 |
| `leftovers` | 只读。列出已卸载应用的残留目录，以及超过 `STALE_DAYS` 天无写入的 Electron/Chromium 配置目录。**脚本永远不会删除这些目录**，需要你确认后手动删。 |
| `help` | 输出脚本头部的简要用法。 |

`run` 的选项与目标选择：

| 参数 | 说明 |
|---|---|
| `--apply` | 实际执行。不加时只打印将要执行的命令。 |
| `--deep` | 对支持的目标使用更彻底的清理方式，见下表“`--deep` 行为”一列。 |
| `-x 目标` / `--exclude 目标` | 排除一个目标，可重复使用。注意每个 `-x` 只吃一个参数，后面的参数仍是要选的目标。 |
| `default` | 所有 default 层目标。 |
| `opt` | 所有 opt 层目标。 |
| `all` | default + opt 全部目标。 |
| `<目标 id>` | 单个目标，可写多个，例如 `run uv npm pnpm`。 |

`scan` 表格中的状态：

| 状态 | 含义 |
|---|---|
| 可清理 | 工具已安装，且有数据可清。 |
| 有跳过 | 部分路径因为应用正在运行而跳过，下一行用 `↳` 说明原因。 |
| 无数据 | 工具已安装，但对应缓存为空。 |
| 不适用 | 工具未安装，或缓存目录不存在。 |

### 目标

**default 层**：`run` 不带参数时执行。这些都是可再生缓存，删掉的代价最多是下次重新下载或重建。

| 目标 | 清理动作 | `--deep` 行为 | 跳过条件 |
|---|---|---|---|
| `yay` | `~/.cache/yay`、`~/.cache/paru/clone`：对仍在安装的包的克隆执行 `git clean -ffdx`，只保留 PKGBUILD 和 `.git`；对已卸载包的克隆或非 git 目录，整体删除 | — | yay、paru 或 makepkg 正在运行 |
| `firefox` | 删除每个配置文件的 `cache2`、`startupCache`、`thumbnails` | — | 该配置文件的 `lock` 指向一个存活进程 |
| `electron` | 删除 `~/.config/<App>`（包括其下的 `Default/`、`Profile*/`、`Partitions/*/`）中的 `Cache`、`Code Cache`、`GPUCache`、`DawnGraphiteCache`、`DawnWebGPUCache`、`GPUPersistentCache`、`GrShaderCache`、`ShaderCache`。VS Code 系应用（Code、Code - OSS、VSCodium、Cursor、Windsurf）还会删除 `CachedData`、`CachedExtensionVSIXs`、`CachedProfilesData`、`logs` | — | 应用正在运行，判断方法见下文“运行检测” |
| `uv` | `uv cache prune` | `uv cache clean` | — |
| `pip` | `pip cache purge` | — | — |
| `pnpm` | `pnpm store prune`，并删除 `~/.cache/pnpm` 元数据缓存 | — | — |
| `npm` | `npm cache clean --force`，并删除 `~/.npm/_logs` | 额外删除 `~/.npm/_npx` | — |
| `bun` | `bun pm cache rm` | — | — |
| `yarn` | 删除 `~/.yarn/berry/cache` 与 `~/.cache/yarn`（直接删目录，因为 yarn berry 在项目目录外会拒绝运行） | — | — |
| `go` | `go clean -cache` | 额外执行 `go clean -modcache` | — |
| `cargo` | 删除 `$CARGO_HOME/registry/src` 和 `git/checkouts`；cargo 会从 `.crate` 自动重新解压 | 额外删除 `registry/cache` 和 `git/db` | cargo 正在运行 |
| `nuget` | `dotnet nuget locals http-cache --clear`；没有 dotnet 时直接删除目录 | — | — |
| `vcpkg` | 删除 `$VCPKG_ROOT/buildtrees` 与 `packages`；`installed/` 不受影响 | 额外删除 `downloads` | vcpkg 正在运行 |
| `precommit` | `pre-commit gc`，并删除 1 小时前遗留的 stash 补丁 `patch*`（可能含明文，见[提交检查](#机密性设计)） | `pre-commit clean` | — |
| `lsp` | 删除 `~/.cache/typescript`（tsserver 类型获取缓存）与 `~/.cache/jedi` | — | tsserver、typingsInstaller、jedi-language-server 或 pylsp 正在运行 |
| `wine` | 删除 `~/.cache/wine`（wine-mono、gecko 安装包）与 `~/.cache/winetricks` | — | — |
| `kde` | 删除 `~/.cache/drkonqi/crashes`、`bookmarksrunner`、`kcrash-metadata`、`appstream`、`discover` | — | Discover 正在运行时保留 AppStream 缓存 |
| `thumbnails` | 删除超过 `THUMBNAIL_DAYS` 天未访问的缩略图 | 删除全部缩略图 | — |
| `zcompdump` | 删除不属于当前 zsh 版本的 `~/.zcompdump-*` | — | — |

**opt 层**：只有点名或使用 `all` 时才执行。这些目标要么重建代价高，要么影响系统级状态。

| 目标 | 清理动作 | 不放进 default 的原因 |
|---|---|---|
| `shader` | 删除 `~/.cache/mesa_shader_cache*`、`radv_builtin_shaders`、`qtshadercache-*`、`nvidia/GLCache` | 删除后首次启动游戏或应用会卡顿；而且 Mesa 自带大小上限 |
| `gradle` | 先执行 `gradle --stop`，再删除 `$GRADLE_USER_HOME/caches` 与 `daemon` | 下次构建要重新下载全部依赖；Gradle 8 及以上版本本身会自动清理 30 天未用的条目 |
| `maven` | 删除 `~/.m2/repository`（Maven 正在运行时跳过） | 下次构建要重新下载全部依赖 |
| `trash` | `gio trash --empty`；没有 gio 时直接删除 Trash 目录 | 不可恢复 |
| `pacman` | `sudo paccache -rk$PACCACHE_KEEP`，再执行 `sudo paccache -ruk0` | 需要 sudo；降级时需要旧包 |
| `journal` | `sudo journalctl --vacuum-size=$JOURNAL_MAX` | 需要 sudo；会丢失旧日志 |
| `docker` | `docker builder prune -f`，再执行 `docker image prune -f`（只删 dangling 镜像） | 需要 docker 守护进程在运行 |

> `scan` 中 docker 一行的“占用”取自 `docker system df` 报告的可回收量，包含所有未被使用的镜像；而 `run docker` 只删 dangling 镜像，实际释放的空间可能少得多。

### 运行检测

正在运行的应用会持有缓存，删掉可能导致崩溃，因此脚本按以下优先级判断应用是否在运行：

1. **Chromium/Electron 的 `SingletonLock`**：这是一个符号链接，指向 `主机名-PID`。脚本检查 `/proc/PID` 是否存在。
2. **VS Code 的 `code.lock`**：文件内容是 PID，同样检查该进程是否存在。
3. **Firefox 配置文件的 `lock`**：符号链接，指向 `IP:+PID`。
4. 没有锁文件时，回退到检查当前用户进程的 `argv[0]` 和 `argv[1]` 中是否出现 `/<应用目录名>`。

锁文件的 PID 已经不存在（应用崩溃后遗留的过期锁）时，视为未运行。如果 PID 恰好被其他进程复用，脚本会误判为运行中，结果是跳过清理，这是安全的一侧。

### 不清理的内容

以下内容脚本不会碰：

- **Electron 的 `Service Worker`、`IndexedDB`、`Local Storage`**：它们是网页应用的离线数据或登录状态。例如 Notion 的 `Partitions/notion/Service Worker` 可能超过 1G，但删掉后需要重新同步。
- **`~/.local/share/uv/tools`、`uv/python`、`~/.rustup`、`~/.vscode/extensions`**：这些是已安装的工具与扩展，不是缓存。
- **KDE 的 `ksycoca6_*`、`*.kcache`**：Plasma 运行时通过 mmap 使用它们，由 Plasma 自己维护。
- **`~/.cache/LarkShell`（飞书）**：按用户账号存储，内容语义不明。它会出现在 `scan` 的“未覆盖项”中，供你自行判断。

### 残留目录报告

`leftovers` 包含两部分：

- **按清单检测**：脚本内的 `LEFTOVER_MAP` 每行格式为 `检测名…|路径…`。只要任一检测名是已安装的 pacman 包，或者是 `PATH` 中的命令，就认为应用仍在使用；否则把列出的路径报告为残留。新增条目时直接在数组里加一行，例如：
  ```bash
  "foo-bin foo|.config/Foo .cache/foo"
  ```
- **按时间检测**：`~/.config` 下带有 Chromium 缓存目录的应用目录中，如果 2 层以内没有任何文件在 `STALE_DAYS` 天内被写入，就报告为“久未使用”。

### 环境变量

| 变量 | 默认值 | 作用 |
|---|---|---|
| `THUMBNAIL_DAYS` | `90` | `thumbnails` 目标的天数阈值 |
| `STALE_DAYS` | `180` | `leftovers` 判定“久未使用”的天数 |
| `JOURNAL_MAX` | `1G` | `journal` 目标清理后保留的日志大小 |
| `PACCACHE_KEEP` | `1` | `pacman` 目标每个包保留的版本数 |
| `UNCOVERED_MIN_MB` | `10` | `scan` 列出未覆盖项的最小体积（MB） |

脚本也遵循以下工具自身的位置变量：`XDG_CACHE_HOME`、`XDG_CONFIG_HOME`、`XDG_DATA_HOME`、`CARGO_HOME`、`VCPKG_ROOT`、`GRADLE_USER_HOME`、`PRE_COMMIT_HOME`、`BUN_INSTALL`、`BUN_INSTALL_CACHE_DIR`、`NUGET_HTTP_CACHE_PATH`。

```bash
THUMBNAIL_DAYS=30 PACCACHE_KEEP=2 ./scripts/clean.sh run --apply thumbnails pacman
```

### 示例

```bash
# 只清 JS 生态的包缓存
./scripts/clean.sh run --apply npm pnpm bun yarn

# 清 default 层，但保留 yay 构建目录和 Firefox 缓存
./scripts/clean.sh run --apply -x yay -x firefox

# 彻底清理（default + opt + deep），先预览
./scripts/clean.sh run --deep all
./scripts/clean.sh run --deep --apply all
```

### 定期运行（可选）

下面用 systemd 用户定时器每周执行一次 default 层清理。default 层不需要 sudo，运行中的应用会被自动跳过。`%h` 是 systemd 对家目录的占位符，所以单元文件里不需要写死路径。

`~/.config/systemd/user/clean.service`：

```ini
[Unit]
Description=Weekly cache cleanup

[Service]
Type=oneshot
ExecStart=%h/.local/share/chezmoi/scripts/clean.sh run --apply
```

`~/.config/systemd/user/clean.timer`：

```ini
[Unit]
Description=Weekly cache cleanup

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=1h

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now clean.timer
journalctl --user -u clean.service     # 查看执行日志
```

如果 chezmoi 源目录不在 `~/.local/share/chezmoi`，请修改 `ExecStart` 中的路径。

---

## backup.sh

### 前提

- 已安装 `chezmoi`、`jq`、`git`、`age`、`python3`（3.9 及以上）。
- chezmoi 配置中设置了 `encryption = "age"`、`age.identity`（或 `age.identities`）与 `age.recipient`（或 `age.recipients`）。本仓库的模板见 `private_dot_config/chezmoi/chezmoi.toml.tmpl`。
- **age 私钥必须放在源目录之外，并另行离线备份**（密码管理器、U 盘等）。私钥丢失后，仓库里的所有 `*.age` 文件都无法解密。
- `.chezmoiignore` 中包含 `system/`。脚本每次运行都会检查这一行，缺失则直接退出，否则 `/etc` 的备份会被部署到 `~/system`。

### 快速开始

```bash
./scripts/backup.sh status          # 只读：看有什么待备份、有哪些风险
./scripts/backup.sh backup -n       # 预览
./scripts/backup.sh backup --commit # 备份并提交（不 push）
git -C "$(chezmoi source-path)" push         # 确认 diff 后手动推送
```

### 子命令

| 子命令 | 说明 |
|---|---|
| `status` | 只读。输出：加密配置摘要（接收者、身份文件是否可读）；`chezmoi status` 漂移；未托管的备份候选及其处理方式（plain / encrypt / 跳过）；`/etc` 备份目标；`/etc` 下未合并的 `.pacnew`/`.pacsave` 与历史备份文件；敏感文件权限审计。 |
| `backup [-n] [--no-etc] [--commit]` | 执行备份，流程见下文。 |
| `check` | 只运行安全检查，检查失败时退出码为 1。提交时的自动检查由 pre-commit 负责，见[提交检查](#提交检查pre-commit)。 |
| `audit [--apply]` | 检查敏感文件权限，`--apply` 时收紧：文件改为 600，目录改为 700。 |
| `restore-etc [--apply] [/etc/路径…]` | 把 `system/` 解密到 tmpfs，与当前 `/etc` 对比并显示 diff；`--apply` 时用 `sudo install` 写回。可以只指定部分路径（目录会递归匹配）。 |

`backup` 的选项：

| 选项 | 说明 |
|---|---|
| `-n` / `--dry-run` | chezmoi 以 `--dry-run --verbose` 运行，只显示将要发生的变更；`archpkg.txt` 也不写入。 |
| `--no-etc` | 跳过 `/etc`，也就不需要 sudo。 |
| `--commit` | 安全检查通过后执行 `git add -A` 并提交。不会 push。 |

### backup 流程

1. **预检**：确认启用了 age 加密，配置了接收者，身份文件可读且不在源目录内，`system/` 已被忽略。
2. **re-add 已托管文件**：先扫描所有以明文托管的文件。如果某个文件出现了密钥特征，先用 `chezmoi add --encrypt` 把它转为加密存储，再对全部文件执行 `chezmoi re-add`。
3. **添加候选**：把 `HOME_SECRET` 与 `HOME_TRACK` 中尚未托管的文件加入 chezmoi。目录会展开为其中小于 1 MiB 的普通文件，并按 `HOME_EXCLUDE` 跳过 `*.lock`、`*_history` 等文件。
4. **备份 `/etc`**：见下文。
5. **更新软件包清单**：把 `pacman -Qqe` 的结果写入 `archpkg.txt`，并显示增减的包。
6. **安全检查**：同 `check`。不通过时返回 1，并且不会提交。
7. **提交**：仅在指定 `--commit` 时执行。

### 加密判定

每个文件按以下顺序判断，命中任一条即加密：

1. 文件在 `HOME_SECRET` 或 `ETC_SECRET` 清单中。
2. 对 `/etc` 文件：权限不是所有人可读（例如 600、640）。
3. 文件内容匹配 `hooks/dotfiles_guard.py` 中 `SECRET_RULES` 定义的密钥特征：私钥 PEM 块、`AGE-SECRET-KEY-`，以及 GitHub、OpenAI/Anthropic、AWS、Slack、PyPI、npm、WakaTime、Google API 的 token 格式，还有通用的 `api_key/secret/token/password = 长串` 模式。guard 出错时按“含密钥”处理，宁可多加密。
4. 以 `chezmoi add --secrets error` 明文添加时，被 chezmoi 内置的 gitleaks 规则拦下。

否则以明文存储。另外，内容中含有当前绝对家目录路径的 `~` 文件会被**跳过**并给出警告，建议改写成 chezmoi 模板（用 `{{ .chezmoi.homeDir }}` 代替写死的路径）后再手动添加。

### /etc 备份

`/etc` 使用一个**独立的 chezmoi 实例**：源目录是 `system/`，目标目录是 `$XDG_RUNTIME_DIR` 下的临时目录（tmpfs，退出时删除）。chezmoi 的文件命名规则照常生效，例如：

```
system/etc/pacman.conf                                     ← /etc/pacman.conf（明文）
system/etc/grub.d/executable_40_custom                     ← 可执行位被保留
system/etc/tpm2-tss/fapi-profiles/encrypted_P_RSA3072SHA384.json.age   ← 加密
```

收集的目标：

- **pacman 标记为 `[modified]` 的软件包配置文件**：即你改过的配置。
- **`ETC_EXTRA` 中的路径**：如果是目录，只取其中**不属于任何软件包**的文件，例如自建的 systemd 单元、pacman hook。
- **`ETC_SECRET` 中的路径**：一律加密，通常需要 sudo 才能读。
- 以上都会排除 `ETC_EXCLUDE` 中的路径：账户数据库、`machine-id`、SSH 主机密钥、`*.pacnew`、`*.bak` 等。

root 才能读的文件会通过 `sudo cat` 读进暂存区，因此需要在交互终端中运行，以便输入密码；没有终端时这些文件会被跳过并给出警告。

### 安全检查（check）

`check` 先确认 age 身份文件没有被 git 跟踪，再运行 `hooks/dotfiles_guard.py tree`：对 `git ls-files -co --exclude-standard`（已跟踪的文件，加上未被忽略的新文件）的工作区内容逐个检查，检查项与提交检查中的 `dotfiles-guard` 完全相同，见[检查内容](#检查内容)。

### 恢复

```bash
# 新机器：先把 age 私钥放回 chezmoi.toml 中 identity 指向的位置，然后：
chezmoi init git@github.com:<user>/dotfiles.git
chezmoi diff
chezmoi apply

# /etc：先预览差异，再按需写回
./scripts/backup.sh restore-etc                       # 全部
./scripts/backup.sh restore-etc /etc/pacman.conf /etc/systemd/system
./scripts/backup.sh restore-etc --apply /etc/pacman.conf

# 软件包
sudo pacman -S --needed - < archpkg.txt     # AUR 包需要改用 yay 或 paru
```

`restore-etc` 写回时保持目标文件现有的权限；目标文件不存在时使用备份中的权限，属主为 `root:root`。改动了 `default/grub`、`mkinitcpio.conf` 等文件后，记得执行 `grub-mkconfig -o /boot/grub/grub.cfg` 或 `mkinitcpio -P`。加密文件只提示有差异，不在终端显示内容。

### 自定义清单

脚本开头的数组都可以直接编辑：

| 数组 | 作用 |
|---|---|
| `HOME_TRACK` | 推荐托管的 `~` 配置（相对路径，可以是目录） |
| `HOME_SECRET` | 一律加密的 `~` 配置 |
| `HOME_EXCLUDE` | 展开目录时跳过的文件名模式 |
| `ETC_EXTRA` | 额外纳入的 `/etc` 路径 |
| `ETC_SECRET` | 一律加密的 `/etc` 路径 |
| `ETC_EXCLUDE` | 永不备份的 `/etc` 路径或模式 |

### 已知事项

- `/etc/tpm2-tss/fapi-profiles/*.json` 会被 chezmoi 的通用 API Key 规则误判为含密钥，因此以加密方式存储。这不影响使用。
- 新文件第一次被 `backup` 加入后，就由 `re-add` 维护。如果之后从清单中删掉某个路径，还需要 `chezmoi forget` 才能停止托管。
- `audit` 只检查已知位置：`HOME_SECRET`、`.pypirc`、`.netrc`、`.git-credentials`、`.ssh`、`.gnupg`、age 身份文件及其所在目录。

---

## 提交检查（pre-commit）

仓库使用 [pre-commit](https://pre-commit.com) 框架在 `git commit` 与 `git push` 时自动检查，防止明文密钥进入仓库。它只依赖 Python 与 git，Windows 和 Linux 使用同一份配置。

### 涉及的文件

| 文件 | 是否跟踪 | 作用 |
|---|---|---|
| `.pre-commit-config.yaml` | 跟踪，两端共用 | hook 列表，所有远程 hook 都固定到具体版本 |
| `.gitleaks.toml` | 跟踪，两端共用 | gitleaks 配置：沿用内置规则，补充 WakaTime 规则，跳过 `encrypted_*.age` 密文 |
| `.gitattributes` | 跟踪，两端共用 | 统一 LF 换行（不受 Windows `core.autocrlf` 影响），`*.age`、`*.ptb` 不做换行转换 |
| `.gitignore` | 跟踪，两端共用 | 忽略 `__pycache__/` |
| `scripts/hooks/dotfiles_guard.py` | 跟踪，两端共用 | 本仓库专用规则，只用 Python 标准库 |
| `.git/hooks/pre-commit`、`pre-push` | **不跟踪**，每台机器各自生成 | 由 `pre-commit install` 写入，内含本机 Python 的绝对路径 |
| `$XDG_CACHE_HOME/pre-commit`（默认 `~/.cache/pre-commit`） | 不跟踪 | hook 环境缓存；Windows 上是 `%USERPROFILE%\.cache\pre-commit` |

以 `.` 开头的源文件会被 chezmoi 忽略，`scripts/` 已在 `.chezmoiignore` 中，所以以上文件都不会被部署到 `~`。

### 安装（每台机器一次）

Linux（Arch）：

```bash
sudo pacman -S --needed pre-commit      # 或: uv tool install pre-commit / pipx install pre-commit
cd "$(chezmoi source-path)"
pre-commit install                      # 同时安装 pre-commit 与 pre-push 两个 hook
pre-commit install-hooks                # 可选：预先构建环境（首次会下载 Go 并编译 gitleaks）
```

Windows（PowerShell）：

```powershell
# 任选一种方式安装 pre-commit
uv tool install pre-commit              # 或: pipx install pre-commit
Set-Location (chezmoi source-path)
git config core.autocrlf false          # 可选；.gitattributes 已强制 LF，这一步只是避免警告
pre-commit install
pre-commit install-hooks
```

说明：

- gitleaks 通过 pre-commit 的 `golang` 语言支持安装。机器上没有 Go 时，pre-commit 会自动下载一份到它的缓存目录，不需要另外安装 Go 或 gitleaks。
- 第一次运行需要联网，用于克隆 hook 仓库、下载 Go 与 Go 模块；之后可以离线运行。
- `pre-commit install` 生成的 `.git/hooks/*` 写有本机 Python 路径，不要在两个系统之间复制，也不要提交。

### 检查内容

`git commit` 时（pre-commit 阶段，只检查暂存区中的文件）：

| hook | 来源 | 检查内容 | 不通过时 |
|---|---|---|---|
| `check-illegal-windows-names` | pre-commit-hooks v6.0.0 | `CON`、`AUX` 等保留名，`<>:"\|?*`，以点或空格结尾的路径 | 阻止 |
| `check-case-conflict` | pre-commit-hooks | 只有大小写不同的路径（Windows 上会互相覆盖） | 阻止 |
| `check-added-large-files` | pre-commit-hooks | 新增文件大于 1 MiB | 阻止 |
| `check-merge-conflict` | pre-commit-hooks | 残留的冲突标记 | 阻止 |
| `check-yaml`、`check-toml` | pre-commit-hooks | 语法错误（`.tmpl` 模板不检查） | 阻止 |
| `gitleaks` | gitleaks v8.30.1 | 200 多种通用密钥规则，加上 `.gitleaks.toml` 中的自定义规则；输出中的密钥值显示为 `REDACTED` | 阻止 |
| `prune-precommit-stash` | 本仓库 | 删除 1 小时前遗留的 stash 补丁，并把缓存目录权限收紧为 700（仅 Linux） | 从不阻止 |
| `dotfiles-guard` | 本仓库 | 见下表 | 阻止（警告不阻止） |

`git push` 时（pre-push 阶段）：

| hook | 检查内容 |
|---|---|
| `dotfiles-guard-push` | 对**每一个**即将推送的提交，检查其新增或修改的文件。这能拦住用 `git commit --no-verify` 跳过检查的提交；即使后续提交删掉了密钥也一样，因为历史中的密钥同样会被推送出去 |
| `check-added-large-files` | 上游清单把它声明在 pre-push 阶段也运行，同样能拦住跳过检查提交的大文件 |

`dotfiles-guard` 的规则（`staged`、`push`、`tree` 三种模式相同）：

| 规则 | 级别 | 说明 |
|---|---|---|
| `age-not-ciphertext` | 错误 | `*.age` 文件的开头不是 `-----BEGIN AGE ENCRYPTED FILE-----` 或 `age-encryption.org/v1` |
| `age-armor-truncated` | 错误 | ASCII 封装的 age 文件缺少结束行 |
| `age-without-encrypted-prefix` / `encrypted-prefix-without-age` | 错误 | chezmoi 的 `encrypted_` 前缀与 `.age` 后缀没有成对出现 |
| `must-encrypt(<目标>)` | 错误 | 按 chezmoi 命名规则把源路径换算成目标路径（例如 `private_dot_ssh/private_id_ed25519` → `.ssh/id_ed25519`），目标属于必须加密的类别却没有加密。清单见脚本中的 `ENCRYPT_REQUIRED` 与 `ENCRYPT_EXEMPT` |
| 各密钥规则名（如 `github-token`） | 错误 | 明文文件匹配 `SECRET_RULES`；`backup.sh` 用的是同一套规则 |
| `absolute-home-path` | 警告 | 含 Linux、macOS 或 Windows（`C:\Users\<用户名>`）的绝对家目录路径，在另一台机器上无效 |

`ENCRYPT_REQUIRED` 目前覆盖：`.ssh/*`（`*.pub` 与 `known_hosts*` 除外）、`.gnupg/*`、`.pypirc`、`.npmrc`、`.netrc`/`_netrc`、`.git-credentials`、`.docker/config.json`、gh 的 `hosts.yml`（Linux 与 Windows 路径）、`.wakatime.cfg`、`.aws/credentials`、`.kube/config`、rclone 配置、`*.pem`/`*.key`/`*.p12`/`*.pfx`、`id_rsa`/`id_ecdsa`/`id_ed25519`、`.env*`（`.env.example` 除外），以及 `system/` 下的 `/etc/shadow*`、`/etc/sudoers*`、`/etc/crypttab`、NetworkManager 连接、WireGuard、SSH 主机密钥、mihomo 配置。

### 被拦下时怎么办

输出只包含 `文件:行号` 和规则名，不会显示密钥本身。例如：

```
dotfiles guard (staged)..................................................Failed
error:   dot_bashrc:19: [github-token]
```

按情况处理：

| 情况 | 处理 |
|---|---|
| 敏感文件被明文添加了 | `git restore --staged <源文件>`，然后 `chezmoi forget <目标>`，再 `chezmoi add --encrypt <目标>` |
| 明文配置里混进了一个 token | 把 token 移到加密文件或环境变量中；或者把整个文件改为加密托管 |
| 误报 | 在该行末尾加注释 `gitleaks:allow`（gitleaks 与 guard 都认这个标记），或者修改 `dotfiles_guard.py` / `.gitleaks.toml` 的规则 |
| 已用 `--no-verify` 提交但还没推送 | pre-push 会拦下。用 `git reset --soft <上一个干净的提交>` 重新整理后再提交 |
| 已经推送 | 立刻**轮换（作废并重新生成）该凭据**。改写 git 历史无法撤回已经公开的内容 |

临时跳过某个 hook（例如没有网络、无法安装 gitleaks 时）：

```bash
SKIP=gitleaks git commit -m "..."        # Linux：跳过指定 hook，其余照常运行
```

```powershell
$env:SKIP = "gitleaks"; git commit -m "..."; Remove-Item Env:SKIP   # Windows PowerShell
```

不要用 `--no-verify` 跳过全部检查；即使用了，pre-push 也会再检查一次。

### 手动运行

```bash
pre-commit run --all-files              # 对全仓库运行 pre-commit 阶段的所有 hook
pre-commit run gitleaks --all-files     # 只运行某一个 hook
pre-commit run --hook-stage pre-push --from-ref origin/archlinux --to-ref HEAD   # 模拟推送检查
python scripts/hooks/dotfiles_guard.py tree           # 只运行本仓库规则（等同于 backup.sh check 的内容部分）
pre-commit autoupdate                   # 升级远程 hook 版本；之后检查 diff 并在两台机器上分别重新运行
```

### 机密性设计

- **不回显密钥**：gitleaks 使用 `--redact`；`dotfiles_guard.py` 只输出文件、行号和规则名。两者都不写报告文件。
- **只读暂存区**：pre-commit 阶段读取的是 index 中的 blob，而不是工作区文件，所以“只暂存一部分”无法绕过检查；pre-push 阶段读取的是每个提交里的 blob。读出的内容只留在内存中。
- **stash 补丁**：pre-commit 在运行 hook 前会把**未暂存**的改动保存为 `$PRE_COMMIT_HOME/patch*`，而且之后从不删除。如果当时工作区里有尚未加密的敏感内容，它会以明文留在这个补丁中。对应措施：`prune-precommit-stash` 每次提交时删除 1 小时前的补丁，并把目录权限设为 700（Linux）；`clean.sh run precommit` 也会删除它们。之所以保留最近 1 小时的补丁，是为了不影响正在进行的提交（pre-commit 需要用补丁恢复你的工作区）。因此在明文状态下提交后，当次的补丁最长会保留到下一次超过 1 小时后的提交。急需清除时可以运行：
  ```bash
  python scripts/hooks/dotfiles_guard.py prune-stash --max-age-minutes 0   # 确认当前没有正在进行的 git commit
  ```
- **密文不被扫描，但会被校验**：`.gitleaks.toml` 让 gitleaks 跳过 `encrypted_*.age`，避免对随机密文误报。guard 会单独确认这些文件确实是 age 密文，所以把明文改名为 `encrypted_x.age` 无法绕过检查。
- **私钥不进仓库**：`AGE-SECRET-KEY-` 与 PEM 私钥块在任何明文文件中都会被拦下；`backup.sh check` 还会确认 chezmoi 配置的 age 身份文件没有被 git 跟踪。
- **最后一道防线不在本地**：本地 hook 可以被卸载或跳过。如果仓库托管在 GitHub，建议在仓库设置中开启 *Secret scanning* 与 *Push protection*，在服务器端再拦一次。

### 跨平台说明

- 换行：`.gitattributes` 强制仓库内与检出后都使用 LF，Windows 上的 `core.autocrlf` 不影响。`*.age` 与 PowerToys 的 `*.ptb` 设为 `-text`，原样保存不做转换；`*.bat`、`*.cmd` 使用 CRLF。
- 路径：guard 统一使用 `/` 处理路径，并识别 Windows 下的目标路径（如 `AppData/Roaming/GitHub CLI/hosts.yml`、`_netrc`）。
- 控制台编码：guard 输出遇到当前控制台编码无法表示的字符时会转义，不会因此崩溃。
- `check-illegal-windows-names` 与 `check-case-conflict` 在 Linux 上提前拦下 Windows 无法检出的路径。
- **`windows` 分支**：它与 `archlinux` 是没有共同历史的独立分支，上面这些文件不会自动出现在那里。需要在 Windows 的 checkout 中从 `archlinux` 分支取出同一份文件（内容无需修改）：
  ```powershell
  git fetch origin
  git checkout origin/archlinux -- .pre-commit-config.yaml .gitleaks.toml .gitattributes .gitignore scripts/hooks/dotfiles_guard.py
  Add-Content .chezmoiignore "scripts/"    # windows 分支的 .chezmoiignore 中还没有这一行
  git add --renormalize .                  # 按新的 .gitattributes 归一化已有文件；检查 git diff --cached 后再提交
  pre-commit install
  ```
  以后修改这些文件时，两个分支要同步更新。

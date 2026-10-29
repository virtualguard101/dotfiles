# scripts/

本目录存放两个相互独立的维护脚本，均为 Arch Linux 编写，使用 Bash：

| 脚本 | 职责 | 是否修改系统 |
|---|---|---|
| [`clean.sh`](clean.sh) | 清理 `~/.cache` 及本机已安装工具的包缓存、应用缓存；报告残留目录 | `run --apply` 时删除缓存 |
| [`backup.sh`](backup.sh) | 基于 chezmoi 备份 `~` 与 `/etc`，敏感文件用 age 加密，提交前做安全检查 | `backup` 时写入 chezmoi 源目录 |

两者没有共享代码，可以单独复制、单独运行。`scripts/` 已写入 `.chezmoiignore`，不会被 `chezmoi apply` 部署到 `~`。

共同约定：

- **所有破坏性操作默认都是预览**：`clean.sh run` 需加 `--apply`，`backup.sh` 的 `audit`、`restore-etc` 需加 `--apply`。`backup` 例外，它直接写入源目录（可用 `-n` 预览），但不会自动 `git push`。
- 输出到终端时带颜色；重定向到文件或管道时自动去掉颜色。
- 退出码：`0` 成功，`1` 出错或检查未通过，`2` 用法错误。
- 只支持 Linux。Windows checkout 需要另写 `.ps1` 入口，不要直接复用这两个脚本。

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
| `precommit` | `pre-commit gc` | `pre-commit clean` | — |
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

- 已安装 `chezmoi`、`jq`、`git`、`age`。
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
| `check` | 只运行安全检查，检查失败时退出码为 1。适合挂到 git pre-commit hook。 |
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
3. 文件内容匹配脚本内置的密钥特征：私钥 PEM 块、`AGE-SECRET-KEY-`，以及 GitHub、OpenAI/Anthropic、AWS、Slack、PyPI、npm、WakaTime、Google API 的 token 格式，还有通用的 `api_key/secret/token/password = 长串` 模式。
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

对 `git ls-files -co --exclude-standard`（已跟踪的文件，加上未被忽略的新文件）逐个检查：

| 检查项 | 结果 |
|---|---|
| age 身份文件被 git 跟踪 | 失败 |
| `*.age` 文件不以 `-----BEGIN AGE ENCRYPTED FILE-----` 或 `age-encryption.org/v1` 开头 | 失败 |
| 明文文件匹配密钥特征 | 失败，并给出行号（不打印内容） |
| 明文文件含绝对家目录路径 | 只警告 |

接入 git pre-commit hook（hook 不会被 git 跟踪，需要在每台机器上各自创建一次）：

```bash
cat > "$(chezmoi source-path)/.git/hooks/pre-commit" <<'EOF'
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/scripts/backup.sh" check
EOF
chmod +x "$(chezmoi source-path)/.git/hooks/pre-commit"
```

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

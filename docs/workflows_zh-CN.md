# 工作流

各分支的用途，以及不同类型的改动各走哪条路径。命令细节见 [reference_zh-CN.md](reference_zh-CN.md)。

---

## 1. 分支定位

| 分支 | 定位 | 去留 |
|---|---|---|
| `main` | 唯一的真实来源：bootstrap 克隆它，每台机器的 chezmoi 工作目录也停在它上面 | 长期保留；必须随时可用于恢复 |
| `dev` | 工具、文档、规则的开发分支，通过 PR 合并进 `main` | 长期保留；每次合并后与 `main` 对齐（[4](#4-让-dev-跟上-main)） |
| `archlinux` / `windows` | 旧布局时代的平台分支，内容已全部并入 `main` | 所有机器迁移完成后删除（[5](#5-一次性将机器迁出旧平台分支)） |

---

## 2. 按改动类型分流

### 2.1 日常备份：在各机器上直接提交到 `main`

dotfile 内容、`packages/` 下的清单、`/etc` 备份，以及对 `dotfiles.toml` 的新增条目或阈值调整（`dotfiles config add backup.linux.track …`、`… ignore …`、`config set clean.…`）：

```bash
git pull --rebase
uv run dotfiles backup --commit
git push
```

不走 PR 的理由：

- 它们是频繁产生的数据快照，不是代码；
- 安全检查已由 pre-commit 和 pre-push hook 执行，PR 不会多带来什么；
- 每台机器只写本平台的目标和清单，冲突很少见。

唯一常见的冲突点是 `platforms.toml`：两台机器同时登记了新目标。两边的条目都保留，再运行 `uv run dotfiles check`。

### 2.2 工具、规则、共享配置：`dev` → PR → `main`

包括 `src/`、`tests/`、`bootstrap.*`、`.chezmoiignore`、`.chezmoi.toml.tmpl`、共享模板（如 `.gitconfig.tmpl`）、在 `platforms.toml` 中调整目标的平台归属，从 `dotfiles.toml` 的保护性清单（`backup.<os>.secret`、`etc.secret`、`etc.exclude`）中删除条目，以及 guard 规则。这里出错会影响两个平台的恢复。合并前至少做：

```bash
uv run pytest && uv run dotfiles check
uv run dotfiles restore                    # 只看计划
# 涉及 Windows 时，查看 Windows 视角下会部署什么：
chezmoi --source . --override-data '{"chezmoi":{"os":"windows"}}' managed
```

用 **merge commit** 方式合并：PR 选择这种方式，或者在 `main` 上本地运行 `git merge --no-ff dev`（[3](#3-在主检出中切换到-dev-开发)）。squash 和 rebase 合并会生成新的提交哈希，之后长期存在的 `dev` 就无法快进，会一直和 `main` 分叉。

### 2.3 恢复过程中发现问题：修在 `main` 上

新机器上运行的就是 `main`。在那里修复、推送，重跑 `restore`，再把修复同步回 `dev`（[4](#4-让-dev-跟上-main)）。

---

## 3. 在主检出中切换到 `dev` 开发

开发就在 `~/.local/share/chezmoi` 中进行：先提交或暂存 `main` 上的改动，再 `git switch dev`（先让它跟上 `main`，见 [4](#4-让-dev-跟上-main)）。这个检出就是 chezmoi 生效中的源目录，所以停在 `dev` 上时：

| 命令 | 停在 `dev` 上时 |
|---|---|
| `pytest`、`check`、`restore`（只看计划）、`apply`（预览）、`backup -n` | 可以放心运行：它们只读取 |
| `apply --apply`、`restore --apply` | 会把尚未合并的状态部署到 `~`：只在 `dev` 没有改动 `home/` 下的文件时（`git diff --stat main dev -- home` 为空），或者明确需要时运行 |
| `backup`（不带 `-n`） | 写进 `dev` 的 `home/`：日常备份在 `main` 上做 |

完成后 `git switch main`，用 merge commit 合并：通过 PR，或者在本地 `git merge --no-ff dev`（没有登录 `gh` 的机器用后者），然后推送。离开这台机器前切回 `main`。

---

## 4. 让 `dev` 跟上 `main`

日常备份直接进 `main`，所以 `dev` 会持续落后。每次开始开发前：

```bash
git switch dev
git merge --ff-only origin/main    # dev 没有未合并的提交
git rebase origin/main             # dev 有未合并的提交
```

---

## 5. 一次性：将机器迁出旧平台分支

检出仍在 `archlinux` 或 `windows` 上的机器使用的是旧布局（dotfiles 在仓库顶层，没有 `.chezmoiroot`）。迁移到 `main`：

1. 检查本地改动：`git status` 和 `git log origin/<分支>..<分支>`。如果有远程没有的提交，先推送并合并进 `main`。
2. `git fetch`，然后 `git switch main`。
3. `uv run dotfiles doctor`，再运行 `uv run dotfiles restore`（只看计划）。
4. `uv run dotfiles restore --apply --only config`：旧的 chezmoi 配置先被备份，再由模板重新生成。旧的私钥位置仍能被识别（[reference_zh-CN.md 2](reference_zh-CN.md#私钥位置)）。
5. 用 `chezmoi diff` 和 `uv run dotfiles backup -n` 检查漂移，按 [reference_zh-CN.md 6.3](reference_zh-CN.md#63-需要手动合并的场景) 处理。
6. `uv run dotfiles restore --apply --only dotfiles`。

迁移中发现的问题修在 `main` 上（[2.3](#23-恢复过程中发现问题修在-main-上)）。

所有机器都迁到 `main` 并验证后，删除旧分支：

```bash
git push origin --delete archlinux windows
git branch -d archlinux windows
git remote set-head origin -a      # origin/HEAD 仍指向旧分支时刷新它
```

---

## 6. 同一系统的两台机器（Arch + Kali）

两台 Linux 共用所有没有被标签区分的 `linux` 目标（reference [3.3](reference_zh-CN.md#33-平台归属)），以及 `dotfiles.toml` 中的清单。各自的情况见 [arch.md](arch.md) 和 [kali.md](kali.md)。

- **apply 前先预览**：`git pull`，`uv run dotfiles apply` 预览，再 `uv run dotfiles apply --apply` 部署。被覆盖或删除的文件会先备份到 `~/.local/state/dotfiles/pre-apply-*`。
- **备份前先预览**：`uv run dotfiles backup -n`。`backup.linux` 两台机器共用，一台上新出现的候选也会部署到另一台。不想要的条目用 `dotfiles config add backup.linux.ignore <路径>` 排除，或者给它加上标签。
- **两台机器共用同一个源文件**：一台上 re-add 的改动，会在另一台 apply 时覆盖。内容需要不同的文件，要么改成模板（按 `.chezmoi.osRelease.id` 或 `.tags` 区分），要么用标签分开。
- **`/etc`**：backup 只写入本机发行版的 `system/<ID>/etc`，但 `etc.extra` / `etc.secret` / `etc.exclude` 两边共用，排除一条路径之前，先确认另一台机器上没有同名文件。读取 `/etc` 需要 sudo，不备份时加 `--no-system`。
- **提交工具改动前跑测试**：`uv run pytest`，并检查它自己的退出码。`uv run pytest | tail -1 && git commit` 判断的是 `tail` 的退出码，失败会被吞掉。

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

PR 用 **merge commit** 方式合并。squash 和 rebase 合并会生成新的提交哈希，之后长期存在的 `dev` 就无法快进，会一直和 `main` 分叉。

### 2.3 恢复过程中发现问题：修在 `main` 上

新机器上运行的就是 `main`。在那里修复、推送，重跑 `restore`，再把修复同步回 `dev`（[4](#4-让-dev-跟上-main)）。

---

## 3. 开发时不切换生效中的检出

`~/.local/share/chezmoi` 是生效中的源目录。把它切到 `dev` 后，`chezmoi apply` 部署的就是尚未合并的状态。开发时改用 git worktree：

```bash
git worktree add ~/dev/dotfiles-dev dev    # chezmoi 源目录之外的任意路径均可
```

`dotfiles` 调用 chezmoi 时传的是 `--source <工具自身所在的仓库>`，所以在 worktree 里运行的命令作用于 worktree：

| 在 worktree 中运行 | 效果 |
|---|---|
| `pytest`、`check`、`restore`（只看计划）、`backup -n` | 可放心用于测试 |
| `doctor` | 把"源目录"一项报告为 fail，因为 chezmoi 配置里的是主检出——这是预期的 |
| `backup`（不带 `-n`） | 写进 worktree 的 `home/` |
| `restore --apply` | 把 `dev` 的状态部署到 `~`——只在明确需要时运行 |

用完后用 `git worktree remove ~/dev/dotfiles-dev` 删除。

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

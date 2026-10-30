# Workflows

How branches are used and which path each kind of change takes. Command details are in
[reference.md](reference.md).
---

## 1. Branch roles

| Branch | Role | Lifetime |
|---|---|---|
| `main` | the single source of truth: bootstrap clones it, and every machine's chezmoi working directory stays on it | permanent; must always be able to restore a machine |
| `dev` | development of the tool, docs and rules; merged into `main` through PRs | permanent; realigned with `main` after every merge ([4](#4-keeping-dev-in-step-with-main)) |
| `archlinux` / `windows` | platform branches from the old layout, fully merged into `main` | deleted once every machine has migrated ([5](#5-one-time-migrating-a-machine-off-the-old-platform-branches)) |

---

## 2. Which path a change takes

### 2.1 Daily backups: commit straight to `main`, on each machine

Dotfile content, the manifests in `packages/` and the `/etc` backup:

```bash
git pull --rebase
uv run dotfiles backup --commit
git push
```

Why no PR:

- these are frequent data snapshots, not code;
- the pre-commit and pre-push hooks already enforce the security checks, so a PR adds nothing;
- each machine writes its own platform's targets and manifests, so conflicts are rare.

The one common conflict is `platforms.toml`, when both machines register new targets at the same time. Keep the entries from both sides, then run `uv run dotfiles check`.

### 2.2 Tool, rules and shared config: `dev` -> PR -> `main`

This covers `src/`, `tests/`, `bootstrap.*`, `.chezmoiignore`, `.chezmoi.toml.tmpl`, shared templates (e.g. `.gitconfig.tmpl`), moving a target between platforms in `platforms.toml`, and the guard rules. A mistake here breaks recovery on both platforms. Before merging, at least:

```bash
uv run pytest && uv run dotfiles check
uv run dotfiles restore                    # plan only
# when Windows is affected, see what the Windows view would deploy:
chezmoi --source . --override-data '{"chezmoi":{"os":"windows"}}' managed
```

Merge PRs with a **merge commit**. Squash and rebase merges rewrite commit hashes, after which the long-lived `dev` can no longer fast-forward and diverges from `main` for good.

### 2.3 A problem found during recovery: fix it on `main`

A fresh machine runs `main`. Fix the problem there, push, rerun `restore`, then bring the fix back into `dev` ([4](#4-keeping-dev-in-step-with-main)).

---

## 3. Developing without switching the live checkout

`~/.local/share/chezmoi` is the live source directory. Switching it to `dev` means `chezmoi apply` deploys unmerged state. Develop in a git worktree instead:

```bash
git worktree add ~/dev/dotfiles-dev dev    # any path outside the chezmoi source dir works
```

`dotfiles` calls chezmoi with `--source <the repo the tool itself lives in>`, so commands run in the worktree act on the worktree:

| Command in the worktree | Effect |
|---|---|
| `pytest`, `check`, `restore` (plan), `backup -n` | safe for testing |
| `doctor` | reports "source dir" as fail, because chezmoi's config names the main checkout — expected |
| `backup` (without `-n`) | writes into the worktree's `home/` |
| `restore --apply` | deploys the `dev` state into `~` — run it only on purpose |

Remove the worktree when done with `git worktree remove ~/dev/dotfiles-dev`.

---

## 4. Keeping `dev` in step with `main`

Daily backups land on `main` directly, so `dev` keeps falling behind. Before starting work:

```bash
git switch dev
git merge --ff-only origin/main    # dev has no unmerged commits
git rebase origin/main             # dev has unmerged commits
```

---

## 5. One time: migrating a machine off the old platform branches

A machine whose checkout is still on `archlinux` or `windows` uses the old layout (dotfiles at the repo top, no `.chezmoiroot`). To move it to `main`:

1. Check for local work: `git status` and `git log origin/<branch>..<branch>`. If there are commits the remote does not have, push them and merge them into `main` first.
2. `git fetch` and `git switch main`.
3. `uv run dotfiles doctor`, then `uv run dotfiles restore` (plan only).
4. `uv run dotfiles restore --apply --only config`: the old chezmoi config is backed up, then regenerated from the template. Old key locations are still recognized ([reference.md 2](reference.md#private-key-location)).
5. Check drift with `chezmoi diff` and `uv run dotfiles backup -n`; handle it as in [reference.md 6.3](reference.md#63-cases-that-need-a-manual-merge).
6. `uv run dotfiles restore --apply --only dotfiles`.

Problems found here are fixed on `main` ([2.3](#23-a-problem-found-during-recovery-fix-it-on-main)).

Once every machine is on `main` and verified, delete the old branches:

```bash
git push origin --delete archlinux windows
git branch -d archlinux windows
git remote set-head origin -a      # refresh origin/HEAD if it still points at an old branch
```

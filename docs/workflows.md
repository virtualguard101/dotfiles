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

Dotfile content, the manifests in `packages/`, the `/etc` backup, and `dotfiles.toml` edits that add entries or tune thresholds (`dotfiles config add backup.linux.track …`, `… ignore …`, `config set clean.…`):

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

This covers `src/`, `tests/`, `bootstrap.*`, `.chezmoiignore`, `.chezmoi.toml.tmpl`, shared templates (e.g. `.gitconfig.tmpl`), moving a target between platforms in `platforms.toml`, removing entries from the protective lists in `dotfiles.toml` (`backup.<os>.secret`, `etc.secret`, `etc.exclude`), and the guard rules. A mistake here breaks recovery on both platforms. Before merging, at least:

```bash
uv run pytest && uv run dotfiles check
uv run dotfiles restore                    # plan only
# when Windows is affected, see what the Windows view would deploy:
chezmoi --source . --override-data '{"chezmoi":{"os":"windows"}}' managed
```

Merge with a **merge commit**: a PR merged that way, or locally `git merge --no-ff dev` on `main` ([3](#3-developing-on-dev-in-the-live-checkout)). Squash and rebase merges rewrite commit hashes, after which the long-lived `dev` can no longer fast-forward and diverges from `main` for good.

### 2.3 A problem found during recovery: fix it on `main`

A fresh machine runs `main`. Fix the problem there, push, rerun `restore`, then bring the fix back into `dev` ([4](#4-keeping-dev-in-step-with-main)).

---

## 3. Developing on `dev` in the live checkout

Development happens in `~/.local/share/chezmoi` itself: commit or stash what is on `main`, then `git switch dev` (catching up first, [4](#4-keeping-dev-in-step-with-main)). That checkout is chezmoi's live source directory, so while it is on `dev`:

| Command | While on `dev` |
|---|---|
| `pytest`, `check`, `restore` (plan), `apply` (preview), `backup -n` | safe: they only read |
| `apply --apply`, `restore --apply` | deploy the unmerged state into `~`: only when `dev` changes nothing under `home/` (`git diff --stat main dev -- home` is empty), or on purpose |
| `backup` (without `-n`) | writes into `dev`'s `home/`: do daily backups on `main` |

When done: `git switch main`, then merge with a merge commit, either through a PR or locally with `git merge --no-ff dev` (a machine without `gh` login does the latter), and push. Switch back to `main` before leaving the machine.

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

---

## 6. Two machines on the same OS (Arch + Kali)

Both Linux machines share every `linux` target that no tag separates (reference [3.3](reference.md#33-platform-ownership)), and the lists in `dotfiles.toml`. Each machine's specifics are in [arch.md](arch.md) and [kali.md](kali.md).

- **Preview before applying**: `git pull`, `uv run dotfiles apply` to preview, then `uv run dotfiles apply --apply`. Overwritten and removed files are backed up to `~/.local/state/dotfiles/pre-apply-*` first.
- **Preview before backing up**: `uv run dotfiles backup -n`. `backup.linux` is shared, so a candidate that shows up on one machine deploys on the other too. Exclude it with `dotfiles config add backup.linux.ignore <path>`, or give it a tag.
- **One source file for both**: a change re-added on one machine overwrites the other's copy at its next apply. A file whose content has to differ becomes a template (on `.chezmoi.osRelease.id` or `.tags`), or the two copies are separated by tags.
- **`/etc`**: backup writes only this distro's `system/<ID>/etc`, but `etc.extra` / `etc.secret` / `etc.exclude` are shared: before excluding a path, check the other machine has no file of that name. Reading `/etc` needs sudo; `--no-system` skips it.
- **Before committing tool changes**: `uv run pytest`, and check its own exit status: `uv run pytest | tail -1 && git commit` tests `tail`'s status, so a failure slips through.

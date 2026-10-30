# dotfiles

An Arch Linux and Windows config repository built on [chezmoi](https://www.chezmoi.io), plus a
uv-managed Python tool `dotfiles` for environment checks, post-reinstall recovery, daily backups
and cache cleanup. Both platforms share one branch and one set of commands.

> The repo is **public**. All sensitive files are stored age-encrypted (`encrypted_*.age`); the
> decryption key is **not** in the repo and must be backed up offline (USB drive / password
> manager). If the key is lost, none of the encrypted files can be recovered.

Chinese documentation: [README_zh-CN.md](README_zh-CN.md) · [docs/reference_zh-CN.md](docs/reference_zh-CN.md)

## Repository layout

```
.chezmoiroot              contains "home": chezmoi treats home/ as the source state
home/                     dotfiles for both platforms (chezmoi source state)
  .chezmoi.toml.tmpl      generates the chezmoi config at init (encryption, key path, recipient)
  .chezmoidata/platforms.toml   platform ownership per target; .chezmoiignore derives from it
system/                   Linux /etc backup (separate chezmoi instance, never deployed to ~)
packages/                 package manifests: archpkg.txt / scoopfile.json / winget.json
src/dotfiles/             the Python tool (standard library only)
bootstrap.sh / .ps1       fresh-machine bootstrap: installs git, chezmoi, uv and clones the repo,
                          then hands over to dotfiles restore
docs/reference.md         detailed reference: command behavior, encryption and platform rules,
                          cleanup targets, commit checks
```

Tool files at the repo top are not inside `home/`, so `chezmoi apply` never deploys them into the
home directory.

## Post-reinstall recovery

You need: the **offline-stored age key file** (`AGE-SECRET-KEY-1…`) and network access to GitHub.
If you need a proxy, set `HTTPS_PROXY` in the current terminal first.

### Arch Linux

```bash
curl -fsSLO https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.sh
less bootstrap.sh                          # read it before running
bash bootstrap.sh                          # shows the recovery plan only
bash bootstrap.sh --apply                  # execute (asks for the key file; or pass --key-file /path)
```

### Windows

In a regular (non-admin) PowerShell:

```powershell
irm https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.ps1 -OutFile bootstrap.ps1
notepad bootstrap.ps1                      # read it before running
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1            # plan only
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 --apply    # execute
```

Do not pipe straight into `curl … | bash` or `irm … | iex`: stdin is then not a terminal and you
cannot type the key path interactively.

### What the recovery flow does

bootstrap only installs git, chezmoi and uv (pacman on Arch, winget on Windows) and clones the
public repo over HTTPS into `~/.local/share/chezmoi`; everything else is done by
`dotfiles restore`:

| Step | What it does |
|---|---|
| `key` | writes the key to `~/.config/chezmoi/key.txt` (owner-only permissions) and validates it against the repo's public key |
| `config` | generates the chezmoi config from `home/.chezmoi.toml.tmpl` |
| `dotfiles` | rehearses decryption once (nothing is written on failure), backs up files about to be overwritten to `~/.local/state/dotfiles/pre-apply-*`, then runs `chezmoi apply` |
| `system` | Linux: restores `/etc`; hardware-bound files (`fstab`, `crypttab`, `default/grub`, ...) show diffs only; `sudoers` is validated with `visudo` first |
| `packages` | Linux: pacman installs packages from configured repos, AUR packages go to paru/yay; Windows: `winget import` and `scoop import` |
| `externals` | fetches external repos such as the nvim config; temporarily connects directly when the git proxy is unreachable |
| `ssh` | generates any private key referenced by `~/.ssh/config` that is missing (keys are never backed up), and can register the GitHub public key via `gh` |
| `shell` | Linux: sets the login shell to zsh |
| `hooks` | installs the commit-check hooks; repo and externals use HTTPS for fetch, push is switched to SSH |
| `verify` | `chezmoi verify`, the security check, and the permission audit |

Every step re-detects the current state before running; completed steps are skipped automatically.
Any failure stops the flow — fix the problem and rerun the same command to continue from that step.
Steps that install packages, modify `/etc`, change the shell or register keys ask for confirmation
(`--yes` skips). To run only some steps use `--only`, e.g. `--only key,config,dotfiles`; to skip
some use `--skip`.

After recovery, the output lists items that need manual attention, usually: `authorized_keys` on
your other servers (for the newly generated SSH keys) and the programs in
`packages/winget-unavailable.txt` that are not in any winget source.

## Daily use

```bash
uv run dotfiles doctor                 # environment: toolchain, encryption config, repo and hooks, missing packages
uv run dotfiles backup -n              # preview a backup
uv run dotfiles backup --commit        # backup and commit (no push); then git push
uv run dotfiles clean scan             # see what caches can be cleaned
uv run dotfiles clean run --apply      # clean the default tier
```

With [just](https://github.com/casey/just) installed there are shortcuts: `just doctor`,
`just backup -n`, `just clean scan`, etc.; see `just --list`.

| Command | What it does |
|---|---|
| `doctor [--json]` | environment detection (read-only); exit code 1 when a required condition is missing |
| `restore [--apply] [--only …] [--skip …] [--key-file path] [--yes]` | post-reinstall recovery; shows the plan by default |
| `status` | backup state: drift, unmanaged candidates, package manifests, permissions |
| `backup [-n] [--no-system] [--commit]` | re-add managed files -> add candidates -> `/etc` (Linux) -> package manifests -> security check |
| `check` | security check: plaintext secrets, `.age` format, must-encrypt targets, platform ownership |
| `audit [--apply]` | sensitive-file permissions (Linux 600/700, Windows ACL) |
| `system-restore [--apply] [/etc/path…]` | Linux: compare `system/` with the current `/etc` and install as needed |
| `clean scan / list / run / leftovers` | cache cleanup; `run` previews by default, `--apply` executes |

Every modifying command previews by default. Full details in
[docs/reference.md](docs/reference.md).

## Security design

- **The private key never enters the repo**: only the public key is stored; `check` and the commit
  checks block any plaintext private key (`AGE-SECRET-KEY-`, PEM private key blocks) and any
  git-tracked key file.
- **Sensitive files are always encrypted**: at backup time the decision to encrypt is made by three
  layers — the manifest lists, the secret-pattern scan, and chezmoi's built-in scan. Commits are
  rejected when SSH configs, credential files, `sudoers` etc. are not encrypted. Encrypted files
  deploy with 600 permissions.
- **Double checks at commit and push**: the pre-commit stage inspects the index; the pre-push stage
  inspects every commit about to be pushed (commits made with `--no-verify` are caught too). Output
  contains only file, line number and rule name — never the secret.
- **Platform isolation**: every target declares its owner in `platforms.toml`; the other platform's
  targets (and their exclusive parent directories) are ignored, so a backup on one platform never
  overwrites the other platform's source files.
- **Recovery never silently destroys existing files**: decryption is rehearsed first, then files
  about to be overwritten are backed up; hardware-bound `/etc` files are never installed
  automatically.
- **Transient plaintext only on tmpfs**: staging for the `/etc` backup and restore lives in
  `$XDG_RUNTIME_DIR` and is deleted afterwards.
- **Server-side backstop**: local hooks can be uninstalled; enable Secret scanning and Push
  protection in the GitHub repo settings as well.

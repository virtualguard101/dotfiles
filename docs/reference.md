# Reference

Behavior, rules and tunables of the `dotfiles` commands. Quick start in the top-level
[README.md](../README.md). Chinese version: [reference_zh-CN.md](reference_zh-CN.md).

Run everything through uv: `uv run dotfiles <command>` (inside the repo), or
`uv run --project ~/.local/share/chezmoi --frozen --no-dev dotfiles <command>` (anywhere). The tool
depends only on the Python standard library; with `--no-dev` it never touches PyPI.

Conventions:

- Modifying commands preview by default: `restore`, `clean run`, `audit`, `system-restore` need
  `--apply`; `backup` writes to the repo directly (`-n` previews) but never pushes.
- Exit codes: `0` success, `1` failure or failed check, `2` usage error (unknown target, step, etc.).
- Output is colored on a terminal; plain with `NO_COLOR` set or when redirected.

---

## 1. Environment detection: doctor

Read-only. Checks, in order:

| Section | Contents |
|---|---|
| System | distribution and kernel / Windows version, desktop session (Wayland/X11), architecture, GPU and driver, Python |
| Package managers | Linux: pacman, yay, paru, nix, flatpak; Windows: winget, scoop, choco |
| Tools | required: git, chezmoi, uv; recommended: gh, pre-commit, ssh-keygen, just, plus sudo and zsh (Linux) or pwsh (Windows). age is optional (chezmoi bundles age) |
| Network | whether git's `http.proxy` is reachable, proxy environment variables |
| chezmoi | whether the source dir is this repo, whether the config file matches the template output, encryption mode, whether the private key exists, matches the repo's public key, its permissions, pending deployments |
| Repo | branch, distance from upstream, uncommitted changes, whether hooks are installed, push URL |
| Packages | how many manifest packages are missing; on Windows also how many programs need manual installs |

Each item is ok / warn / fail / info; fail means a required condition is missing and the exit code
is 1. `--json` prints structured results for scripts.

---

## 2. Post-reinstall recovery: restore

```
dotfiles restore [--apply] [--only steps,…] [--skip steps,…] [--key-file path] [-y/--yes]
```

### Steps

| Step | Platform | Detection | Action |
|---|---|---|---|
| `key` | both | the template's chosen key path has a file, and its derived public key matches the template's recipient | reads from `--key-file` or interactive input (a file path, or paste the key line, hidden), writes it and validates again; deletes the file on mismatch |
| `config` | both | the chezmoi source dir is this repo; `~/.config/chezmoi/chezmoi.toml` matches the template render | backs up the old config, then runs `chezmoi init` |
| `dotfiles` | both | whether `chezmoi status` (excluding externals) has pending items | full rehearsal -> backup of files about to be overwritten -> `chezmoi apply --force` |
| `system` | Linux | whether `system/etc` exists | restore rules from [3.6](#36-linux-etc) (asks for confirmation) |
| `packages` | both | manifest packages that are missing | Linux: `sudo pacman -S --needed` for packages in configured repos, the rest via paru/yay, listed when no AUR helper exists; Windows: `winget import`, `scoop import` (asks for confirmation) |
| `externals` | both | whether externals' target dirs exist | `chezmoi apply --include externals`; connects directly when the git proxy is unreachable |
| `ssh` | both | whether `~/.ssh/id_ed25519` and the private keys referenced by `~/.ssh/config` exist | generates missing keys with `ssh-keygen` (passphrase set interactively); can register the GitHub public key via `gh` when logged in |
| `shell` | Linux | whether the login shell is zsh | `chsh -s` (asks for confirmation) |
| `hooks` | both | whether the pre-commit / pre-push hooks are installed; whether the GitHub HTTPS remote's push URL is SSH | `uv tool install pre-commit` when needed, `pre-commit install`, `git remote set-url --push origin git@github.com:…` |
| `verify` | both | always | `chezmoi verify`, the permission audit, `check` |

### Execution semantics

- Without `--apply`, only detection runs and the plan is printed. Later steps depend on earlier
  results (e.g. the dotfiles step shows "blocked" without a key) — that is normal.
- With `--apply`, steps run in order: those detected as "done" are skipped; the flow stops on
  "blocked" or a failed step; after running, the step is detected again to confirm completion.
- No state files: every judgment is based on the current system state, so reruns are always safe
  and can resume from where a run stopped.
- Steps that need confirmation are skipped on non-interactive terminals unless `--yes` is given.

### Private key location

`home/.chezmoi.toml.tmpl` picks the key path in this order, using the last one that exists:

1. `~/.config/chezmoi/key.txt` (default; `restore` writes here on a new machine)
2. `~/secret/chezmoi_age_key.txt` (old Windows location)
3. `~/vg101/secret/chezmoi_age_key.txt` (old Linux location)

Existing machines therefore never need to move their key. When written: Linux permissions 600
(parent dir 700 if created); Windows drops inheritance and keeps only the user, SYSTEM and
Administrators.

### Network and proxy

- bootstrap clones the public repo over HTTPS, so no SSH key is needed yet; externals also use
  HTTPS. The push URL is switched to SSH in the `hooks` step.
- The deployed `~/.gitconfig` sets `http.proxy = 127.0.0.1:7897`. On a new machine where the proxy
  app is not installed yet, the `externals` step detects the unreachable proxy and temporarily
  overrides it to empty (direct connection) via `GIT_CONFIG_*` env vars, without touching any file.
- If even a direct connection cannot reach GitHub, set `HTTPS_PROXY` in the terminal before running
  bootstrap; uv follows it when downloading Python too.

---

## 3. Backup: status / backup / check / audit

### 3.1 backup flow

1. **Preflight**: age encryption enabled, recipient configured, private key present and not inside
   the repo; on Windows, checks whether the Documents folder is redirected (e.g. to OneDrive).
2. **re-add managed files**: plaintext managed files that grew secret patterns are tightened to 600
   and switched to encrypted, then `chezmoi re-add` runs for all managed files.
3. **Drift of template targets**: `re-add` does not handle templates (e.g. `.gitconfig.tmpl`).
   Template targets with local changes are listed and must be merged back into the `.tmpl` by hand.
4. **Add candidates**: unmanaged files from the current platform's candidate list are added to
   chezmoi and registered under the current platform in `platforms.toml`.
5. **/etc** (Linux, skipped with `--no-system`): see [3.6](#36-linux-etc).
6. **Package manifests**: Linux writes `packages/archpkg.txt` (`pacman -Qqe`); Windows writes
   `scoopfile.json`, `winget.json` (normalized so they only change when the package set changes)
   and `winget-unavailable.txt`.
7. **Security check**: same as `check`; returns 1 and does not commit on failure.
8. **Commit**: only with `--commit`; never pushes.

### 3.2 Candidate rules

For each file in the candidate list, in order:

| Condition | Action |
|---|---|
| already managed | skipped (maintained by re-add) |
| target owned by the other platform | skipped with a hint: make it a template and register it in `shared` if both platforms need it |
| content contains the current home dir as an absolute path | skipped with a hint to rewrite as a template (`{{ .chezmoi.homeDir }}`) |
| in the always-encrypt list, or matches a secret pattern | tightened to 600 on Linux, then `chezmoi add --encrypt` |
| `chezmoi add --secrets error` blocked by chezmoi's built-in scan | same, switched to encrypted |
| otherwise | plain `chezmoi add` |

The candidate lists live in `src/dotfiles/backup.py` as `LINUX` / `WINDOWS`: `track` (recommended
for tracking; directories expand to regular files under 1 MiB), `secret` (always encrypted),
`exclude` (filename patterns skipped when expanding directories).

### 3.3 Platform ownership

`home/.chezmoidata/platforms.toml` declares an owner for every managed target: `shared`, `linux` or
`windows`. Entries are paths relative to `~`; a directory entry covers everything below it.

- `.chezmoiignore` is a template: it ignores the other platform's entries, and their parent
  directories that belong only to that platform. Linux, for example, ignores `Documents`
  (otherwise Windows' `readonly_Documents/` would chmod `~/Documents` read-only) but not
  `.config` (both platforms have targets in it).
- Running `chezmoi add` on one platform for a target owned by the other only warns "ignoring"; it
  never rewrites the other platform's source files.
- `check` requires every target managed in each platform's view to be covered by exactly one list.

Manually adding a Linux-only file:

```bash
chezmoi add ~/.config/foo/bar.conf                 # or add --encrypt
# then add ".config/foo" (or the concrete file) to platforms.toml's linux list
uv run dotfiles check
```

Same-name targets needed on both platforms with different content: make it a template
(e.g. distinguish inside `dot_gitconfig.tmpl` with `{{ if eq .chezmoi.os "windows" }}`) and
register it in `shared`.

### 3.4 Encryption and templates

- Encrypted source file names look like `encrypted_private_dot_zshrc.age`. `private_` makes the
  deployed permissions 600; backup handles this automatically.
- Encrypted files can be templates, but `re-add` does not handle templates. To share one ciphertext
  across platforms, reference it from a template with `{{ include "…" | decrypt }}` (currently
  unused).

### 3.5 check

1. the chezmoi config's private key is not tracked by git;
2. `guard tree`: runs the same checks as the commit checks over all tracked and unignored new
   files, see [5](#5-commit-checks-pre-commit);
3. platform ownership is complete.

### 3.6 Linux /etc

`/etc` uses a separate chezmoi instance: the source dir is `system/` at the repo top, the
destination is a staging area under `$XDG_RUNTIME_DIR` (tmpfs, deleted afterwards). Files only root
can read are read in via `sudo cat`, so run it in an interactive terminal; without a terminal those
files are skipped with a warning.

Collected targets (`src/dotfiles/system_etc.py`):

- package config files marked `[modified]` by pacman;
- paths in `ETC_EXTRA` (directories contribute only files not owned by any package, e.g. custom
  systemd units, pacman hooks);
- paths in `ETC_SECRET`, always encrypted; files not world-readable and files matching secret
  patterns are encrypted too;
- everything in `ETC_EXCLUDE` is excluded: account databases, `machine-id`, SSH host keys,
  `*.pacnew`, `*.bak`, etc.

Restore (`system-restore`, and restore's `system` step):

- decrypts into the staging area and compares each file with `/etc`, showing diffs (encrypted
  files only report that they differ, without content).
- Files in `HARDWARE_BOUND` (`fstab`, `crypttab`, `default/grub`, `mkinitcpio.conf`,
  `mkinitcpio.d/*`, `X11/xorg.conf.d/*`, `modprobe.d/*`) show diffs only and must be named
  explicitly on the command line to install: a new system's disk UUIDs, encryption layout and
  hardware usually differ.
- `sudoers` and `sudoers.d/*` are validated with `visudo -cf` before installing; on failure they
  are not installed.
- Installs keep the target's current permissions; a nonexistent target uses the backup's
  permissions with owner `root:root`. After changing boot or initramfs files, run
  `grub-mkconfig -o /boot/grub/grub.cfg` or `mkinitcpio -P`.

### 3.7 audit

Checks the always-encrypt list's files, `.pypirc`, `.netrc`, `.git-credentials`, `~/.ssh` (and the
private keys inside), `~/.gnupg`, key files and their directories. Linux requires files 600 and
dirs 700; Windows requires the ACL to contain only the user, SYSTEM and Administrators. Tightened
with `--apply`.

---

## 4. Cache cleanup: clean

```
dotfiles clean scan [--deep]
dotfiles clean list
dotfiles clean run [--apply] [--deep] [-x target]… [target|default|opt|all]…
dotfiles clean leftovers
```

- `scan`: size and state per target (cleanable / skips / no data / n/a / unmeasured), large
  directories in the cache roots covered by no target, the leftover report, and report-only items
  (orphan packages, coredumps, WSL virtual disks, ...).
- `run`: no targets means `default`; the `opt` tier must be named or selected via `opt` / `all`.
  `-x` excludes targets, repeatable or comma-separated.
- `leftovers`: leftover directories of uninstalled apps and long-unused Electron config dirs.
  Report only, never deleted.

### 4.1 Safety measures

- Every deletion passes a scope assertion: the path must be absolute, lie under an allowed root
  (Linux: the home dir and explicitly set tool dirs; Windows: the home dir, the scoop dir, the
  Windows Update download dir), and must not be a protected directory itself (home, the XDG/AppData
  roots, Documents, Desktop, Downloads, `.ssh`, this repo, ...). A failed assertion aborts the run.
- Deletions never follow symlinks or Windows reparse points; only the link itself is deleted.
- Caches of running apps are skipped: Chromium/Electron's `SingletonLock` (Linux, PID inside is
  checked) or `lockfile` (Windows, exclusive-open check), VS Code's `code.lock`, Firefox's `lock` /
  `parent.lock`; without a lock file the process name is matched.
- Tool-native cleanup commands are preferred (`uv cache prune`, `npm cache clean`,
  `pnpm store prune`, ...); directories are deleted only when no command exists.
- Offline data is never touched: Electron/browser Service Worker, IndexedDB, Local Storage;
  installed tools (uv tools, rustup, VS Code extensions); KDE's ksycoca and `*.kcache`.

### 4.2 Targets shared by both platforms

| Target | Tier | Action | `--deep` |
|---|---|---|---|
| `uv` | default | `uv cache prune` | `uv cache clean` |
| `pip` | default | `pip cache purge` | |
| `pnpm` | default | `pnpm store prune` + metadata cache | |
| `npm` | default | `npm cache clean --force` + `_logs` | also removes `_npx` |
| `bun` | default | `bun pm cache rm` | |
| `yarn` | default | deletes the global caches of classic and berry | |
| `go` | default | `go clean -cache`; deletes the cache dir when Go is uninstalled | also `go clean -modcache` |
| `cargo` | default | `$CARGO_HOME/registry/src`, `git/checkouts` (skipped while cargo runs) | also `registry/cache`, `git/db` |
| `nuget` | default | `dotnet nuget locals http-cache --clear` | |
| `vcpkg` | default | `$VCPKG_ROOT/buildtrees`, `packages` | also `downloads` |
| `precommit` | default | `pre-commit gc` + stash patches older than 1 hour | `pre-commit clean` |
| `gradle` | opt | `gradle --stop`, then deletes `caches`, `daemon` | |
| `maven` | opt | `~/.m2/repository` (skipped while Maven runs) | |
| `docker` | opt | `docker builder prune -f` + `docker image prune -f` (scan's size is the reclaimable total) | |

### 4.3 Linux targets

| Target | Tier | Action |
|---|---|---|
| `yay` | default | `~/.cache/yay`, `~/.cache/paru/clone`: `git clean -ffdx` for installed packages' clones (keeps the PKGBUILD), deletes clones of uninstalled packages (skipped while yay/paru/makepkg runs) |
| `firefox` | default | per-profile `cache2`, `startupCache`, `thumbnails` |
| `electron` | default | Cache, Code Cache, GPUCache etc. under `~/.config/<App>`; VS Code-family also CachedData, CachedExtensionVSIXs, logs |
| `lsp` | default | `~/.cache/typescript`, `~/.cache/jedi` (skipped while a language server runs) |
| `wine` | default | wine-mono/gecko installers and winetricks caches |
| `kde` | default | DrKonqi crash reports, KRunner bookmark copies, AppStream/Discover caches (Discover's kept while it runs) |
| `thumbnails` | default | thumbnails not accessed for `THUMBNAIL_DAYS` days (`--deep`: all) |
| `zcompdump` | default | `~/.zcompdump-*` left over from older zsh versions |
| `shader` | opt | Mesa/RADV/Qt shader caches |
| `trash` | opt | empty the trash |
| `pacman` | opt | `sudo paccache -rk$PACCACHE_KEEP` and `-ruk0` |
| `journal` | opt | `sudo journalctl --vacuum-size=$JOURNAL_MAX` |

### 4.4 Windows targets

| Target | Tier | Action |
|---|---|---|
| `temp` | default | top-level `%TEMP%` entries older than `TEMP_DAYS` days (a dir with newer files inside is kept; locked files are skipped) |
| `scoop` | default | `scoop cleanup -a -k` (`--deep` also `scoop cache rm -a`) |
| `electron` | default | Chromium caches under `%APPDATA%\<App>`; VS Code-family (Code, Cursor, Windsurf, Kiro, Trae) also CachedData etc. |
| `browser` | default | profile and shader caches of Edge / Chrome / Brave |
| `firefox` | default | caches under `%LOCALAPPDATA%\Mozilla\Firefox\Profiles\*` |
| `updaters` | default | electron-updater installers in `%LOCALAPPDATA%\*-updater` |
| `crashdumps` | opt | `CrashDumps` and WER reports |
| `cloudmusic` | opt | NetEase Cloud Music playback cache |
| `shader` | opt | DirectX / NVIDIA / AMD / Intel shader caches |
| `nugetpkgs` | opt | `dotnet nuget locals global-packages --clear` |
| `recycle` | opt | `Clear-RecycleBin -Force` |
| `wu` | opt | stops wuauserv and bits, then empties `SoftwareDistribution\Download` (needs admin) |
| `dism` | opt | `DISM /Online /Cleanup-Image /StartComponentCleanup` (needs admin) |

### 4.5 Environment variables

| Variable | Default | Effect |
|---|---|---|
| `THUMBNAIL_DAYS` | 90 | day threshold for Linux `thumbnails` |
| `TEMP_DAYS` | 7 | day threshold for Windows `temp` |
| `STALE_DAYS` | 180 | days without writes for `leftovers` |
| `JOURNAL_MAX` | 1G | journal size kept after `journal` |
| `PACCACHE_KEEP` | 1 | versions kept per package by `pacman` |
| `UNCOVERED_MIN_MB` | 10 (Linux) / 100 (Windows) | minimum size for `scan` to list uncovered items |

Tool-specific location variables are honored too: `XDG_*`, `CARGO_HOME`, `VCPKG_ROOT`,
`GRADLE_USER_HOME`, `PRE_COMMIT_HOME`, `BUN_INSTALL`, `NUGET_HTTP_CACHE_PATH`, `NUGET_PACKAGES`,
`SCOOP`, etc.

### 4.6 Periodic runs (Linux, optional)

`~/.config/systemd/user/cache-clean.service`:

```ini
[Unit]
Description=Weekly cache cleanup

[Service]
Type=oneshot
ExecStart=uv run --project %h/.local/share/chezmoi --frozen --no-dev dotfiles clean run --apply
```

`~/.config/systemd/user/cache-clean.timer`:

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
systemctl --user daemon-reload && systemctl --user enable --now cache-clean.timer
```

The default tier needs no sudo; running apps are skipped automatically. If uv is not on the systemd
user session's PATH, replace `uv` with its absolute path.

---

## 5. Commit checks (pre-commit)

The repo uses [pre-commit](https://pre-commit.com) to run checks automatically at `git commit` and
`git push`, keeping plaintext secrets out. It needs only Python and git; both platforms share one
config. restore's `hooks` step installs it; manual install:

```bash
uv tool install pre-commit        # or pacman -S pre-commit
pre-commit install                # installs both pre-commit and pre-push
pre-commit install-hooks          # optional: pre-build the envs (first run downloads Go and builds gitleaks)
```

`.git/hooks/*` contain this machine's Python path; they are generated per machine — do not copy or
commit them. Building gitleaks the first time needs the Go module proxy; set `HTTPS_PROXY` briefly
if a direct connection fails.

### 5.1 Files involved

| File | Purpose |
|---|---|
| `.pre-commit-config.yaml` | hook list, remote hooks pinned to exact versions |
| `.gitleaks.toml` | gitleaks built-in rules plus a WakaTime rule; skips `encrypted_*.age` ciphertext |
| `.gitattributes` | LF line endings everywhere (immune to Windows `core.autocrlf`); `*.age`, `*.ptb` untouched; `*.bat`, `*.cmd` use CRLF |
| `src/dotfiles/guard.py` | repo-specific rules; both a package module and executed directly by pre-commit, so standard library only |

### 5.2 What is checked

At `git commit` (the staged set only):

| Hook | Checks |
|---|---|
| `check-illegal-windows-names`, `check-case-conflict` | paths Windows cannot have |
| `check-added-large-files` | new files larger than 1 MiB |
| `check-merge-conflict`, `check-yaml`, `check-toml` | conflict markers and syntax errors (`.tmpl` skipped) |
| `gitleaks` | 200+ generic secret rules; values print as `REDACTED` |
| `prune-precommit-stash` | deletes stash patches older than 1 hour and tightens the cache dir to 700 (never blocks) |
| `dotfiles-guard` | see the table below |

At `git push`, `dotfiles-guard-push` checks the files added or modified by **every** commit about
to be pushed, so commits made with `--no-verify` are caught too — even when a later commit removes
the secret.

The `dotfiles-guard` rules (identical in `staged`, `push` and `tree` modes). Deployment targets
follow chezmoi rules: files under the dir named by `.chezmoiroot` (e.g. `home/`) map to `~` targets,
`system/` maps to system paths, and the rest (tool code, package manifests) are not deployment
targets but are secret-scanned all the same.

| Rule | Level | Meaning |
|---|---|---|
| `age-not-ciphertext` / `age-armor-truncated` | error | a `*.age` file is not a complete age ciphertext |
| `age-without-encrypted-prefix` / `encrypted-prefix-without-age` | error | the `encrypted_` prefix and the `.age` suffix do not come in pairs |
| `must-encrypt(<target>)` | error | the target belongs to a must-encrypt category but is not encrypted (`ENCRYPT_REQUIRED` / `ENCRYPT_EXEMPT`) |
| secret rule names (e.g. `github-token`) | error | plaintext matching `SECRET_RULES`; `backup` uses the same rules to decide on encryption |
| `absolute-home-path` | warning | contains an absolute home path, invalid on another machine |

`ENCRYPT_REQUIRED` covers: `.ssh/*` (except `*.pub`, `known_hosts*`), `.gnupg/*`, `.pypirc`,
`.npmrc`, `.netrc`/`_netrc`, `.git-credentials`, `.docker/config.json`, gh's `hosts.yml`,
`.wakatime.cfg`, `.aws/credentials`, `.kube/config`, rclone configs, `*.pem`/`*.key`/`*.p12`/
`*.pfx`, `id_rsa`/`id_ecdsa`/`id_ed25519`, `.env*` (except `.env.example`), plus `/etc/shadow*`,
`/etc/sudoers*`, `/etc/crypttab`, NetworkManager connections, WireGuard, SSH host keys and the
mihomo config under `system/`.

### 5.3 When blocked

Output contains only `file:line` and the rule name.

| Situation | What to do |
|---|---|
| a sensitive file was added in plaintext | `git restore --staged <source>`, `chezmoi forget <target>`, then `chezmoi add --encrypt <target>` |
| a token slipped into a plaintext config | move the token to an encrypted file or an env var, or track the whole file encrypted |
| false positive | add a trailing `gitleaks:allow` comment (both tools honor it), or adjust `guard.py` / `.gitleaks.toml` |
| committed with `--no-verify`, not yet pushed | pre-push will catch it; `git reset --soft <last clean commit>` and redo |
| already pushed | **rotate that credential immediately**; rewriting git history cannot un-publish it |

Temporarily skip one hook: Linux `SKIP=gitleaks git commit …`; Windows
`$env:SKIP = "gitleaks"; git commit …; Remove-Item Env:SKIP`.

### 5.4 Stash patches

Before running hooks, pre-commit saves **unstaged** changes as `$PRE_COMMIT_HOME/patch*` and never
deletes them; if the worktree held unencrypted sensitive content at that moment, it stays in the
patch as plaintext. `prune-precommit-stash` deletes patches older than 1 hour at every commit (the
newest patch is left for the commit in progress to restore the worktree), and
`clean run precommit` deletes them too. To purge right now (confirm no commit is in progress):

```bash
uv run dotfiles guard prune-stash --max-age-minutes 0
```

---

## 6. Development and tests

```bash
uv run pytest                     # or just test
uvx ruff check src tests
```

Tests never touch the real system: the Windows cleanup targets' filesystem logic runs on any
platform by pointing `%LOCALAPPDATA%` etc. at temp dirs; platform ownership tests render the other
platform's view with `chezmoi --override-data '{"chezmoi":{"os":"windows"}}'`.

See what would deploy in the Windows view from Linux:

```bash
chezmoi --source ~/.local/share/chezmoi --override-data '{"chezmoi":{"os":"windows"}}' managed
```

## 7. Known limitations

- **The Python version has never actually run on Windows**: the Windows cleanup targets, backup
  (scoop/winget export, ACL audit), the recovery flow and `bootstrap.ps1` were ported from the
  original PowerShell scripts (or written fresh) and are only tested at the filesystem level on
  Linux, plus syntax parsing. The original scripts are in commit `a15c9b5`
  (`git show a15c9b5:scripts/clean.ps1`). On first Windows use, run `doctor`, `clean scan`,
  `status` and `restore` (without `--apply`) to confirm the results.
- **scoop is not installed automatically**: its official install method is a remote script, which
  this tool will not run for you; restore stops with instructions when scoop is missing.
- **Third-party repos like archlinuxcn**: on a new system, before `/etc/pacman.conf` is restored and
  the matching keyring installed, their packages are listed as "AUR or unknown". Handle those as
  restore instructs, then rerun `--only packages`.
- **Same-name targets with different content on the two platforms** (e.g. `.wakatime.cfg`,
  `.docker/config.json`) are currently managed by Windows only; when Linux needs one, turn it into
  a per-platform template and register it in `shared`.
- **`[https] proxy` in `.gitconfig`** is not a git-recognized setting (git only knows `http.proxy`,
  which covers HTTPS anyway) and can be deleted.

# Reference

Behavior, rules and tunables of the `dotfiles` commands. Quick start in the top-level
[README.md](../README.md). Chinese version: [reference_zh-CN.md](reference_zh-CN.md). Code layout: [architecture.md](architecture.md).

Run everything through uv: `uv run dotfiles <command>` (inside the repo), or `uv run --project ~/.local/share/chezmoi --frozen --no-dev dotfiles <command>` (anywhere). The tool depends only on the Python standard library; with `--no-dev` it never touches PyPI.

Conventions:

- Modifying commands preview by default: `restore`, `clean run`, `audit`, `system-restore` need `--apply`; `backup` writes to the repo directly (`-n` previews) but never pushes.

- Exit codes: `0` success, `1` failure or failed check, `2` usage error (unknown target, step, etc.).

- Output is colored on a terminal; plain with `NO_COLOR` set or when redirected.

---

## 1. Environment detection: doctor

Read-only. Checks, in order:

| Section | Contents |
|---|---|
| System | distribution and kernel / Windows version, desktop session (Wayland/X11), architecture, GPU and driver, Python |
| Package managers | Linux: pacman, apt, yay, paru, nix, flatpak; Windows: winget, scoop, choco |
| Tools | required: git, chezmoi, uv; recommended: gh, pre-commit, ssh-keygen, just, plus sudo and zsh (Linux) or pwsh (Windows). age is optional (chezmoi bundles age) |
| Network | whether git's `http.proxy` is reachable, proxy environment variables |
| chezmoi | whether the source dir is this repo, whether the config file matches the template output, encryption mode, whether the private key exists, matches the repo's public key, its permissions, pending deployments |
| Repo | branch, distance from upstream, uncommitted changes, whether hooks are installed, push URL |
| Packages | how many manifest packages are missing; on Windows also how many programs need manual installs |

Each item is ok / warn / fail / info; fail means a required condition is missing and the exit code is 1. `--json` prints structured results for scripts.

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
| `dotfiles` | both | whether `chezmoi status` (excluding externals) has pending items | full rehearsal -> backup of files about to be overwritten or removed -> `chezmoi apply --force` |
| `system` | Linux | whether `system/<distro>/etc` exists for this distro (os-release `ID`) | restore rules from [3.6](#36-linux-etc) (asks for confirmation); skipped on a distro without a backup |
| `packages` | both | packages missing from this package manager's manifest (`archpkg.txt` for pacman, `aptpkg.txt` for apt) | Arch-like: `sudo pacman -S --needed` for packages in configured repos, the rest via paru/yay, listed when no AUR helper exists; Debian-like: `sudo apt-get install` for packages in the configured apt sources, the rest listed (a third-party source not restored yet, or a local .deb); Windows: `winget import`, `scoop import` (asks for confirmation) |
| `externals` | both | whether externals' target dirs exist | `chezmoi apply --include externals`; connects directly when the git proxy is unreachable |
| `ssh` | both | whether `~/.ssh/id_ed25519` and the private keys referenced by `~/.ssh/config` exist | generates missing keys with `ssh-keygen` (passphrase set interactively); can register the GitHub public key via `gh` when logged in |
| `shell` | Linux | whether the login shell is zsh | `chsh -s` (asks for confirmation) |
| `hooks` | both | whether the pre-commit / pre-push hooks are installed; whether the push URL of this repo, and of git externals owned by the same GitHub account, is SSH (third-party externals such as oh-my-zsh keep theirs) | `uv tool install pre-commit` when needed, `pre-commit install`, `git remote set-url --push origin git@github.com:…` |
| `verify` | both | always | `chezmoi verify`, the permission audit, `check` |

### Execution semantics

- Without `--apply`, only detection runs and the plan is printed. Later steps depend on earlier results (e.g. the dotfiles step shows "blocked" without a key) — that is normal.

- With `--apply`, steps run in order: those detected as "done" are skipped; the flow stops on "blocked" or a failed step; after running, the step is detected again to confirm completion.

- No state files: every judgment is based on the current system state, so reruns are always safe and can resume from where a run stopped.

- Steps that need confirmation are skipped on non-interactive terminals unless `--yes` is given.

### Private key location

`home/.chezmoi.toml.tmpl` picks the key path in this order, using the last one that exists:

1. `~/.config/chezmoi/key.txt` (default; `restore` writes here on a new machine)

2. `~/secret/chezmoi_age_key.txt` (old Windows location)

3. `~/vg101/secret/chezmoi_age_key.txt` (old Linux location)

Existing machines therefore never need to move their key. When written: Linux permissions 600(parent dir 700 if created); Windows drops inheritance and keeps only the user, SYSTEM and Administrators.

### Network and proxy

- bootstrap clones the public repo over HTTPS, so no SSH key is needed yet; externals also use HTTPS. The `hooks` step switches the push URL to SSH for this repo and for externals of the same GitHub owner; repos you never push to are left alone.

- Without sudo, `bootstrap.sh` stops with the user-level route: the release binaries of chezmoi and uv in `~/.local/bin` (it puts that directory on `PATH` before looking for tools). `doctor` suggests the same route for recommended tools such as gh and just.

- The deployed `~/.gitconfig` sets `http.proxy = 127.0.0.1:7897`. On a new machine where the proxy app is not installed yet, the `externals` step detects the unreachable proxy and temporarily overrides it to empty (direct connection) via `GIT_CONFIG_*` env vars, without touching any file.

- If even a direct connection cannot reach GitHub, set `HTTPS_PROXY` in the terminal before running bootstrap; uv follows it when downloading Python too.

---

## 3. Backup: status / backup / check / audit

### 3.1 backup flow

1. **Preflight**: age encryption enabled, recipient configured, private key present and not inside the repo; on Windows, checks whether the Documents folder is redirected (e.g. to OneDrive).

2. **re-add managed files**: plaintext managed files that grew secret patterns are tightened to 600 and switched to encrypted (except `create_` / `modify_` sources, whose live content chezmoi deliberately does not track), then `chezmoi re-add` runs for all managed files. Managed files whose source carries `private_` but whose live mode is looser (e.g. a tool rewrote them as 644) are tightened to 600 first — `re-add` copies the live mode back into the source and would otherwise drop `private_`. With `-n`, chezmoi's output is captured (it never opens a pager) and `.age` diffs are shown as one line each, since ciphertext is unreadable.

3. **Drift of template targets**: `re-add` does not handle templates (e.g. `.gitconfig.tmpl`). Template targets with local changes are listed and must be merged back into the `.tmpl` by hand ([6.3](#63-cases-that-need-a-manual-merge)).

4. **Add candidates**: unmanaged files from the current platform's candidate list are added to chezmoi and registered under the current platform in `platforms.toml`.

5. **System config** (skipped with `--no-system`): `/etc` on Linux, see [3.6](#36-linux-etc); registry keys on Windows, see [3.9](#39-windows-registry-keys).

6. **Package manifests**: Arch-like Linux writes `packages/archpkg.txt` (`pacman -Qqe`), Debian-like Linux `packages/aptpkg.txt` (`apt-mark showmanual`, sorted); Windows writes `scoopfile.json`, `winget.json` (normalized so they only change when the package set changes) and `winget-unavailable.txt`.

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

The candidate lists live in `dotfiles.toml` under `[backup.linux]` / `[backup.windows]`: `track`
(recommended for tracking; directories expand to regular files under 1 MiB), `secret` (always
encrypted), `exclude` (filename patterns skipped when expanding directories) and `ignore`
(candidates never added nor reported: an exact path, a directory, or a glob relative to `~`). See
[3.8](#38-dotfilestoml-and-dotfiles-config).

### 3.3 Platform ownership

`home/.chezmoidata/platforms.toml` declares an owner for every managed target: `shared`, `linux` or `windows`. Entries are paths relative to `~`; a directory entry covers everything below it.

- `.chezmoiignore` is a template: it ignores the other platform's entries, and their parent directories that belong only to that platform. Linux, for example, ignores `Documents` (otherwise Windows' `readonly_Documents/` would chmod `~/Documents` read-only) but not `.config` (both platforms have targets in it).

- Running `chezmoi add` on one platform for a target owned by the other only warns "ignoring"; it never rewrites the other platform's source files.

- `check` requires every target managed in each platform's view to be covered by exactly one list.

- **Unowned targets are registered automatically.** A target added with plain `chezmoi add` has no owner, and `.chezmoiignore` would hide it from no platform, so it would deploy everywhere. `dotfiles backup` registers every unowned managed target under the current platform (`-n` lists them), and the commit hooks reject a commit that adds a `~` target without an owner (`no-platform-owner`, see [5.2](#52-what-is-checked)). Commit the file and the updated `platforms.toml` together.

Manually adding a Linux-only file:

```bash
chezmoi add ~/.config/foo/bar.conf                 # or add --encrypt
uv run dotfiles backup -n                          # lists it under "Register platform owners"
uv run dotfiles backup --commit                    # registers it under linux and commits
```

#### Environment tags: `[requires]`

Within one OS, machines still differ: a KDE config has no business on a GNOME machine. The `[requires]` table maps a **tag** to targets that deploy only on machines with that tag:

```toml
[requires]
kde = [".config/kwinrc", ".config/kdeglobals"]
konsole = [".config/konsolerc", ".local/share/konsole"]
```

- **A machine's tags** are detected at `chezmoi init` by `home/.chezmoi.toml.tmpl` and written to the config as `[data] tags`: the os-release `ID` and `ID_LIKE` words (e.g. `arch`; `kali`, `debian`), `kde` when `plasmashell` is on `PATH`, `gnome` when `gnome-shell` is, `xfce` when `xfce4-session` is, `konsole` when `konsole` is, and a **program tag** when the binary a target runs exists (`zeroclaw`: `~/.cargo/bin/zeroclaw`, `taskdog`: `~/.local/bin/taskdog-server`, the `ExecStart` of their user units). Tags of your own go in `~/.config/chezmoi/tags`, one per line (lowercase letters, digits and hyphens; blank lines and `#` comments are ignored); that file stays on the machine, outside the repo. `dotfiles doctor` shows all of them, and warns about lines of the tags file that are not valid tags and so are ignored. Rerun `chezmoi init` (or `dotfiles restore --apply --only config`) after installing or removing a desktop environment or one of those programs, or after editing the tags file.
- Targets not listed in `[requires]` deploy on every machine of their OS, so most targets need no tag. A target with a tag the machine lacks is ignored like another platform's target (including parent directories used only by such targets).
- A config written before tags existed applies no `[requires]` filtering; `doctor` reports it until the config is regenerated.
- `check` validates tag names (`[a-z0-9-]`) and that each entry overlaps an entry in `[platforms]`.
- Preview another machine's view: `chezmoi --override-data '{"chezmoi":{"os":"linux"},"tags":["kali","debian","gnome"]}' managed`.

Same-name targets needed on both platforms with different content: make it a template(e.g. distinguish inside `dot_gitconfig.tmpl` with `{{ if eq .chezmoi.os "windows" }}`) and register it in `shared`.

#### Retiring a target: `.chezmoiremove`

Deleting a source file only stops chezmoi from managing the target; the deployed copy stays on every machine. To retire a target everywhere:

1. `git rm` its source file under `home/`, and remove its entry from `platforms.toml` (and from `[requires]`, if listed).
2. Drop it from `backup.<os>.track` in `dotfiles.toml`, or the next backup adds it back; to stop backing up a file that still exists, add it to `backup.<os>.ignore` instead.
3. List the target in `home/.chezmoiremove`. `dotfiles apply` shows it as `D`, backs it up to `~/.local/state/dotfiles/pre-apply-*` with the other files, and deletes it.

`.chezmoiignore` takes precedence: a target that is ignored on a machine (another platform's, or one whose `[requires]` tag is missing) is never removed there. A target that is both managed and listed in `.chezmoiremove` is an error (`inconsistent state`).

### 3.4 Encryption and templates

- Encrypted source file names look like `encrypted_private_dot_npmrc.age`. `private_` makes the deployed permissions 600; backup handles this automatically. An encrypted template is `encrypted_….tmpl.age` (`.age` outermost).

- Encrypted files can be templates, but `re-add` does not handle templates. To share one ciphertext across platforms, reference it from a template with `{{ include "…" | decrypt }}` (currently unused).

### 3.5 check

1. the chezmoi config's private key is not tracked by git;

2. `guard tree`: runs the same checks as the commit checks over all tracked and unignored new files, see [5](#5-commit-checks-pre-commit);

3. platform ownership is complete.

### 3.6 Linux /etc

`/etc` uses a separate chezmoi instance: the source dir is `system/<distro>/` at the repo top (`<distro>` is the os-release `ID`, e.g. `system/arch/`; each distro backs up and restores only its own directory, so another distro's `/etc` is never installed or overwritten), the destination is a staging area under `$XDG_RUNTIME_DIR` (tmpfs, deleted afterwards). Files only root can read are read in via `sudo cat`, so run it in an interactive terminal; without a terminal those files are skipped with a warning.

Collected targets (lists in `dotfiles.toml` `[etc]`):

- package config files the user changed: marked `[modified]` by pacman; on dpkg systems, conffiles whose checksum differs from the one in `/var/lib/dpkg/status` (what `debsums -ce` reports; a conffile another package diverted, as Kali's `kali-defaults` / `kali-themes` do, is checked at its diverted-to path);

- paths in `extra` (directories contribute only files not owned by any package, e.g. custom systemd units, pacman hooks, or your own files in drop-in dirs such as `/etc/profile.d` and `/etc/ssh/sshd_config.d`). Put local settings in a drop-in file rather than editing a package's config: the package file stays pristine, and upgrades then produce no `.pacnew` to merge;

- paths in `secret`, always encrypted; files not world-readable and files matching secret patterns are encrypted too. A file that already matches its backup (the decrypted content, plus the attributes `chezmoi add` would record: encryption, `private_`, `readonly_`, `executable_`) is left as it is. Exact modes are not compared: attributes cannot express 0440 or 0640, and chezmoi derives the rest from the umask (0664 under umask 002). Otherwise age output differs on every encryption, so re-adding it would show every encrypted file as modified after each backup;

- a listed file (`extra` or `secret`) that is **exactly what its package installed** is not collected: the package restores it. Only a verified match counts. pacman: a backup file whose md5 equals the one in `/var/lib/pacman/local/*/files`. dpkg: a conffile (`/var/lib/dpkg/status`) or a file in `info/*.md5sums` with the same md5. A file with no such record, a missing file, and a root-only file that cannot be hashed without `sudo` all stay in the backup. `backup` removes entries of this kind that are already in `system/<distro>/`; any other entry that is no longer collected (excluded, deleted, unreadable without sudo) only gets a note and is never deleted. One exception is Kali's `kali-themes`, which ships its `/etc` files under `/usr/share/kali-themes/etc/` and copies them, so dpkg has no md5 for the `/etc` path and `sddm.conf.d/kde_settings.conf` is still backed up;

- everything in `exclude` is excluded: account databases, `machine-id`, SSH host keys, `*.pacnew`, `*.dpkg-dist` and the other dpkg / ucf leftovers, `*.bak`, etc. Entries that do not exist on a distro (sddm, mkinitcpio, pacman hooks on Kali; `/etc/apt` on Arch) contribute nothing, so one list serves every distro.

`dotfiles status` ends with the pending items: `.pacnew` / `.pacsave` files, or on dpkg systems the `.dpkg-*` / `.ucf-*` leftovers. Leftovers of stock files another package diverted (Kali's `kali-themes` / `kali-menu`) and of `ca-certificates.conf`, which `update-ca-certificates` rewrites, are never your edits to merge, so they are left out and only counted.

Restore (`system-restore`, and restore's `system` step):

- uses this distro's backup; `system-restore --distro NAME` restores another one explicitly (e.g. after reinstalling into a derivative). restore's `system` step is skipped on a distro without a backup.

- decrypts into the staging area and compares each file with `/etc`, showing diffs (encrypted files only report that they differ, without content).

- Files in `hardware_bound` (`fstab`, `crypttab`, `default/grub`, `mkinitcpio.conf`, `mkinitcpio.d/*`, `X11/xorg.conf.d/*`, `modprobe.d/*`) show diffs only and must be named explicitly on the command line to install: a new system's disk UUIDs, encryption layout and hardware usually differ.

- `sudoers` and `sudoers.d/*` are validated with `visudo -cf` before installing; on failure they are not installed.

- Installs keep the target's current permissions; a nonexistent target uses the backup's permissions with owner `root:root`. After changing boot or initramfs files, run `grub-mkconfig -o /boot/grub/grub.cfg` or `mkinitcpio -P`.

### 3.7 audit

Checks the files of `backup.<os>.secret`, `.pypirc`, `.netrc`, `.git-credentials`, `~/.ssh` (and the private keys inside), `~/.gnupg`, key files and their directories. Linux requires files 600 and dirs 700; Windows requires the ACL to contain only the user, SYSTEM and Administrators. Tightened with `--apply`.

### 3.8 dotfiles.toml and `dotfiles config`

The backup and cleanup lists and tunables are data in `dotfiles.toml` at the repo top (outside
`home/`, so never deployed). The schema — keys, types, descriptions, environment overrides — lives in
`src/dotfiles/utils/common/config.py`; `dotfiles check` rejects unknown keys (typos) and wrong types.

```
dotfiles config list [PREFIX]        every key with its value and description (e.g. list backup.linux)
dotfiles config get KEY              one value
dotfiles config add KEY VALUE...     append to a list      e.g. add backup.linux.ignore .config/spectaclerc
dotfiles config remove KEY VALUE...  remove from a list
dotfiles config set KEY VALUE        set a number or string, e.g. set clean.linux.thumbnail_days 30
dotfiles config path                 where the file is
```

Writes edit the file line by line: arrays stay one item per line, hand-written comments and ordering
are kept, and the result is re-parsed and validated before it replaces the file. Arrays of tables
(`[[clean.paths]]`, `[[clean.<os>.leftovers]]`) are edited by hand. The file is shared by both
platforms; commit it like any other data change ([workflows.md](workflows.md)).

### 3.9 Windows registry keys

The Windows counterpart of `/etc` (3.6): a second chezmoi instance whose source dir is `system/windows/`. It is **opt-in**: `dotfiles.toml` `[windows] registry` lists the keys (a hive and a subkey, e.g. `HKCU\Control Panel\Desktop`) and is empty by default, so nothing happens until you add one (`dotfiles config add windows.registry 'HKCU\Control Panel\Desktop'`).

- `backup` exports each key with `reg export` into `system/windows/registry/<key>.reg` (`HKCU_Control_Panel_Desktop.reg`), as UTF-8 text with LF line ends so git can diff it. A key holding something the secret scan flags is stored encrypted. A file whose content and encryption did not change is left alone, so nothing is re-encrypted. A key that cannot be exported is a warning; a file whose key was taken off the list gets a note and is never deleted.
- `system-restore` shows a diff between each stored key and the live one (an encrypted one only reports that it differs); `--apply` imports it with `reg import`. Import **merges**: values and subkeys added since the backup stay. Importing into `HKLM` / `HKCR` needs an elevated shell. Name keys (or file names) on the command line to restore only those.
- `status` lists the keys and whether each is backed up; `backup -n` previews.
- Run on a real Windows machine on 2026-11-04 (a temporary key with Unicode, binary and multi-string values went through backup, change, preview, import and verify); see [win.md](win.md) 3 for the keys in use. One limit: a single `HKLM` / `HKCR` key in the list makes `system-restore --apply` require an elevated shell for **all** keys, even when only `HKCU` keys are named.

---

## 4. Cache cleanup: clean

```
dotfiles clean scan [--deep]
dotfiles clean list
dotfiles clean run [--apply] [--deep] [-x target]… [target|default|opt|all]…
dotfiles clean leftovers
```

- `scan`: size and state per target (cleanable / skips / no data / n/a / unmeasured), large directories in the cache roots covered by no target, the leftover report, and report-only items (orphan packages, coredumps, WSL virtual disks, ...).

- `run`: no targets means `default`; the `opt` tier must be named or selected via `opt` / `all`. `-x` excludes targets, repeatable or comma-separated.

- `leftovers`: leftover directories of uninstalled apps and long-unused Electron config dirs. Report only, never deleted.

### 4.1 Safety measures

- Every deletion passes a scope assertion: the path must be absolute, lie under an allowed root (Linux: the home dir and explicitly set tool dirs; Windows: the home dir, the scoop dir, the Windows Update download dir), and must not be a protected directory itself (home, the XDG/AppData roots, Documents, Desktop, Downloads, `.ssh`, this repo, ...). A failed assertion aborts the run.

- Deletions never follow symlinks or Windows reparse points; only the link itself is deleted.

- Caches of running apps are skipped: Chromium/Electron's `SingletonLock` (Linux, PID inside is checked) or `lockfile` (Windows, exclusive-open check), VS Code's `code.lock`, Firefox's `lock` / `parent.lock`; without a lock file the process name is matched.

- Tool-native cleanup commands are preferred (`uv cache prune`, `npm cache clean`, `pnpm store prune`, ...); directories are deleted only when no command exists.

- Offline data is never touched: Electron/browser Service Worker, IndexedDB, Local Storage; installed tools (uv tools, rustup, VS Code extensions); KDE's ksycoca and `*.kcache`.

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
| `thumbnails` | default | thumbnails not accessed for `thumbnail_days` days (`--deep`: all) |
| `zcompdump` | default | `~/.zcompdump-*` left over from older zsh versions |
| `shader` | opt | Mesa/RADV/Qt shader caches |
| `trash` | opt | empty the trash |
| `pacman` | opt | `sudo paccache -rk<paccache_keep>` and `-ruk0` |
| `journal` | opt | `sudo journalctl --vacuum-size=<journal_max>` |

### 4.4 Windows targets

| Target | Tier | Action |
|---|---|---|
| `temp` | default | top-level `%TEMP%` entries older than `temp_days` days (a dir with newer files inside is kept; locked files are skipped) |
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

### 4.5 Tunables

Set in `dotfiles.toml` ([3.8](#38-dotfilestoml-and-dotfiles-config)); the environment variable, when
set, overrides the file for one run.

| Key | Default | Environment | Effect |
|---|---|---|---|
| `clean.linux.thumbnail_days` | 90 | `THUMBNAIL_DAYS` | day threshold for Linux `thumbnails` |
| `clean.windows.temp_days` | 7 | `TEMP_DAYS` | day threshold for Windows `temp` |
| `clean.<os>.stale_days` | 180 | `STALE_DAYS` | days without writes for `leftovers` |
| `clean.linux.journal_max` | 1G | `JOURNAL_MAX` | journal size kept after `journal` |
| `clean.linux.paccache_keep` | 1 | `PACCACHE_KEEP` | versions kept per package by `pacman` |
| `clean.<os>.uncovered_min_mb` | 10 (Linux) / 100 (Windows) | `UNCOVERED_MIN_MB` | minimum size for `scan` to list uncovered items |
| `clean.<os>.uncovered_always` | | | cache dir names `scan` never reports as uncovered |
| `[[clean.<os>.leftovers]]` | | | `detect` names -> `paths` reported by `leftovers` when none of the names is installed |
| `clean.disabled` | `[]` | | target ids never listed or run |
| `clean.opt` | `[]` | | target ids moved from the default tier to opt |
| `[[clean.paths]]` | | | extra path-only targets: `id`, `tier`, `desc`, `paths` (`~/` allowed), optional `os`; every path still passes the scope check of [4.1](#41-safety-measures) |

Tool-specific location variables are honored too: `XDG_*`, `CARGO_HOME`, `VCPKG_ROOT`, `GRADLE_USER_HOME`, `PRE_COMMIT_HOME`, `BUN_INSTALL`, `NUGET_HTTP_CACHE_PATH`, `NUGET_PACKAGES`,
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

The default tier needs no sudo; running apps are skipped automatically. If uv is not on the systemd user session's PATH, replace `uv` with its absolute path.

---

## 5. Commit checks (pre-commit)

The repo uses [pre-commit](https://pre-commit.com) to run checks automatically at `git commit` and `git push`, keeping plaintext secrets out. It needs only Python and git; both platforms share one config. restore's `hooks` step installs it; manual install:

```bash
uv tool install pre-commit        # or pacman -S pre-commit
pre-commit install                # installs both pre-commit and pre-push
pre-commit install-hooks          # optional: pre-build the envs (first run downloads Go and builds gitleaks)
```

`.git/hooks/*` contain this machine's Python path; they are generated per machine — do not copy or commit them. Building gitleaks the first time needs the Go module proxy; set `HTTPS_PROXY` briefly if a direct connection fails.

### 5.1 Files involved

| File | Purpose |
|---|---|
| `.pre-commit-config.yaml` | hook list, remote hooks pinned to exact versions |
| `.gitleaks.toml` | gitleaks built-in rules plus a WakaTime rule; skips `encrypted_*.age` ciphertext |
| `.gitattributes` | LF line endings everywhere (immune to Windows `core.autocrlf`); `*.age`, `*.ptb` untouched; `*.bat`, `*.cmd` use CRLF |
| `src/dotfiles/utils/common/guard/` | repo-specific rules (`checks.py`); imported by `dotfiles backup` / `check` and run directly by pre-commit (`python src/dotfiles/utils/common/guard …`), so standard library only. New checks plug into its `CHECKS` registry ([architecture.md](architecture.md#4-guard-the-extension-interface)) |

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

At `git push`, `dotfiles-guard-push` checks the files added or modified by **every** commit about to be pushed, so commits made with `--no-verify` are caught too — even when a later commit removes the secret.

The `dotfiles-guard` rules (identical in `staged`, `push` and `tree` modes). Deployment targets follow chezmoi rules: files under the dir named by `.chezmoiroot` (e.g. `home/`) map to `~` targets, `system/<distro>/` maps to system paths, and the rest (tool code, package manifests) are not deployment targets but are secret-scanned all the same.

| Rule | Level | Meaning |
|---|---|---|
| `age-not-ciphertext` / `age-armor-truncated` | error | a `*.age` file is not a complete age ciphertext |
| `age-without-encrypted-prefix` / `encrypted-prefix-without-age` | error | the `encrypted_` prefix and the `.age` suffix do not come in pairs |
| `must-encrypt(<target>)` | error | the target belongs to a must-encrypt category but is not encrypted (`ENCRYPT_REQUIRED` / `ENCRYPT_EXEMPT`) |
| secret rule names (e.g. `github-token`) | error | plaintext matching `SECRET_RULES`; `backup` uses the same rules to decide on encryption |
| `no-platform-owner(<target>)` | error | a `~` target with no entry in `platforms.toml`, read from the same snapshot (index, pushed commit, worktree); it would deploy on every platform ([3.3](#33-platform-ownership)) |
| `absolute-home-path` | warning | contains this machine's home directory (literal path, or the same user as `C:\Users\<user>`, JSON-escaped or `/c/Users/<user>`), which is invalid on another machine. Generic examples such as `/home/username` are not reported |

`ENCRYPT_REQUIRED` covers: `.ssh/*` (except `*.pub`, `known_hosts*`), `.gnupg/*`, `.pypirc`, `.npmrc`, `.netrc`/`_netrc`, `.git-credentials`, `.docker/config.json`, gh's `hosts.yml`, `.wakatime.cfg`, `.aws/credentials`, `.kube/config`, rclone configs, `*.pem`/`*.key`/`*.p12`/ `*.pfx`, `id_rsa`/`id_ecdsa`/`id_ed25519`, `.env*` (except `.env.example`), plus `/etc/shadow*`, `/etc/sudoers*`, `/etc/crypttab`, NetworkManager connections, WireGuard, SSH host keys and the mihomo config under `system/`.

### 5.3 When blocked

Output contains only `file:line` and the rule name.

| Situation | What to do |
|---|---|
| a sensitive file was added in plaintext | `git restore --staged <source>`, `chezmoi forget <target>`, then `chezmoi add --encrypt <target>` |
| a token slipped into a plaintext config | move the token to an encrypted file or an env var, or track the whole file encrypted |
| false positive | add a trailing `gitleaks:allow` comment (both tools honor it), or adjust `utils/common/guard/checks.py` / `.gitleaks.toml` |
| committed with `--no-verify`, not yet pushed | pre-push will catch it; `git reset --soft <last clean commit>` and redo |
| already pushed | **rotate that credential immediately**; rewriting git history cannot un-publish it |

Temporarily skip one hook: Linux `SKIP=gitleaks git commit …`; Windows `$env:SKIP = "gitleaks"; git commit …; Remove-Item Env:SKIP`.

### 5.4 Stash patches

Before running hooks, pre-commit saves **unstaged** changes as `$PRE_COMMIT_HOME/patch*` and never deletes them; if the worktree held unencrypted sensitive content at that moment, it stays in the patch as plaintext. `prune-precommit-stash` deletes patches older than 1 hour at every commit (the newest patch is left for the commit in progress to restore the worktree), and `clean run precommit` deletes them too. To purge right now (confirm no commit is in progress):

```bash
uv run dotfiles guard prune-stash --max-age-minutes 0
```

---

## 6. Compatibility notes

This repo is cloned directly into `~/.local/share/chezmoi`, chezmoi's default source directory, so the checkout is the working directory.

`chezmoi init`, `apply` and `update` work with default settings; daily sync is `git pull` + `chezmoi apply`.

Because `.chezmoiroot` names `home/` as the source state, the tool files at the repo top (`src/`, `tests/`, `Justfile`, ...) are never deployed into `~`. Fresh machines clone straight into this path too (see `bootstrap.sh`); cloning to a separate directory and applying from there would leave two copies that drift.

### 6.1 Traditional chezmoi commands

All traditional commands work transparently; chezmoi resolves the real source paths through `.chezmoiroot`:

| Command | Behavior |
|---|---|
| `chezmoi edit <target>` | opens the source file under `home/`; encrypted files are decrypted for editing and re-encrypted on save |
| `chezmoi edit --apply <target>` | edit and deploy in one step |
| `chezmoi re-add` | re-imports changed plaintext targets and re-encrypts changed encrypted ones (`dotfiles backup` wraps this) |
| `chezmoi add <file>` | works as usual; the new target needs an owner in `platforms.toml` ([3.3](#33-platform-ownership)): `dotfiles backup` registers it, and the commit hooks block it until then |
| `chezmoi managed / diff / verify / forget` | unaffected; `chezmoi source-path` resolves to `~/.local/share/chezmoi/home` |

Two caveats:

- `re-add` does not handle templates (`.tmpl`); when a template target drifts, `backup` lists it and the change must be merged back into the `.tmpl` by hand — exact steps in [6.3](#63-cases-that-need-a-manual-merge).

- Never edit an `.age` source file by hand — always go through `chezmoi edit` ([6.3](#63-cases-that-need-a-manual-merge)).

### 6.2 Re-syncing after pulling commits

Pulling (e.g. after `dev` is merged into `main`) needs no re-initialization — the source directory does not change. To bring the real system back in sync with the new source state:

1. `git fetch && git pull` (or switch to `main` to follow the shared branch).

2. `dotfiles backup -n`: check for drift — live files modified since the last apply (apply would overwrite them). Keep wanted changes with `dotfiles backup --commit`, or edit the source files directly.

3. `dotfiles apply` to preview, then `dotfiles apply --apply`: deploys the source state; files about to be overwritten or removed are backed up to `~/.local/state/dotfiles/pre-apply-*` first ([6.4](#64-dotfiles-apply)).

4. `dotfiles audit --apply` if permissions need tightening, then `dotfiles doctor` and `dotfiles check` to confirm.

In step 2, "keep the change" vs "let apply overwrite" is decided per file. The next section gives the exact steps for every place the docs say "merge by hand".

### 6.3 Cases that need a manual merge

#### Plaintext target drifted

The live file changed and you want to keep the change:

```bash
chezmoi diff ~/.config/foo/bar.conf   # what apply would change
chezmoi add ~/.config/foo/bar.conf    # fold the live content back into the source state
uv run dotfiles check                 # platform ownership must stay complete
```

`dotfiles backup` folds back every drifted plaintext target at once; run `backup -n` first to preview what it would fold in. If instead the source should win, do nothing — apply overwrites the live file (`restore` backs it up to `~/.local/state/dotfiles/pre-apply-*` first).

#### Template target drifted

E.g. `~/.gitconfig`, rendered from `home/dot_gitconfig.tmpl`. `re-add` and `backup` both skip templates, so the live change must go into the `.tmpl` by hand. First look at the diff from both sides:

```bash
chezmoi diff ~/.gitconfig                        # last-applied state vs the live file
chezmoi cat ~/.gitconfig | diff - ~/.gitconfig   # current template render vs the live file
```

Go through the diff hunk by hunk and copy the wanted live changes into `home/dot_gitconfig.tmpl` — wrap platform-dependent changes in template logic (see [3.3](#33-platform-ownership)). Then deploy and confirm the merge is complete:

```bash
chezmoi apply ~/.gitconfig
chezmoi cat ~/.gitconfig | diff - ~/.gitconfig   # empty output = back in sync
```

#### Encrypted target drifted

Taking `~/.ssh/config` as an example:

```bash
chezmoi edit ~/.ssh/config            # decrypts to a temp file, opens $EDITOR, re-encrypts on save
# or, if the live file is authoritative:
chezmoi add --encrypt ~/.ssh/config   # re-imports it; the encrypted_ prefix is kept
```

Never edit the `.age` source file itself; `dotfiles check` fails when the `encrypted_` prefix and the `.age` suffix stop coming in pairs ([5.2](#52-what-is-checked)).

#### New file added

See also [3.3](#33-platform-ownership):

```bash
chezmoi add ~/.config/foo/bar.conf         # or `chezmoi add --encrypt` for sensitive content
uv run dotfiles backup --commit            # registers it under the current platform
# to deploy it on both platforms, move the entry to shared in platforms.toml by hand
```

### 6.4 dotfiles apply

The reverse of `backup`: deploys the repo's source state into `~`.

```
dotfiles apply [TARGET...]            preview (default)
dotfiles apply --apply [TARGET...]    deploy
```

- The preview lists every pending change (`A` added, `D` deleted, `M` modified). Plaintext targets
  show their diff; **encrypted targets get one line and are never decrypted on screen**. A `!` marks a
  file you edited locally since the last apply — applying overwrites it, so keep such edits first with
  `dotfiles backup` or `chezmoi merge` ([6.3](#63-cases-that-need-a-manual-merge)).
- `--apply` rehearses the whole apply first (nothing is written if decryption or a template fails;
  like the real apply it passes `--force`, so locally edited files do not stop it),
  copies the files about to be overwritten or removed to `~/.local/state/dotfiles/pre-apply-*`, then runs
  `chezmoi apply`. It is the same code as restore's `dotfiles` step (`src/dotfiles/utils/common/deploy.py`).
- After deploying files under `~/.config/fcitx5`, a running fcitx5 is told to reload them over D-Bus
  (`ReloadConfig` for `config` / `profile`, `ReloadAddonConfig <addon>` for each `conf/<addon>.conf`; the
  global reload does not reach addon configs). fcitx5 keeps its config in memory and writes it back on
  exit, so without this the next logout would undo the deploy.
- `TARGET` limits it to some paths under `~` (files or directories chezmoi manages).
- Externals and `/etc` are not included: use `dotfiles restore --only externals` and
  `dotfiles system-restore`.

### 6.5 Pitfalls

Collected while syncing two Linux machines; machine-specific ones are in [arch.md](arch.md) and [kali.md](kali.md).

- Do not write `$HOME` or `~` in a config file unless that program expands it: zellij's `layout_dir` and aria2 do not; zsh, systemd's `%h` and npm's `${HOME}` do. Otherwise use a template with `{{ .chezmoi.homeDir }}`.
- Guard every `.` / `source` in shell startup files with an existence check: the machines have different tools installed, and a failing `.` aborts a POSIX login shell.
- `chezmoi re-add` copies the live mode back into the source: after a program rewrites a file as 644, a plain re-add drops `private_`. Use `dotfiles backup` (it tightens first), or `chmod 600` before re-adding.
- Quitting chezmoi's pager with q during a long `chezmoi diff` hangs it: use `--no-pager | less -R`, or `dotfiles apply` to preview.
- `re-add` skips template targets: merge changes into the `.tmpl` by hand ([6.3](#63-cases-that-need-a-manual-merge)).
- Encrypted files are not checked for home paths: a hard-coded path inside one goes unnoticed (the old `.zshrc` and VS Code's `settings.json` both had some).
- In zsh, a whole command in a variable (`C="chezmoi --config …"; $C …`) is not split on spaces: use a function, or `${=C}`.
- Programs that write their config back (fcitx5) need a reload after apply (`dotfiles apply` does it for fcitx5), and list values they write back may change order: keep only the entries you need.
- `apply`'s diff prints plaintext targets to the terminal: never put a token in a plaintext target (`.zshrc`'s are in the encrypted `secrets.zsh`).
- `.chezmoiignore` wins over `.chezmoiremove`: a target ignored on a machine (another platform's, or a missing tag) is not removed there; move it away by hand.

---

## 7. Development and tests

```bash
uv run pytest                     # or just test
uvx ruff check src tests
```

Tests never touch the real system: the Windows cleanup targets' filesystem logic runs on any platform by pointing `%LOCALAPPDATA%` etc. at temp dirs; platform ownership tests render the other platform's view with `chezmoi --override-data '{"chezmoi":{"os":"windows"}}'`.

See what would deploy in the Windows view from Linux:

```bash
chezmoi --source ~/.local/share/chezmoi --override-data '{"chezmoi":{"os":"windows"}}' managed
```

## 8. Known limitations

- **Windows: run for real once, on 2026-11-04**: `doctor`, `apply`, `backup`, `restore` (without `--apply`), `clean scan`, `clean run` (preview), `audit` and `system-restore` all ran on a physical machine; what turned up is in [win.md](win.md) 7. Still unexercised there: `clean run --apply`, `audit --apply`, `bootstrap.ps1 --apply` and a restore onto a fresh Windows. The original PowerShell scripts are in commit `a15c9b5` (`git show a15c9b5:scripts/clean.ps1`).

- **scoop comes from its official installer script**: there is no winget package. `bootstrap.ps1 --apply` downloads the installer to a temporary file, prints its path so you can read it, and runs it only after you confirm (`--yes` skips the question); it refuses an administrator session, as scoop does. restore's packages step stops with instructions while scoop is missing.

- **Windows commands that find a program by name use the first match on `PATH`**: from Git Bash, Git's `usrin` comes before `System32`, so a bare `whoami` is GNU's (no `/user`). Use absolute paths for a Windows program whose name Git also ships (`whoami` already does); `chezmoi managed -p absolute` prints forward slashes, so compare paths as `Path`, not as strings.

- **One `/etc` backup per distro**: two machines with the same distro share `system/<distro>/`. Back up `/etc` on one of them only (use `backup --no-system` on the other).

- **Third-party repos like archlinuxcn**: on a new system, before `/etc/pacman.conf` is restored and the matching keyring installed, their packages are listed as "AUR or unknown". Handle those as restore instructs, then rerun `--only packages`. The same goes for third-party apt sources (e.g. VS Code's) and their keyrings on Debian-like systems.

- **One apt manifest for all Debian-like machines**: `aptpkg.txt` is per package manager, like `archpkg.txt`; two different Debian derivatives would share it.

- **Same-name targets with different content on the two platforms** (e.g. `.wakatime.cfg`, `.docker/config.json`) are currently managed by Windows only; when Linux needs one, turn it into a per-platform template and register it in `shared`.

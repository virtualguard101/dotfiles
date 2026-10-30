#Requires -Version 5.1
<#
Fresh-machine bootstrap (Windows): installs git / chezmoi / uv via winget, clones the
dotfiles repo into chezmoi's default source dir, then hands over to `dotfiles restore`.
Everything else lives in Python; this script only does what must happen before uv exists.

This file is intentionally ASCII-only: Windows PowerShell 5.1 (the only shell on a fresh
install) reads BOM-less scripts as ANSI, which breaks non-ASCII text.

Usage (download, read, then run - do not pipe into iex, the key prompt needs a console):
  irm https://raw.githubusercontent.com/virtualguard101/dotfiles/main/bootstrap.ps1 -OutFile bootstrap.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1            # plan only
  powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 --apply    # execute

Options: -Repo URL  -Branch NAME  (or env DOTFILES_REPO / DOTFILES_BRANCH).
All other arguments are passed to `dotfiles restore` unchanged.
#>
[CmdletBinding()]
param(
  [string]$Repo = $(if ($env:DOTFILES_REPO) { $env:DOTFILES_REPO } else { 'https://github.com/virtualguard101/dotfiles.git' }),
  [string]$Branch = $(if ($env:DOTFILES_BRANCH) { $env:DOTFILES_BRANCH } else { 'main' }),
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$RestoreArgs = @()
)

$ErrorActionPreference = 'Stop'

function Log([string]$Text) { Write-Host '==> ' -ForegroundColor Blue -NoNewline; Write-Host $Text }
function Die([string]$Text) { Write-Host "x $Text" -ForegroundColor Red; exit 1 }
function Have([string]$Name) { [bool](Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
                [Environment]::GetEnvironmentVariable('Path', 'User')) -join ';'
}

# ---------------------------------------------------------------- 1. prerequisites
if (-not (Have 'winget')) { Die 'winget not found: install "App Installer" from the Microsoft Store, then rerun' }
$packages = [ordered]@{ git = 'Git.Git'; chezmoi = 'twpayne.chezmoi'; uv = 'astral-sh.uv' }
foreach ($tool in $packages.Keys) {
  if (Have $tool) { continue }
  Log "installing $tool ($($packages[$tool]), winget)"
  & winget install --id $packages[$tool] --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
  if ($LASTEXITCODE -ne 0) { Die "winget install $($packages[$tool]) failed (exit $LASTEXITCODE)" }
  Refresh-Path
  if (-not (Have $tool)) { Die "$tool installed but not on PATH; open a new terminal and rerun" }
}

# ---------------------------------------------------------------- 2. repository
$src = Join-Path $HOME '.local\share\chezmoi'
function Repo-Id([string]$Url) {
  ($Url -replace '^(https?://|ssh://)?(git@)?', '' -replace ':', '/' -replace '\.git$', '' -replace '/$', '').ToLowerInvariant()
}
if (Test-Path -LiteralPath (Join-Path $src '.git')) {
  $origin = (& git -C $src remote get-url origin 2>$null)
  if ((Repo-Id "$origin") -ne (Repo-Id $Repo)) { Die "$src is a clone of another repository (origin: $origin); move it away first" }
  Log "using existing clone $src"
  if (-not (& git -C $src status --porcelain)) {
    & git -C $src pull --ff-only --quiet
    if ($LASTEXITCODE -ne 0) { Log 'cannot fast-forward (offline or local commits); keeping the current version' }
  } else {
    Log 'working tree has uncommitted changes; not updating'
  }
} elseif ((Test-Path -LiteralPath $src) -and (Get-ChildItem -LiteralPath $src -Force | Select-Object -First 1)) {
  Die "$src exists and is not a git clone; move it away first"
} else {
  Log "cloning $Repo ($Branch) into $src"
  & git clone --branch $Branch $Repo $src
  if ($LASTEXITCODE -ne 0) { Die 'git clone failed' }
}
foreach ($f in '.chezmoiroot', 'pyproject.toml') {
  if (-not (Test-Path -LiteralPath (Join-Path $src $f))) { Die "$src does not have the expected layout (missing $f)" }
}

# ---------------------------------------------------------------- 3. hand over to Python
Log "running dotfiles restore $($RestoreArgs -join ' ')"
& uv run --project $src --frozen --no-dev --quiet dotfiles restore @RestoreArgs
exit $LASTEXITCODE

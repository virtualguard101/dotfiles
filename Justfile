# Shortcuts for common commands (optional; without just, run
# `uv run --frozen --no-dev dotfiles ...` directly). Recipes run under PowerShell 7 on Windows.
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

dot := "uv run --frozen --no-dev dotfiles"

# list all recipes
default:
    @just --list

# check the system, toolchain and encryption config (read-only)
doctor:
    {{ dot }} doctor

# post-reinstall recovery; shows the plan by default, e.g. just restore --apply
restore *args:
    {{ dot }} restore {{ args }}

# backup state (read-only)
status:
    {{ dot }} status

# backup; e.g. just backup -n / just backup --commit
backup *args:
    {{ dot }} backup {{ args }}

# security check
check:
    {{ dot }} check

# sensitive-file permission audit; just audit --apply tightens them
audit *args:
    {{ dot }} audit {{ args }}

# cache cleanup; e.g. just clean scan / just clean run --apply
clean *args:
    {{ dot }} clean {{ args }}

# run the tests (installs the dev dependency pytest)
test *args:
    uv run --frozen pytest {{ args }}

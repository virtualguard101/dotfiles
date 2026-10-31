"""Cross-platform dotfiles maintenance built on chezmoi. Entry point: dotfiles.cli."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dotfiles")     # single source of truth: pyproject.toml
except PackageNotFoundError:              # run from a source tree without installing (PYTHONPATH=src)
    __version__ = "0+unknown"

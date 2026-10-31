"""Entry point for pre-commit: `python src/dotfiles/utils/common/guard <subcommand>`.

Running a package directory puts that directory first on sys.path, where module names like
`git.py` or `checks.py` could shadow other imports. Replace it with src/ so the package is imported
the normal way; only the standard library and this package are loaded (no uv, no venv needed).
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[4])   # .../src

from dotfiles.utils.common.guard.cli import main  # noqa: E402

sys.exit(main())

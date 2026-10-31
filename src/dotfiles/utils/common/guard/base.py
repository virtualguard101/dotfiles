"""The extension interface of guard: a FileContext in, Findings out.

A check is a plain function `check(ctx: FileContext) -> Iterable[Finding]` listed in
checks.CHECKS. It must be pure (no I/O, no git): the same check then works unchanged for the index
(pre-commit), every pushed commit (pre-push) and the worktree (dotfiles check). A Finding carries a
rule name and a location only — never the matched content — so no check can leak a secret into
terminal output or CI logs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterable, Optional

MAX_BYTES = 5 * 1024 * 1024
SKIPPED_MODES = ("120000", "160000")     # symlink, submodule: nothing to read


@dataclass
class Finding:
    level: str      # error (blocks the commit/push) | warn
    rule: str
    path: str
    line: int = 0
    where: str = ""  # short commit SHA (push mode)

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        prefix = f"{self.where} " if self.where else ""
        return f"{prefix}{loc}: [{self.rule}]"


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


@dataclass
class FileContext:
    path: str                   # relative to the repo top, with /
    data: bytes
    mode: str = "100644"        # git file mode
    _target: object = field(default=..., repr=False)

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def is_age(self) -> bool:
        return self.path.endswith(".age")

    @property
    def target(self) -> Optional[str]:
        """Deployment target (relative to ~, or /... under system/); None when not a target."""
        if self._target is ...:
            from .paths import target_of
            self._target = target_of(self.path)
        return self._target     # type: ignore[return-value]

    @property
    def text(self) -> Optional[bytes]:
        """Content for text checks; None for age files, binaries and files over MAX_BYTES."""
        if self.is_age or len(self.data) > MAX_BYTES or is_binary(self.data):
            return None
        return self.data


Check = Callable[[FileContext], Iterable[Finding]]

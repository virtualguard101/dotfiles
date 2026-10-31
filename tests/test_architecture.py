"""Layering rules from docs/architecture.md, checked on the import statements of every module."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "dotfiles"


def modules() -> dict[str, Path]:
    """dotted name (relative to the dotfiles package) -> file; packages are named by their __init__."""
    out = {}
    for p in PKG.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(PKG).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        out[".".join(parts) or "<root>"] = p
    return out


def module_imports(name: str, path: Path) -> tuple[set[str], set[str]]:
    """dotfiles modules imported at module level and inside functions, as absolute dotted names
    without the leading "dotfiles." (relative imports resolved)."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]

    def names(node: ast.AST) -> set[str]:
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".") if package and package != "<root>" else []
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                mod = ".".join(base + ([node.module] if node.module else []))
            elif node.module and node.module.startswith("dotfiles"):
                mod = node.module[len("dotfiles."):] if node.module != "dotfiles" else ""
            else:
                return set()
            return {f"{mod}.{a.name}".strip(".") for a in node.names}
        if isinstance(node, ast.Import):
            return {a.name[len("dotfiles."):] for a in node.names if a.name.startswith("dotfiles.")}
        return set()

    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = set().union(*(names(n) for n in tree.body)) if tree.body else set()
    every = set().union(*(names(n) for n in ast.walk(tree)))
    return top, every - top


MODS = modules()


def test_guard_is_self_contained():
    """pre-commit runs utils/common/guard directly with the system Python: the package may import
    only itself, and the package __init__ files on its import path must import nothing."""
    for name, path in MODS.items():
        if name.startswith("utils.common.guard"):
            top, lazy = module_imports(name, path)
            outside = {i for i in top | lazy if not i.startswith("utils.common.guard")}
            assert not outside, f"{name} imports {outside}"
    for chain in ("<root>", "utils", "utils.common"):
        top, lazy = module_imports(chain, MODS[chain])
        assert not top and not lazy, f"{chain}/__init__.py imports {top | lazy}"


def test_utils_never_import_cli():
    for name, path in MODS.items():
        if name.startswith("utils"):
            top, lazy = module_imports(name, path)
            bad = {i for i in top | lazy if i.startswith("cli")}
            assert not bad, f"{name} imports {bad}"


def test_generic_utils_do_not_depend_on_common():
    """ui, fsutil and platforms are project-agnostic: they sit below utils/common."""
    for name, path in MODS.items():
        if name.startswith("utils") and not name.startswith("utils.common") and name != "utils":
            top, lazy = module_imports(name, path)
            bad = {i for i in top | lazy if i.startswith("utils.common")}
            assert not bad, f"{name} imports {bad}"


def test_commands_import_each_other_only_lazily():
    """A command module loads other commands inside functions, so one broken or platform-specific
    command never breaks the others at import time. cli/clean/* may import within cli.clean."""
    for name, path in MODS.items():
        if not name.startswith("cli.") or name == "cli":
            continue
        own = "cli.clean" if name.startswith("cli.clean") else name
        top, _ = module_imports(name, path)
        bad = {i for i in top if i.startswith("cli") and not i.startswith(own)}
        assert not bad, f"{name} imports {bad} at module level"

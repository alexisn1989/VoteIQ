#!/usr/bin/env python3
"""
Pre-commit safety checks for VoteIQ.

Run by .git/hooks/pre-commit before every commit. Catches:
  1. Syntax errors in staged .py files
  2. Hard imports in main.py that reference files not yet committed/staged
  3. Local module imports in any staged .py file that reference missing files

Install hook (run once):
    python scripts/pre_commit_check.py --install
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── Local module roots that map to file paths ────────────────────────────────
# These are modules that live inside the repo (not pip packages).
# Add any new top-level local packages here.
_LOCAL_ROOTS = {
    "voteiq",
    "config",
    "civic_analyst",
    "address_lookup",
    "ingest_news",
}


def _staged_files() -> list[Path]:
    """Return list of staged .py files (added or modified)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, cwd=ROOT,
    )
    return [ROOT / f for f in result.stdout.splitlines() if f.endswith(".py")]


def _tracked_files() -> set[Path]:
    """Return all files currently tracked by git (committed + staged)."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=ROOT,
    )
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, cwd=ROOT,
    )
    paths = set()
    for line in result.stdout.splitlines() + staged.stdout.splitlines():
        paths.add(ROOT / line.strip())
    return paths


def check_syntax(staged: list[Path]) -> list[str]:
    """Compile each staged .py file and report syntax errors."""
    errors = []
    for path in staged:
        if not path.exists():
            continue
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout).strip()
            errors.append(f"  SYNTAX ERROR  {path.relative_to(ROOT)}\n    {msg}")
    return errors


def _module_to_path(module: str) -> Path | None:
    """Convert a dotted module name to a file path relative to ROOT, if local."""
    root_name = module.split(".")[0]
    if root_name not in _LOCAL_ROOTS:
        return None
    parts = module.split(".")
    # Try as package (dir/__init__.py) or module (.py)
    as_module = ROOT / Path(*parts).with_suffix(".py")
    as_pkg = ROOT / Path(*parts) / "__init__.py"
    if as_module.exists():
        return as_module
    if as_pkg.exists():
        return as_pkg
    # Return the expected .py path even if missing (for error reporting)
    return as_module


def _extract_imports(path: Path) -> list[str]:
    """Parse a Python file and return all local module names it imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []  # syntax check will catch this separately

    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # absolute import only
                modules.append(node.module)
        elif isinstance(node, ast.Call):
            # Catch: _safe_include("voteiq.api.routes.foo", ...)
            func = node.func
            func_name = ""
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            if func_name in ("_safe_include", "import_module"):
                if node.args and isinstance(node.args[0], ast.Constant):
                    modules.append(node.args[0].value)
    return modules


def check_imports(staged: list[Path], tracked: set[Path]) -> list[str]:
    """
    For every staged .py file, resolve local imports and verify the target
    file is either already committed or also staged in this commit.
    """
    errors = []
    # Also always check main.py for hard imports (even if not staged)
    files_to_check = list(staged)
    main_py = ROOT / "main.py"
    if main_py not in set(staged) and main_py.exists():
        files_to_check.append(main_py)

    for src_file in files_to_check:
        if not src_file.exists():
            continue
        for mod in _extract_imports(src_file):
            target = _module_to_path(mod)
            if target is None:
                continue  # third-party package, skip
            if not target.exists():
                rel_src = src_file.relative_to(ROOT)
                rel_tgt = target.relative_to(ROOT)
                errors.append(
                    f"  MISSING FILE  {rel_tgt}\n"
                    f"    imported by: {rel_src}\n"
                    f"    module:      {mod}"
                )
            elif target not in tracked:
                rel_src = src_file.relative_to(ROOT)
                rel_tgt = target.relative_to(ROOT)
                errors.append(
                    f"  NOT STAGED    {rel_tgt}\n"
                    f"    imported by: {rel_src}\n"
                    f"    module:      {mod}\n"
                    f"    -> run: git add {rel_tgt}"
                )
    return errors


def install_hook() -> None:
    """Write .git/hooks/pre-commit to call this script."""
    hook_path = ROOT / ".git" / "hooks" / "pre-commit"
    hook_path.write_text(
        "#!/bin/sh\n"
        f'python "{ROOT / "scripts" / "pre_commit_check.py"}"\n',
        encoding="utf-8",
    )
    # Make executable on Unix; no-op on Windows
    try:
        hook_path.chmod(0o755)
    except Exception:
        pass
    print(f"[OK] Hook installed at {hook_path}")


def main() -> int:
    if "--install" in sys.argv:
        install_hook()
        return 0

    staged = _staged_files()
    if not staged:
        return 0  # nothing to check

    tracked = _tracked_files()

    syntax_errors = check_syntax(staged)
    import_errors = check_imports(staged, tracked)

    all_errors = syntax_errors + import_errors
    if not all_errors:
        print(f"[OK] pre-commit: {len(staged)} file(s) checked -- all OK")
        return 0

    print("\n=== PRE-COMMIT CHECK FAILED ===")
    if syntax_errors:
        print(f"  {len(syntax_errors)} SYNTAX ERROR(S)")
        for e in syntax_errors:
            print(e)
    if import_errors:
        print(f"  {len(import_errors)} IMPORT PROBLEM(S)")
        for e in import_errors:
            print(e)
    print("=" * 34)
    print("\nFix the above before committing.")
    print("To bypass (emergency only): git commit --no-verify\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())

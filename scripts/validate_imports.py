#!/usr/bin/env python3
"""Import every module under src/ to catch syntax/import errors early.

Replaces a hand-maintained list of `python -c "import X"` lines in the
Makefile, which silently stopped covering new modules the moment someone
added one and forgot to update the list (it missed server.py, net_guard.py,
llm_health.py, deep_dive.py, newsletter.py, and everything added since).
Discovering modules by walking the tree means a module can never again be
skipped by omission.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"


def discover_modules() -> list[str]:
    modules = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        if relative.name == "__init__.py":
            module_name = ".".join(relative.parent.parts)
        else:
            module_name = ".".join(relative.with_suffix("").parts)
        modules.append(module_name)
    return modules


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    modules = discover_modules()
    failures = []
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - report every failure, don't stop at the first
            failures.append((module_name, exc))

    print(f"Checked {len(modules)} module(s) under src/.")
    if failures:
        print(f"{len(failures)} import failure(s):")
        for module_name, exc in failures:
            print(f"  - {module_name}: {exc}")
        return 1
    print("All modules import cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The repository's own `scripts` package answers to the name.

pywin32 - which arrives as a dependency of `mcp` - ships an importable
`win32/scripts/` namespace package. Running an entry point as
`python scripts\\bootstrap.py` puts `scripts/` on `sys.path` rather than the
repository root, so `import scripts` finds pywin32's and every
`scripts.something` import fails. It stopped `start.cmd` at
"[Setup 3/4] Preparing API dependencies" with

    ModuleNotFoundError: No module named 'scripts.model_assets'

and no later `sys.path` change could recover it: by then the name was bound.

Run in a subprocess with the same `sys.path` the launcher produces, because
the test suite runs from the repository root where the shadowing never
happens - the bug is invisible from in here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_bootstrap_imports_this_repositorys_scripts_package() -> None:
    probe = (
        "import runpy, sys;"
        # Exactly what running the file as a script gives you.
        "sys.path[0] = r'{scripts}';"
        "import importlib;"
        "spec = importlib.util.spec_from_file_location('bootstrap', r'{entry}');"
        "module = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(module);"
        "from scripts.model_assets import ensure_all;"
        "print('ok')"
    ).format(scripts=ROOT / "scripts", entry=ROOT / "scripts" / "bootstrap.py")

    finished = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )

    assert finished.returncode == 0, finished.stderr[-2000:]
    assert "ok" in finished.stdout

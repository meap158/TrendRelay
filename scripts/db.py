"""Database migration CLI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = (
    ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
)

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage TrendRelay database migrations."
    )
    parser.add_argument("command", choices=["upgrade", "current"])
    args = parser.parse_args()
    (ROOT / ".data").mkdir(parents=True, exist_ok=True)
    config = Config(str(ROOT / "services" / "api" / "alembic.ini"))
    config.set_main_option(
        "script_location", str(ROOT / "services" / "api" / "migrations")
    )
    if args.command == "upgrade":
        command.upgrade(config, "head")
    else:
        command.current(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

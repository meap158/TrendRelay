"""Build (once) and run the Gemini Web bridge for Tools > Video providers > Gemini Web.

    python scripts/gemini_web_bridge.py

Clones https://github.com/ntthanh2603/gemini-web-to-api into `.data/gemini-web/src/bridge`,
builds it with Go, and runs it from `.data/gemini-web/`, where Tools writes the `.env` the
bridge reads (the cookie, the account slot, the port). Everything it writes stays under
`.data`, including Go's caches, because `C:` has run out of space here.

The bridge only reads `.env` for variables the shell has not already set, and this machine's
shell sets `PORT` for other services. So the port is passed explicitly from the `.env`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".data" / "gemini-web"
SOURCE = HOME / "src" / "bridge"
BINARY = HOME / ("bridge.exe" if os.name == "nt" else "bridge")
REPO = "https://github.com/ntthanh2603/gemini-web-to-api.git"
#: The release the patch was written against. The patch lets a video request carry the
#: product picture, which the bridge refuses without it.
PINNED = "cb40694"
PATCH = Path(__file__).with_name("gemini_web_bridge.patch")


def _port() -> str:
    env = HOME / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PORT="):
                return line.split("=", 1)[1].strip()
    return "4981"


def _go_env() -> dict[str, str]:
    scratch = HOME / "gotmp"
    for folder in ("gocache", "gopath", "gotmp"):
        (HOME / folder).mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "GOCACHE": str(HOME / "gocache"),
        "GOPATH": str(HOME / "gopath"),
        "GOTMPDIR": str(scratch),
        "TMP": str(scratch),
        "TEMP": str(scratch),
    }


def _patched() -> bool:
    """Whether the source already carries the video-reference patch."""
    done = subprocess.run(
        ["git", "apply", "--reverse", "--check", str(PATCH)],
        cwd=SOURCE, capture_output=True, check=False,
    )
    return done.returncode == 0


def main() -> int:
    if not (HOME / ".env").is_file():
        print("No .env yet: save the cookie in Tools > Video providers > Gemini Web first.")
        return 1
    if shutil.which("go") is None and not BINARY.is_file():
        print("Go is needed to build the bridge (https://go.dev/dl/).")
        return 1
    env = _go_env()
    if SOURCE.is_dir() and not _patched():
        subprocess.run(["git", "apply", str(PATCH)], cwd=SOURCE, check=True)
        BINARY.unlink(missing_ok=True)
    if not BINARY.is_file():
        if not SOURCE.is_dir():
            SOURCE.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", REPO, str(SOURCE)], check=True)
            subprocess.run(["git", "checkout", "-q", PINNED], cwd=SOURCE, check=True)
        subprocess.run(
            ["go", "build", "-o", str(BINARY), "./cmd/server"], cwd=SOURCE, env=env, check=True,
        )
    run_env = {**env, "PORT": _port()}
    print(f"Gemini Web bridge on http://127.0.0.1:{run_env['PORT']} (Ctrl+C to stop)")
    return subprocess.call([str(BINARY)], cwd=HOME, env=run_env)


if __name__ == "__main__":
    sys.exit(main())

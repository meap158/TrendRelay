"""Draw the built-in character cast with the Grok Build CLI.

An operator tool, run by hand, not by the app. Each character is two calls
to the CLI's Imagine tools: `image_gen` for the portrait, then `image_edit`
on that portrait for the consistency sheet, both with the prompts in
`trendrelay_api.character_catalogue`. The results are written to
`services/api/src/trendrelay_api/character_defaults/<slug>/`, which is what
ships. A character whose two files already exist is skipped, so a rerun only
draws what is missing; delete a folder to draw that character again.

It uses the operator's signed-in Grok account, not an API key, and every
call is a generation on that account - which is why it is a script someone
runs on purpose rather than something the app does on start.

    python scripts/build_default_characters.py            # every missing one
    python scripts/build_default_characters.py aiko wei   # just these
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = (
    ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
)

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

from PIL import Image  # noqa: E402

from trendrelay_api.character_catalogue import (  # noqa: E402
    DEFAULT_CHARACTERS,
    DEFAULTS_BY_SLUG,
    CharacterSpec,
    default_paths,
    portrait_prompt,
    sheet_prompt,
)

#: Scratch for the CLI's own output, on the project drive.
WORK = ROOT / ".data" / "characters" / "build"
#: One CLI call can take minutes; a hung one should not hold the run forever.
CALL_TIMEOUT = 900


def _task(tool: str, prompt: str, target: Path, *, image: Path | None, aspect: str) -> str:
    source = f'image = "{image}", ' if image else ""
    return (
        f"Call the {tool} tool exactly once with {source}aspect_ratio = \"{aspect}\" and "
        "the prompt between BEGIN and END, verbatim. Then copy the generated image file "
        f'to exactly the file path "{target}.img" - a file, not a folder, whatever '
        "format the image is - and print only that path. "
        "Do not call any other image tool and do not edit any other file. If the tool "
        "refuses or fails, print FAILED and the reason, and do not retry.\n\n"
        f"BEGIN\n{prompt}\nEND\n"
    )


def _run_cli(task: str, target_stem: Path) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", dir=WORK, delete=False
    ) as handle:
        handle.write(task)
        task_file = Path(handle.name)
    try:
        completed = subprocess.run(
            ["grok", "--prompt-file", str(task_file), "--always-approve", "--max-turns", "12"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=CALL_TIMEOUT, cwd=ROOT, shell=sys.platform == "win32",
        )
    finally:
        task_file.unlink(missing_ok=True)
    # The CLI is told the exact file; it has also been seen making a folder
    # of that name and writing `1.jpg` inside, which is just as usable.
    found = sorted(
        path for path in target_stem.parent.glob(target_stem.name + "*")
        if path.is_file()
    ) or sorted(path for path in target_stem.glob("*") if path.is_file())
    if not found:
        tail = (completed.stdout + completed.stderr).strip()[-600:]
        raise RuntimeError(f"No image was written for {target_stem.name}: {tail}")
    return found[0]


def _save_jpeg(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGB").save(destination, "JPEG", quality=90, optimize=True)


def build(spec: CharacterSpec) -> str:
    portrait_path, sheet_path = default_paths(spec.slug)
    if portrait_path.is_file() and sheet_path.is_file():
        return f"{spec.slug}: already drawn"
    folder = WORK / spec.slug
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    if not portrait_path.is_file():
        drawn = _run_cli(
            _task("image_gen", portrait_prompt(spec.appearance, spec.outfit),
                  folder / "portrait", image=None, aspect="2:3"),
            folder / "portrait",
        )
        _save_jpeg(drawn, portrait_path)
    drawn = _run_cli(
        _task("image_edit", sheet_prompt(spec.outfit), folder / "sheet",
              image=portrait_path, aspect="2:3"),
        folder / "sheet",
    )
    _save_jpeg(drawn, sheet_path)
    return f"{spec.slug}: drawn"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slugs", nargs="*", help="Only these characters.")
    parser.add_argument("--parallel", type=int, default=3)
    args = parser.parse_args()
    unknown = [slug for slug in args.slugs if slug not in DEFAULTS_BY_SLUG]
    if unknown:
        parser.error(f"Unknown character: {', '.join(unknown)}")
    if shutil.which("grok") is None:
        parser.error("The grok CLI is not on PATH. Install Grok Build and sign in first.")
    chosen = [DEFAULTS_BY_SLUG[slug] for slug in args.slugs] or list(DEFAULT_CHARACTERS)
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {pool.submit(build, spec): spec for spec in chosen}
        for future, spec in futures.items():
            try:
                print(future.result(), flush=True)
            except Exception as error:  # noqa: BLE001 - report and keep going
                failures += 1
                print(f"{spec.slug}: FAILED {error}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

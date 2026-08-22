"""Prepare TrendRelay's isolated local transcription, OCR and translation runtimes.

The app does this itself now - Tools, or the transcription switch in the Library
- and that is the path to use, because it shows progress and says why something
failed. This stays for the case the interface cannot cover: a headless machine
being provisioned before anyone opens a browser at it.

It is deliberately a shell around `trendrelay_api.media_ai` rather than a second
implementation. The version pins, the language pairs and the order the steps run
in all live there, so what a terminal installs and what the button installs
cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.media_ai import (  # noqa: E402
    PROVIDER_TOOL,
    prepare_provider,
    provider_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["status", *(f"install-{provider}" for provider in PROVIDER_TOOL)],
    )
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(provider_status(), indent=2))
        return 0

    provider = args.command.removeprefix("install-")
    skipped = prepare_provider(
        provider,
        on_stage=lambda fraction, label: print(f"  {int(fraction * 100):3d}%  {label}", flush=True),
    )
    for note in skipped:
        print(f"  skipped {note}")
    print(f"{provider.title()} is ready and switched on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

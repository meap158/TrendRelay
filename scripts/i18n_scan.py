"""Find user-facing text still hardcoded in the web app.

A migration this size needs a number, not an impression. This reports what is
left to translate, per file, so progress is measurable and "done" is checkable
rather than a feeling.

It is deliberately a heuristic and says so: it looks at JSX text nodes and at
the attributes that reach a user's eyes or a screen reader. It will over-report
some strings that are really identifiers and under-report text built by
concatenation. Treat the number as a work list, not a proof.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

WEB_APP = Path(__file__).resolve().parents[1] / "apps" / "web" / "app"

#: Attributes whose values a person reads or hears.
VISIBLE_ATTRIBUTES = ("title", "placeholder", "aria-label", "alt", "label")

#: Text between tags: `>Some words<`, keeping only what has a letter in it.
JSX_TEXT = re.compile(r">([^<>{}\n][^<>{}]*)<")
ATTRIBUTE_TEXT = re.compile(
    rf'\b({"|".join(VISIBLE_ATTRIBUTES)})\s*=\s*"([^"]+)"'
)
HAS_LETTERS = re.compile(r"[A-Za-z]{2,}")
#: Things that look like text but are not: css, urls, keys, single symbols.
NOT_COPY = re.compile(
    r"^\s*(?:[\d\s.,:;%×·—–\-+/()\[\]]*|https?://\S*|[a-z-]+/[a-z-]+|"
    r"[a-zA-Z]+(?:[A-Z][a-z]+)+|#[0-9a-fA-F]{3,8}|&\w+;)\s*$"
)


def candidates(source: str) -> list[str]:
    found: list[str] = []
    for match in JSX_TEXT.finditer(source):
        text = match.group(1).strip()
        if text and HAS_LETTERS.search(text) and not NOT_COPY.match(text):
            found.append(text)
    for match in ATTRIBUTE_TEXT.finditer(source):
        text = match.group(2).strip()
        if text and HAS_LETTERS.search(text) and not NOT_COPY.match(text):
            found.append(text)
    return found


def scan(root: Path) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.tsx")):
        source = path.read_text(encoding="utf-8")
        strings = candidates(source)
        report.append(
            {
                "file": str(path.relative_to(root.parent)).replace("\\", "/"),
                "remaining": len(strings),
                # `t(` calls already there, so a half-migrated file reads as
                # half migrated rather than as untouched.
                "translated": source.count("t(\""),
                "samples": strings[:5],
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", type=Path, default=WEB_APP)
    args = parser.parse_args()

    report = scan(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0

    remaining = sum(int(item["remaining"]) for item in report)
    translated = sum(int(item["translated"]) for item in report)
    total = remaining + translated
    done = (translated / total * 100) if total else 100.0
    print(f"{translated}/{total} strings translated ({done:.0f}%)\n")
    print(f"{'file':52} {'left':>6} {'done':>6}")
    for item in sorted(report, key=lambda entry: -int(entry["remaining"])):
        if item["remaining"] or item["translated"]:
            print(f"{item['file']:52} {item['remaining']:>6} {item['translated']:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

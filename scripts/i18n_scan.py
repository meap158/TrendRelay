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


#: `>` and `<` are also the generic brackets, so `useState<Foo>(null)` looks
#: exactly like a JSX text node to a regex. Copy does not contain these.
NOT_PROSE = (
    "\n", ";", "=", "const ", "return ", "=>", "props.", "//",
    # Expression fragments that reach here because they contain words:
    # `threaders.length && (`, `0 && canEdit ? (`, `(response: Response): Promise`.
    "&&", "?", "(", ")", "[", "]",
)

#: Type names and expression fragments that survive the checks above because
#: they are short and alphabetic.
CODE_WORDS = {"json", "Promise", "Response", "void", "null", "undefined", "string"}

#: Strings that are on screen and deliberately not translated. Each is either
#: something a person types or clicks verbatim, or an acronym that is the term
#: of art in every one of these languages. Translating any of them would send
#: someone looking for a field, file or menu item that does not exist.
DO_NOT_TRANSLATE = {
    # Measurement acronyms every advertiser already reads.
    "ROAS", "ACoS", "TACoS",
    # Meta and Amazon literals: permissions, prefixes, menu paths, env keys.
    "ads_read", "business_management", "act_…", "Business",
    "Create Application", "Add New Credential",
    "Associates Central → Tools → Creators API",
    "Meta Ads Kit → Setup → Launch Meta login",
    "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_URL", "ZERNIO_API_KEY", "META_AD_ACCOUNT=act_…",
    # Platform identifiers used as <option value> and as their own label.
    "tiktok", "instagram", "youtube", "douyin", "other",
    "editor", "approver", "analyst", "owner",
    # Placeholder examples in form fields, shown to illustrate a format.
    "brand-team", "zernio", "faceless demo", "USD",
    "coffee, travel", "travel, espresso", "en, th", "US, TH", "TH, US",
    "portable espresso maker", "Portable espresso acceleration",
    # Product names. A brand is spelled the same in every one of these
    # languages, and "translating" one would name a service that does not exist.
    "TrendRelay", "Instagram", "Douyin", "TikTok", "YouTube", "Reddit",
    "Pinterest", "Google", "Supabase", "Amazon", "Meta", "Threads",
    # A filename shown in a <code> tag for the reader to type.
    ".env",
}

#: `rich()` passes React children in an object literal, so the scanner sees the
#: text between two `<code>` elements - `, businessManagement:` - and calls it
#: copy. Those are argument names in this file's own source, not anything on
#: screen.
OBJECT_LITERAL = re.compile(r"^,?\s*\w+:\s*$|^,\s")
#: `s.end_seconds`, `item.hot_value`: a dotted identifier with no spaces.
DOTTED_IDENTIFIER = re.compile(r"^\w+(?:\.\w+)+$")

#: A file path, a URL scheme, or an env-var name in shouting case.
LOOKS_LITERAL = re.compile(
    r"""^(?:
        [A-Za-z]:[\\/].*            # S:\Media\clip.mp4
      | \.?[\w./\\-]+\.[a-z0-9]{2,4}$   # .env, clip.mp4, page.tsx
      | [A-Z][A-Z0-9_]{3,}$         # SUPABASE_URL
      | \w+://.*                    # os-keyring://…
    )""",
    re.X,
)


def is_prose(text: str) -> bool:
    """Whether this looks like copy a translator should see.

    Deliberately conservative in both directions and honest about it: the point
    of the count is to know when the work is done, so a fragment of TypeScript
    inflating it is as unhelpful as a real sentence hidden from it.
    """
    if any(marker in text for marker in NOT_PROSE):
        return False
    if text in CODE_WORDS or text in DO_NOT_TRANSLATE:
        return False
    if OBJECT_LITERAL.match(text) or DOTTED_IDENTIFIER.match(text):
        return False
    return not LOOKS_LITERAL.match(text)


#: Comments are not shipped to anyone, and they are full of angle brackets:
#: a line explaining that `<video> and <audio>` share an interface reads to the
#: extractor as the JSX text node "and". Stripped before anything else, so the
#: count reflects the interface rather than the commentary about it.
LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def strip_comments(source: str) -> str:
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


def candidates(source: str) -> list[str]:
    source = strip_comments(source)
    found: list[str] = []
    for match in JSX_TEXT.finditer(source):
        text = match.group(1).strip()
        if text and HAS_LETTERS.search(text) and not NOT_COPY.match(text) and is_prose(text):
            found.append(text)
    for match in ATTRIBUTE_TEXT.finditer(source):
        text = match.group(2).strip()
        if text and HAS_LETTERS.search(text) and not NOT_COPY.match(text) and is_prose(text):
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

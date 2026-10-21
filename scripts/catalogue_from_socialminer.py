"""Turn a SocialMiner JSON export into reference-catalogue skeletons (PM-233).

    python scripts/catalogue_from_socialminer.py export.json --source hemell_official --out c.json

Reads `posts[]` from the export (docs/reference-catalogue.md lists the fields used).
For each post it writes the facts the export already holds (id, date, kind, slide
count, engagement, caption pattern) and leaves the annotation fields empty for a
person to fill in after viewing the images locally. Image URLs are NOT copied into
the catalogue: the catalogue never stores or republishes another brand's images.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KINDS = {"image": "single", "carousel": "carousel", "reel": "reel", "video": "reel"}
ANNOTATIONS: dict[str, Any] = {
    "structure": [],
    "framing": [],
    "poses": [],
    "productsPerFrame": [],
    "togetherSet": None,
    "palette": [],
    "styling": [],
    "notes": "",
}


def caption_pattern(text: str, hashtags: list[str]) -> str:
    """A coarse shape of the caption (length class and hashtag count), not the caption itself."""
    words = len(text.split())
    length = (
        "none" if words == 0 else "short" if words <= 12 else "medium" if words <= 40 else "long"
    )
    return f"{length} caption, {len(hashtags)} hashtag(s)"


def entry(post: dict[str, Any], source: str) -> dict[str, Any]:
    media_type = str(post.get("mediaType") or "other")
    urls = post.get("mediaUrls") or []
    stamp = post.get("timestamp")
    reactions = post.get("reactions") or {}
    likes = int(reactions.get("total") or 0) if isinstance(reactions, dict) else 0
    return {
        "source": source,
        "postId": str(post.get("externalId") or post.get("id") or ""),
        "postedAt": (
            datetime.fromtimestamp(stamp / 1000, UTC).date().isoformat()
            if isinstance(stamp, int | float)
            else None
        ),
        "kind": KINDS.get(media_type, media_type),
        "slideCount": len(urls) if media_type == "carousel" else 1,
        "engagement": {"likes": likes, "comments": int(post.get("commentCount") or 0)},
        "captionPattern": caption_pattern(
            str(post.get("text") or ""), list(post.get("hashtags") or [])
        ),
        **{
            key: (value.copy() if isinstance(value, list) else value)
            for key, value in ANNOTATIONS.items()
        },
    }


def build(export: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Catalogue skeletons for one account, newest first; other accounts' posts are ignored."""
    wanted = source.lower().lstrip("@")

    def belongs(post: dict[str, Any]) -> bool:
        # SocialMiner ids are "<platform>:<name>" (a post's sourceId is its account's id).
        ids = (str(post.get("sourceId") or ""), str(post.get("authorId") or ""))
        return any(i.lower().rpartition(":")[2].lstrip("@") == wanted for i in ids if i)

    posts = [p for p in export.get("posts") or [] if belongs(p)]
    posts.sort(key=lambda p: p.get("timestamp") or 0, reverse=True)
    return [entry(p, wanted) for p in posts]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path)
    parser.add_argument(
        "--source", required=True, help="Instagram account name, for example hemell_official"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        export = json.loads(args.export.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Cannot read the export: {error}", file=sys.stderr)
        return 1
    entries = build(export, args.source)
    if not entries:
        print(
            f"No posts for '{args.source}' in the export. "
            "Check the account name against sources[] in the file.",
            file=sys.stderr,
        )
        return 1
    args.out.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(entries)} catalogue entries written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

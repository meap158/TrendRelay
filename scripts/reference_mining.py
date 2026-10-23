"""Rank a SocialMiner export, fetch its pictures locally and build review sheets.

    python scripts/reference_mining.py rank   export.xlsx --batch 2026-10-23
    python scripts/reference_mining.py fetch  --batch 2026-10-23 [--limit N]
    python scripts/reference_mining.py sheets --batch 2026-10-23

Everything lands in `References/brand-pages/<batch>/`, which the repo ignores:
the pictures belong to the accounts that posted them, so they are only looked
at here and never committed, uploaded, or given to an image model
(docs/reference-catalogue.md). SOP/attribution/mine-reference-recipes.md is the
whole routine this is the first half of.

- `rank` reads the Posts sheet (or a SocialMiner JSON export), keeps one row per
  post id (sessions overlap), groups by `sourceId` (display names vary for the
  same account), and orders every post by engagement, highest first. It also
  records each post's percentile within its own account, because engagement
  is not comparable across accounts of different sizes.
- `fetch` downloads every slide, a few at a time, and is resumable: a slide
  already on disk is skipped. The CDN links are signed and expire within days
  of the export, so fetch right after mining.
- `sheets` draws one numbered contact sheet per post and, per account, profile
  grid sheets in posting order (three across, newest top-left), which is how
  a run of posts that tiles into one larger picture shows itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "References" / "brand-pages"
KINDS = {"image": "single", "carousel": "carousel", "reel": "reel", "video": "reel"}
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"


def batch_dir(batch: str) -> Path:
    return BASE / batch


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


_MEDIA_KEY = re.compile(r"/(\d+_\d+_\d+)_n\.")


def _urls(value: Any) -> list[str]:
    """The slides, once each. An export can list one picture twice, in two
    renditions with different query strings; the file name in the path is
    the picture, so repeats of it are dropped."""
    if isinstance(value, list):
        raw = [str(u).strip() for u in value if str(u).strip()]
    else:
        raw = [part.strip() for part in str(value or "").split("|") if part.strip()]
    seen: set[str] = set()
    kept = []
    for url in raw:
        match = _MEDIA_KEY.search(url)
        key = match.group(1) if match else url.split("?")[0]
        if key not in seen:
            seen.add(key)
            kept.append(url)
    return kept


def read_posts(path: Path) -> list[dict[str, Any]]:
    """Rows of the export as dicts, from the xlsx Posts sheet or a JSON export."""
    if path.suffix.lower() == ".json":
        return list(json.loads(path.read_text(encoding="utf-8")).get("posts") or [])
    import openpyxl

    sheet = openpyxl.load_workbook(path, read_only=True)["Posts"]
    rows = sheet.iter_rows(values_only=True)
    header = [str(h) for h in next(rows)]
    return [dict(zip(header, row, strict=False)) for row in rows]


def rank(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for post in posts:
        key = str(post.get("id") or "")
        if not key:
            continue
        # Keep the copy with the most slides and the highest count: a later
        # session can see a post after it gathered more interactions.
        old = unique.get(key)
        if old is None or (_int(post.get("engagement")), len(_urls(post.get("mediaUrls")))) > (
            _int(old.get("engagement")), len(_urls(old.get("mediaUrls")))
        ):
            unique[key] = post
    entries = []
    for post in unique.values():
        reactions = post.get("reactions")
        likes = _int(reactions.get("total")) if isinstance(reactions, dict) else _int(post.get("reactionsTotal"))
        comments = _int(post.get("commentCount"))
        engagement = _int(post.get("engagement")) or likes + comments
        kind = KINDS.get(str(post.get("mediaType") or ""), str(post.get("mediaType") or "other"))
        urls = _urls(post.get("mediaUrls"))
        stamp = post.get("timestampMs") or post.get("timestamp")
        if isinstance(stamp, str) and not stamp.isdigit():
            posted = stamp[:10]
            stamp_ms = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
        else:
            stamp_ms = _int(stamp)
            posted = datetime.fromtimestamp(stamp_ms / 1000, UTC).date().isoformat() if stamp_ms else None
        entries.append({
            "postId": str(post.get("externalId") or key.rpartition(":")[2]),
            "account": str(post.get("sourceId") or post.get("authorId") or "").rpartition(":")[2],
            "url": post.get("url"),
            "postedAt": posted,
            "postedAtMs": stamp_ms,
            "kind": kind,
            "slideCount": len(urls),
            "engagement": engagement,
            "likes": likes,
            "comments": comments,
            "views": _int(post.get("viewCount")) or None,
            "hasCaption": bool(str(post.get("text") or "").strip()),
            "hashtagCount": len([h for h in str(post.get("hashtags") or "").replace(",", " ").split() if h]),
            "_urls": urls,
            "_text": str(post.get("text") or "")[:600],
        })
    entries.sort(key=lambda e: e["engagement"], reverse=True)
    by_account: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_account.setdefault(entry["account"], []).append(entry)
    for group in by_account.values():
        # Already highest first: position 0 is the account's 100th percentile.
        for position, entry in enumerate(group):
            entry["accountPercentile"] = round(100 * (1 - position / max(len(group), 1)), 1)
    for position, entry in enumerate(entries, 1):
        entry["rank"] = position
    return entries


def cmd_rank(args: argparse.Namespace) -> int:
    entries = rank(read_posts(args.export))
    out = batch_dir(args.batch)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ranked.json").write_text(
        json.dumps({"export": str(args.export), "rankedAt": datetime.now(UTC).isoformat(),
                    "posts": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    accounts: dict[str, int] = {}
    for entry in entries:
        accounts[entry["account"]] = accounts.get(entry["account"], 0) + 1
    print(f"{len(entries)} posts ranked into {out / 'ranked.json'}; per account: {accounts}")
    return 0


def _ranked(batch: str) -> list[dict[str, Any]]:
    return json.loads((batch_dir(batch) / "ranked.json").read_text(encoding="utf-8"))["posts"]


def _fetch_one(job: tuple[str, Path]) -> str:
    url, target = job
    if target.exists() and target.stat().st_size > 0:
        return "skip"
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(request, timeout=60).read()
            target.write_bytes(data)
            return "ok"
        except Exception:  # noqa: BLE001 - a dead link is reported, not fatal
            time.sleep(2 * (attempt + 1))
    return "fail"


def cmd_fetch(args: argparse.Namespace) -> int:
    media = batch_dir(args.batch) / "media"
    jobs = []
    for entry in _ranked(args.batch)[: args.limit]:
        folder = media / entry["postId"]
        folder.mkdir(parents=True, exist_ok=True)
        jobs += [(url, folder / f"{n:02d}.jpg") for n, url in enumerate(entry["_urls"], 1)]
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, outcome in enumerate(pool.map(_fetch_one, jobs), 1):
            counts[outcome] = counts.get(outcome, 0) + 1
            if done % 250 == 0:
                print(f"{done}/{len(jobs)} {counts}", flush=True)
    print(f"done {len(jobs)} slides {counts}")
    return 0 if not counts.get("fail") else 2


def _thumb(path: Path, height: int):
    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        width = max(1, round(image.width * height / image.height))
        return image.resize((width, height))


def cmd_sheets(args: argparse.Namespace) -> int:
    from PIL import Image, ImageDraw

    root = batch_dir(args.batch)
    posts_dir = root / "sheets" / "posts"
    grid_dir = root / "sheets" / "grids"
    posts_dir.mkdir(parents=True, exist_ok=True)
    grid_dir.mkdir(parents=True, exist_ok=True)
    entries = _ranked(args.batch)
    made = 0
    for entry in entries:
        target = posts_dir / f"{entry['rank']:04d}_{entry['postId']}.jpg"
        slides = sorted((root / "media" / entry["postId"]).glob("*.jpg"))
        if target.exists() or not slides:
            continue
        thumbs = []
        for slide in slides:
            try:
                thumbs.append(_thumb(slide, args.height))
            except OSError:
                continue
        if not thumbs:
            continue
        per_row = 5
        rows = [thumbs[i:i + per_row] for i in range(0, len(thumbs), per_row)]
        width = max(sum(t.width for t in row) + 6 * (len(row) - 1) for row in rows)
        sheet = Image.new("RGB", (width, len(rows) * (args.height + 6)), "white")
        draw = ImageDraw.Draw(sheet)
        number = 1
        for r, row in enumerate(rows):
            x = 0
            for thumb in row:
                y = r * (args.height + 6)
                sheet.paste(thumb, (x, y))
                draw.rectangle((x, y, x + 22, y + 16), fill="black")
                draw.text((x + 4, y + 2), str(number), fill="white")
                x += thumb.width + 6
                number += 1
        sheet.save(target, quality=82)
        made += 1
    # Profile grid: the first slide of each post, posting order, three across.
    # The tile is a 3:4 centre crop, the shape a profile grid shows today.
    accounts: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        accounts.setdefault(entry["account"], []).append(entry)
    tile_w, tile_h, rows_per_sheet = 150, 200, 8
    for account, group in accounts.items():
        group.sort(key=lambda e: e["postedAtMs"] or 0, reverse=True)
        for start in range(0, len(group), 3 * rows_per_sheet):
            chunk = group[start:start + 3 * rows_per_sheet]
            sheet = Image.new("RGB", (3 * tile_w + 4, ((len(chunk) + 2) // 3) * (tile_h + 2)), "white")
            draw = ImageDraw.Draw(sheet)
            for i, entry in enumerate(chunk):
                first = root / "media" / entry["postId"] / "01.jpg"
                x, y = (i % 3) * (tile_w + 2), (i // 3) * (tile_h + 2)
                if first.exists():
                    try:
                        from PIL import ImageOps

                        with Image.open(first) as image:
                            sheet.paste(ImageOps.fit(image.convert("RGB"), (tile_w, tile_h)), (x, y))
                    except OSError:
                        pass
                draw.rectangle((x, y, x + 40, y + 14), fill="black")
                draw.text((x + 3, y + 2), f"#{entry['rank']}", fill="white")
            sheet.save(grid_dir / f"{account}_{start // (3 * rows_per_sheet) + 1:03d}.jpg", quality=80)
    print(f"{made} post sheets; profile grids for {len(accounts)} accounts in {grid_dir}")
    return 0


def cmd_overview(args: argparse.Namespace) -> int:
    """Stack the sheets of a rank range into pages of a few posts each, so a
    reviewer reads several posts per picture. Each row is labelled with its
    rank, account, kind, slide count and engagement."""
    from PIL import Image, ImageDraw

    root = batch_dir(args.batch)
    entries = {e["rank"]: e for e in _ranked(args.batch)}
    out = root / "sheets" / "overview"
    out.mkdir(parents=True, exist_ok=True)
    width, label_h = 1400, 22
    ranks = [r for r in range(args.start, args.end + 1) if r in entries
             and (not args.account or entries[r]["account"] == args.account)][: args.top]
    for page_start in range(0, len(ranks), args.per_page):
        page = ranks[page_start:page_start + args.per_page]
        rows = []
        for rank in page:
            entry = entries[rank]
            found = list((root / "sheets" / "posts").glob(f"{rank:04d}_*.jpg"))
            label = (f"#{rank} {entry['account']} {entry['kind']} {entry['slideCount']} slides "
                     f"eng {entry['engagement']} (p{entry['accountPercentile']})")
            image = None
            if found:
                with Image.open(found[0]) as sheet:
                    scale = min(1.0, width / sheet.width, args.row_height / sheet.height)
                    image = sheet.convert("RGB").resize(
                        (max(1, int(sheet.width * scale)), max(1, int(sheet.height * scale))))
            rows.append((label, image))
        height = sum(label_h + (img.height if img else 20) + 8 for _, img in rows)
        page_image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(page_image)
        y = 0
        for label, img in rows:
            draw.rectangle((0, y, width, y + label_h - 2), fill=(30, 30, 30))
            draw.text((6, y + 4), label, fill="white")
            y += label_h
            if img:
                page_image.paste(img, (0, y))
                y += img.height + 8
            else:
                draw.text((6, y + 2), "(no pictures fetched)", fill="black")
                y += 28
        prefix = f"{args.account}_" if args.account else ""
        name = out / f"{prefix}ranks_{page[0]:04d}-{page[-1]:04d}.jpg"
        page_image.save(name, quality=80)
        print(name)
    return 0


VOCAB_PATH = Path(__file__).with_name("reference_annotation_vocab.json")
_SINGLE_FIELDS = ("contentType", "lighting", "gridRole", "textOverlay")


def _annotations(batch: str) -> dict[str, dict[str, Any]]:
    folder = batch_dir(batch) / "annotations"
    found = {}
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data if isinstance(data, list) else [data]:
            found[str(item.get("postId"))] = item
    return found


def problems(item: dict[str, Any], vocab: dict[str, Any]) -> list[str]:
    """What is wrong with one annotation against the fixed vocabulary."""
    bad = []
    for field in _SINGLE_FIELDS:
        if item.get(field) not in vocab[field]:
            bad.append(f"{field}={item.get(field)!r}")
    for mood in item.get("mood") or []:
        if mood not in vocab["mood"]:
            bad.append(f"mood={mood!r}")
    slides = item.get("slides")
    if not isinstance(slides, list) or not slides:
        bad.append("slides missing")
        slides = []
    for n, slide in enumerate(slides, 1):
        if slide.get("framing") not in vocab["framing"]:
            bad.append(f"slide {n} framing={slide.get('framing')!r}")
        if slide.get("setting") not in vocab["setting"]:
            bad.append(f"slide {n} setting={slide.get('setting')!r}")
        for pose in slide.get("pose") or []:
            if pose not in vocab["pose"]:
                bad.append(f"slide {n} pose={pose!r}")
    if not isinstance(item.get("reuse"), int) or not 1 <= item["reuse"] <= 5:
        bad.append("reuse must be 1-5")
    return bad


def cmd_validate(args: argparse.Namespace) -> int:
    vocab = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    ranked = {e["postId"]: e for e in _ranked(args.batch)}
    found = _annotations(args.batch)
    failing = 0
    for post_id, item in found.items():
        bad = problems(item, vocab) + ([] if post_id in ranked else ["postId not in ranked.json"])
        if bad:
            failing += 1
            print(f"{item.get('rank')} {post_id}: {'; '.join(bad[:6])}")
    missing = [e["rank"] for e in ranked.values() if e["postId"] not in found]
    print(f"{len(found)} annotated, {failing} with problems, {len(missing)} not annotated"
          + (f" (first: {sorted(missing)[:10]})" if missing else ""))
    return 0 if not failing and not missing else 1


def cmd_aggregate(args: argparse.Namespace) -> int:
    """Count patterns among each account's best posts, and in how many accounts.

    Only posts at or above `--percentile` within their own account count, and
    only those marked reusable for apparel: the question is what the stronger
    posts of each page have in common, not what the biggest page posts most."""
    from collections import Counter, defaultdict

    ranked = {e["postId"]: e for e in _ranked(args.batch)}
    found = _annotations(args.batch)
    chosen = [
        (ranked[p], a) for p, a in found.items()
        if p in ranked and ranked[p]["accountPercentile"] >= args.percentile
        and a.get("apparelUsable") and (a.get("reuse") or 0) >= args.min_reuse
    ]
    counts: dict[str, Counter] = defaultdict(Counter)
    accounts: dict[tuple[str, str], set[str]] = defaultdict(set)
    examples: dict[tuple[str, str], list[int]] = defaultdict(list)

    def add(facet: str, value: str, entry: dict[str, Any]) -> None:
        counts[facet][value] += 1
        accounts[(facet, value)].add(entry["account"])
        if len(examples[(facet, value)]) < 8:
            examples[(facet, value)].append(entry["rank"])

    for entry, item in chosen:
        add("contentType", item["contentType"], entry)
        add("lighting", item["lighting"], entry)
        add("gridRole", item["gridRole"], entry)
        for mood in set(item.get("mood") or []):
            add("mood", mood, entry)
        slides = item.get("slides") or []
        for value in {s.get("setting") for s in slides}:
            add("setting", value, entry)
        for value in {p for s in slides for p in (s.get("pose") or [])}:
            add("pose", value, entry)
        for value in {s.get("framing") for s in slides}:
            add("framing", value, entry)
        # The order of framings, which is the shape of a carousel.
        shape = []
        for s in slides:
            if not shape or shape[-1] != s.get("framing"):
                shape.append(s.get("framing"))
        add("sequence", " > ".join(shape[:5]), entry)
        add("outfits", "one outfit" if (item.get("outfitCount") or 1) <= 1 else "several outfits", entry)
        add("together", "products together in a frame" if item.get("togetherInFrame") else "one product per frame", entry)
    report = {
        "posts": len(chosen),
        "percentile": args.percentile,
        "facets": {
            facet: [
                {"value": value, "posts": n, "accounts": len(accounts[(facet, value)]),
                 "exampleRanks": examples[(facet, value)]}
                for value, n in counter.most_common()
            ]
            for facet, counter in counts.items()
        },
        "ideas": [
            {"rank": entry["rank"], "account": entry["account"], "idea": item.get("recipeIdea")}
            for entry, item in sorted(chosen, key=lambda pair: pair[0]["rank"])
            if item.get("recipeIdea")
        ],
    }
    out = batch_dir(args.batch) / "aggregate.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(chosen)} posts aggregated into {out}")
    for facet in ("contentType", "setting", "pose", "sequence"):
        top = report["facets"].get(facet, [])[:8]
        print(facet, [(row["value"], row["posts"], row["accounts"]) for row in top])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_rank = sub.add_parser("rank")
    p_rank.add_argument("export", type=Path)
    p_rank.add_argument("--batch", required=True)
    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--batch", required=True)
    p_fetch.add_argument("--limit", type=int, default=None)
    p_fetch.add_argument("--workers", type=int, default=4)
    p_sheets = sub.add_parser("sheets")
    p_sheets.add_argument("--batch", required=True)
    p_sheets.add_argument("--height", type=int, default=260)
    p_over = sub.add_parser("overview")
    p_over.add_argument("--batch", required=True)
    p_over.add_argument("--start", type=int, default=1)
    p_over.add_argument("--end", type=int, default=40)
    p_over.add_argument("--per-page", type=int, default=4)
    p_over.add_argument("--row-height", type=int, default=420)
    p_over.add_argument("--account", default=None, help="only this account's posts")
    p_over.add_argument("--top", type=int, default=None, help="at most this many posts")
    p_valid = sub.add_parser("validate")
    p_valid.add_argument("--batch", required=True)
    p_agg = sub.add_parser("aggregate")
    p_agg.add_argument("--batch", required=True)
    p_agg.add_argument("--percentile", type=float, default=70.0)
    p_agg.add_argument("--min-reuse", type=int, default=3)
    args = parser.parse_args(argv)
    return {"rank": cmd_rank, "fetch": cmd_fetch, "sheets": cmd_sheets,
            "overview": cmd_overview, "validate": cmd_validate,
            "aggregate": cmd_aggregate}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

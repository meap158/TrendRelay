"""Read-only PM-355 audit against the running API and saved ShopPilot catalogue.

The local build receipts identify this round. Files, rather than the primary
asset reference plus files, are counted to avoid counting covers twice.
"""

import argparse
import json
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8011/api/workspaces/ws_03c59534908647d892d8e0dab62780e8")
    parser.add_argument("--campaign", default="campaign_97570f2a4ddc4d9392321758ed6cf56a")
    parser.add_argument("--round-dir", type=Path, default=Path(".data/mm-round"))
    parser.add_argument("--catalog", type=Path, default=Path("S:/Vibe Marketing/ShopPilot/runs/products/catalog.json"))
    args = parser.parse_args()

    def get(path):
        with urllib.request.urlopen(args.api + path, timeout=60) as response:
            return json.load(response)

    built = read(args.round_dir / "built.json")
    selected = {row["key"]: row for row in read(args.round_dir / "selection.json")}
    catalogue = {str(row["id"]): row for row in read(args.catalog)["products"]}
    products = get("/attribution/products")["products"]
    by_offer = {offer["id"]: row for row in products for offer in row.get("offers", [])}
    tags = get("/attribution/campaign-tags")["by_offer"]
    live = get(f"/campaigns/{args.campaign}/autopilot")
    queue = {row["id"]: row for row in live["queue"]}
    assets, sources = {}, {}
    rows, errors = [], []
    for key, receipt in built.items():
        item = queue.get(receipt["item_id"])
        if item is None:
            errors.append(f"{key}: missing queue item")
            continue
        choice = selected[key]
        paths = item["image_paths"] or ([item["video_path"]] if item["video_path"] else [])
        expected_paths = []
        for asset_id in choice["assets"]:
            if asset_id not in assets:
                asset = get(f"/media/library/assets/{asset_id}")
                assets[asset_id] = asset.get("asset", asset)
            asset = assets[asset_id]
            expected_paths.append(asset.get("path") or asset.get("file_path") or asset.get("original_path"))
        if not all(expected_paths):
            errors.append(f"{key}: cannot resolve every original Library path")
        elif expected_paths != paths:
            errors.append(f"{key}: Library file order differs from queue")
        if len(paths) != len(choice["assets"]):
            errors.append(f"{key}: media count differs from build receipt")
        for draft_id in choice["drafts"]:
            if draft_id not in sources:
                source = get(f"/attribution/creative-drafts/{draft_id}")
                sources[draft_id] = source.get("draft", source)
            draft = sources[draft_id]
            source_ids = draft.get("ingested_asset_ids", [])
            if not source_ids:
                errors.append(f"{key}: source {draft_id} has no ingested assets")
            if draft.get("card_count") and len(source_ids) < draft["card_count"]:
                errors.append(f"{key}: source {draft_id} is incomplete")
            if source_ids and not set(source_ids).issubset(choice["assets"]):
                errors.append(f"{key}: source carousel {draft_id} was split")
            if draft.get("files_owed", 0):
                errors.append(f"{key}: source {draft_id} still owes files")
        stocks = []
        for offer_id in item["offer_ids"]:
            product = by_offer.get(offer_id, {})
            item_id = None
            for value in (product.get("identifier"), product.get("product_url")):
                match = re.search(r"(\d{9,})(?:\?.*)?$", value or "")
                if match:
                    item_id = match.group(1)
                    break
            stock = catalogue.get(item_id, {}).get("stock", {}).get("total")
            stocks.append({"offer_id": offer_id, "stock": stock, "name": product.get("name")})
            if args.campaign not in tags.get(offer_id, []):
                errors.append(f"{key}: offer outside campaign: {offer_id}")
        known_stock = [p["stock"] for p in stocks if p["stock"] is not None]
        rows.append({"key": key, "item_id": item["id"], "position": item["position"],
                     "state": item["state"], "pinned_slot": item["pinned_slot"],
                     "shape": "video" if item["video_path"] else "carousel" if len(paths) > 1 else "image",
                     "recipe": choice["recipe"], "product_count": len(item["offer_ids"]),
                     "lead_stock": max(known_stock) if known_stock else None,
                     "products": stocks, "paths": paths, "caption": item["body"],
                     "ha_my": choice.get("character") is not None})
    rows.sort(key=lambda r: (r["pinned_slot"] or "9999", r["position"]))
    superseded = []
    original_receipts = args.round_dir / "original-built.json"
    if original_receipts.exists():
        active_ids = {r["item_id"] for r in rows}
        for receipt in read(original_receipts).values():
            if receipt["item_id"] not in active_ids:
                item = queue.get(receipt["item_id"])
                if item is None or item["state"] != "retired" or item["pinned_slot"]:
                    errors.append(f"Superseded post is not retired and unpinned: {receipt['item_id']}")
                superseded.append(receipt["item_id"])
    files = Counter(path.casefold() for row in rows for path in row["paths"])
    duplicates = {path: count for path, count in files.items() if count > 1}
    if duplicates:
        errors.append("Media files reused within round")
    original_files = set()
    baseline = args.round_dir / "before-regroup.json"
    if baseline.exists() and original_receipts.exists():
        original_ids = {r["item_id"] for r in read(original_receipts).values()}
        original_files = {path.casefold() for item in read(baseline)["queue"]
                          if item["id"] in original_ids
                          for path in item["image_paths"] + [item["video_path"]] if path}
        if original_files - files.keys():
            errors.append("Original round media omitted from the active regrouped round")
    captions = Counter(row["caption"] for row in rows)
    if any(count > 1 for count in captions.values()):
        errors.append("Duplicate captions")
    adjacent = {field: sum(a[field] == b[field] for a, b in zip(rows, rows[1:]))
                for field in ("shape", "recipe", "product_count")}
    inversions = [[a["key"], b["key"]] for a, b in zip(rows, rows[1:])
                  if a["lead_stock"] is not None and b["lead_stock"] is not None
                  and a["lead_stock"] < b["lead_stock"]]
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "campaign": args.campaign,
              "authority": live["autopilot"]["authority"], "posts": len(rows),
              "states": dict(Counter(r["state"] for r in rows)),
              "formats": dict(Counter(r["shape"] for r in rows)),
              "media_files": sum(files.values()), "unique_media_files": len(files),
              "duplicate_files": duplicates, "pinned": sum(bool(r["pinned_slot"]) for r in rows),
              "adjacent_equal": adjacent, "stock_inversions": inversions,
              "source_drafts_checked": len(sources), "errors": errors, "rows": rows}
    report["superseded_retired_posts"] = len(superseded)
    report["original_files_preserved"] = len(original_files & files.keys())
    report["recipe_basis"] = "Editorial post recipe; merged comparisons retain their source draft recipes in the manifest."
    report["acceptance"] = {
        "media_integrity": not errors,
        "stock_order": not inversions and all(r["lead_stock"] is not None for r in rows),
        "all_pinned": all(r["pinned_slot"] for r in rows),
        "all_drafts": all(r["state"] == "draft" for r in rows),
        "strict_format_alternation": adjacent["shape"] == 0,
        "strict_recipe_alternation": adjacent["recipe"] == 0,
        "strict_product_count_alternation": adjacent["product_count"] == 0,
    }
    args.round_dir.mkdir(parents=True, exist_ok=True)
    target = args.round_dir / "acceptance-audit.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    print(f"Full evidence: {target}")
    return not all(report["acceptance"].values())


if __name__ == "__main__":
    raise SystemExit(main())

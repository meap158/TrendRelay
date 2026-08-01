"""Isolated JSON bridge for the pinned Meta Ads Collector runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / ".tools" / "catalog" / "meta-ads-collector" / "source"
sys.path.insert(0, str(SOURCE_ROOT))

from meta_ads_collector import FilterConfig, MetaAdsCollector  # noqa: E402

AD_TYPES = {
    "all": "ALL",
    "political": "POLITICAL_AND_ISSUE_ADS",
    "housing": "HOUSING_ADS",
    "employment": "EMPLOYMENT_ADS",
    "credit": "CREDIT_ADS",
}
SEARCH_TYPES = {
    "keyword": "KEYWORD_UNORDERED",
    "exact": "KEYWORD_EXACT_PHRASE",
    "page": "PAGE",
}


def main() -> int:
    request: dict[str, Any] = json.load(sys.stdin)
    filters = FilterConfig(
        min_impressions=request.get("min_impressions"),
        min_spend=request.get("min_spend"),
        media_type=request["media_type"].upper(),
        publisher_platforms=request.get("publisher_platforms") or None,
    )
    with MetaAdsCollector(rate_limit_delay=2.0, jitter=0.75, timeout=30) as collector:
        ads = [
            ad.to_dict()
            for ad in collector.search(
                query=request["query"],
                country=request["country"],
                ad_type=AD_TYPES[request["ad_type"]],
                status=request["status"].upper(),
                search_type=SEARCH_TYPES[request["search_type"]],
                page_ids=[request["page_id"]] if request.get("page_id") else None,
                sort_by=(
                    "SORT_BY_TOTAL_IMPRESSIONS"
                    if request["sort_by"] == "impressions"
                    else None
                ),
                max_results=request["max_results"],
                page_size=min(request["max_results"], 30),
                filter_config=filters,
            )
        ]
        output = {"ads": ads, "stats": collector.get_stats()}
    print(json.dumps(output, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

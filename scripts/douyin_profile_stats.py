"""Report each Douyin profile's declared post total, without downloading.

Runs inside the provider's own venv, because it borrows the provider's signed
API client. For every URL it resolves short links, and for profile URLs asks
`get_user_info` - which answers a signed-out session - for the author's
`aweme_count`: the number the profile page prints as 作品. That declared total
is what lets a download job say "41 of 882" instead of leaving coverage to be
guessed from a count.

Output is one JSON array on stdout; every URL gets an entry, profile or not,
so the caller can zip results back to its sources without bookkeeping.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def collect(cookie_path: Path, urls: list[str]) -> list[dict[str, object]]:
    from core.api_client import DouyinAPIClient
    from core.url_parser import URLParser
    from utils.validators import is_short_url, normalize_short_url

    cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
    results: list[dict[str, object]] = []
    async with DouyinAPIClient(cookies) as client:
        for url in urls:
            entry: dict[str, object] = {"url": url, "kind": "other"}
            try:
                resolved = url
                if is_short_url(url):
                    resolved = await client.resolve_short_url(
                        normalize_short_url(url)
                    ) or url
                parsed = URLParser.parse(resolved) or {}
                if parsed.get("type") == "user" and parsed.get("sec_uid"):
                    entry["kind"] = "profile"
                    entry["sec_uid"] = parsed["sec_uid"]
                    info = await client.get_user_info(str(parsed["sec_uid"]))
                    nickname = str(info.get("nickname") or "").strip()
                    if nickname:
                        entry["nickname"] = nickname
                    total = info.get("aweme_count")
                    if isinstance(total, int) and total >= 0:
                        entry["declared_total"] = total
                elif parsed.get("type"):
                    entry["kind"] = str(parsed["type"])
            except Exception as error:  # noqa: BLE001 - one URL cannot fail the batch
                entry["error"] = str(error)[:200]
            results.append(entry)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cookies", type=Path, required=True)
    parser.add_argument("urls", nargs="+")
    args = parser.parse_args()
    results = asyncio.run(collect(args.cookies, list(args.urls)))
    json.dump(results, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

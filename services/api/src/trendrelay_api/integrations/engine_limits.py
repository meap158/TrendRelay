"""What each engine's plan allows, and how much of it is gone.

Three different confidences, kept apart on purpose, because presenting them as
one number is how a dashboard ends up lying:

``measured``
    The engine said so, just now. Buffer returns ``RateLimit`` headers on every
    GraphQL response; bundle.social answers
    ``GET /organization/usage/daily-limits`` with used/limit/remaining.

``counted``
    TrendRelay counted it from what the engine returned - connected accounts,
    for instance. Exact, but ours rather than theirs.

``published``
    Taken from the engine's own pricing page on a date. Not read from the API at
    all, and a plan can change without notice. Labelled as documentation so
    nobody reconciles a bill against it.

The distinction is the whole point. A figure that looks live and is a year-old
scrape of a marketing page is worse than no figure, because it gets believed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Confidence = Literal["measured", "counted", "published"]

#: When the published figures below were last checked against each engine's own
#: pricing page. Shown with them, so their age is visible rather than implied.
PUBLISHED_ON = "2026-08-09"


@dataclass(frozen=True)
class Allowance:
    """One limit, what is gone, and how much that figure can be trusted."""

    id: str
    label: str
    confidence: Confidence
    #: None where the plan does not cap this at all - which is a real answer,
    #: and a different one from "we do not know".
    limit: int | None
    used: int | None
    note: str = ""

    @property
    def remaining(self) -> int | None:
        if self.limit is None or self.used is None:
            return None
        return max(0, self.limit - self.used)

    @property
    def unlimited(self) -> bool:
        return self.limit is None


#: Free-plan terms, from each engine's pricing page on PUBLISHED_ON.
#:
#: Deliberately only the free tier. TrendRelay cannot read which plan an account
#: is on - none of the three expose it - so quoting paid-tier numbers beside a
#: free-tier account would be guessing at which row applies.
FREE_PLAN: dict[str, dict[str, Any]] = {
    "bundle_social": {
        "plan": "Free",
        "accounts": 3,
        "posts_per_month": 20,
        "comments_per_month": 50,
        "source": "https://bundle.social/pricing",
    },
    "zernio": {
        "plan": "Free (first 2 accounts)",
        "accounts": 2,
        # Genuinely uncapped on Zernio: they charge per connected account and
        # every account, free or paid, posts without limit.
        "posts_per_month": None,
        "comments_per_month": None,
        "source": "https://zernio.com/pricing",
    },
    "buffer": {
        "plan": "Free",
        "accounts": 3,
        #: Not a monthly cap. Buffer holds ten *queued* posts per channel at a
        #: time; publishing one frees its slot. Recorded as a queue depth so it
        #: is never added up as a monthly allowance.
        "queued_per_channel": 10,
        "requests_per_30_days": 3_000,
        "source": "https://buffer.com/pricing",
    },
}

#: `RateLimit: limit=100, remaining=99, reset=60` - the header Buffer returns on
#: every GraphQL response, in the IETF draft format.
_RATE_LIMIT = re.compile(r"(\w+)\s*=\s*(\d+)")


def parse_rate_limit(header: str | None) -> dict[str, int]:
    """Pull limit/remaining/reset out of a `RateLimit` header.

    Tolerant by design: an engine that changes the header's shape should cost a
    missing figure, not a failed page.
    """
    if not header:
        return {}
    return {
        name.casefold(): int(value)
        for name, value in _RATE_LIMIT.findall(header)
        if name.casefold() in {"limit", "remaining", "reset"}
    }


def allowances(
    provider_id: str,
    *,
    account_count: int,
    rate_limit: dict[str, int] | None = None,
    daily: dict[str, Any] | None = None,
) -> list[Allowance]:
    """Everything worth showing for one engine, most actionable first."""
    plan = FREE_PLAN.get(provider_id)
    if not plan:
        return []
    found: list[Allowance] = []

    # Counted: how many accounts are connected against what the free plan allows.
    found.append(Allowance(
        id="accounts",
        label="Connected accounts",
        confidence="counted",
        limit=plan.get("accounts"),
        used=account_count,
        note=f"Connected accounts counted here; the cap is the {plan['plan']} plan's.",
    ))

    # Measured: Buffer tells us its request budget on every call.
    if rate_limit and "limit" in rate_limit:
        used = rate_limit["limit"] - rate_limit.get("remaining", rate_limit["limit"])
        found.append(Allowance(
            id="requests",
            label="API requests in this window",
            confidence="measured",
            limit=rate_limit["limit"],
            used=used,
            note="Reported by the engine on its last response.",
        ))

    # Measured: bundle.social's own daily counter, per account.
    if daily:
        for kind in ("posts", "comments"):
            figures = daily.get(kind) or {}
            if "limit" not in figures:
                continue
            found.append(Allowance(
                id=f"daily_{kind}",
                label=f"{kind.title()} today",
                confidence="measured",
                limit=int(figures["limit"]),
                used=int(figures.get("used", 0)),
                note="Reported by the engine for this account, resets daily.",
            ))

    # Published: what the plan says, where the API says nothing.
    if plan.get("posts_per_month") is not None:
        found.append(Allowance(
            id="posts_per_month",
            label="Posts per month",
            confidence="published",
            limit=int(plan["posts_per_month"]),
            used=None,
            note=f"From {plan['source']}, checked {PUBLISHED_ON}. Not read from the engine.",
        ))
    elif "posts_per_month" in plan:
        found.append(Allowance(
            id="posts_per_month",
            label="Posts per month",
            confidence="published",
            limit=None,
            used=None,
            note=f"Uncapped on the {plan['plan']} plan. From {plan['source']}, "
                 f"checked {PUBLISHED_ON}.",
        ))
    if plan.get("queued_per_channel"):
        found.append(Allowance(
            id="queued_per_channel",
            label="Scheduled posts held per channel",
            confidence="published",
            limit=int(plan["queued_per_channel"]),
            used=None,
            note="A queue depth, not a monthly allowance: publishing one frees "
                 f"its slot. From {plan['source']}, checked {PUBLISHED_ON}.",
        ))
    if plan.get("requests_per_30_days") and not rate_limit:
        found.append(Allowance(
            id="requests_per_30_days",
            label="API requests per 30 days",
            confidence="published",
            limit=int(plan["requests_per_30_days"]),
            used=None,
            note=f"From {plan['source']}, checked {PUBLISHED_ON}. Not read from the engine.",
        ))
    return found


def payload(item: Allowance) -> dict[str, Any]:
    return {
        "id": item.id,
        "label": item.label,
        "confidence": item.confidence,
        "limit": item.limit,
        "used": item.used,
        "remaining": item.remaining,
        "unlimited": item.unlimited,
        "note": item.note,
    }

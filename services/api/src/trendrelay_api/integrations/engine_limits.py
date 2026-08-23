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
PUBLISHED_ON = "2026-08-24"


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
#: Deliberately only the free tier. No engine names the plan an account is on,
#: so these are what applies until `infer_plan` finds a reported limit that says
#: otherwise - and quoting paid-tier numbers beside an account we have not
#: placed on a paid tier would be guessing at which row applies.
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
    "woopsocial": {
        "plan": "Free",
        "accounts": 2,
        # Genuinely uncapped: every plan advertises unlimited posts, and the
        # monthly credits meter AI content generation rather than publishing.
        # TrendRelay writes its own captions, so it never spends one - and
        # recording credits as a posting allowance would eventually grey out
        # destinations that are in fact free to post.
        "posts_per_month": None,
        "comments_per_month": None,
        "ai_credits_per_month": 30,
        "source": "https://woopsocial.com/pricing",
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

#: `RateLimit-Policy: 100;w=900, 250;w=86400, 3000;w=2592000` - the same draft's
#: companion header, listing every window rather than just the one about to bite.
_RATE_LIMIT_POLICY = re.compile(r"(\d+)\s*;\s*w\s*=\s*(\d+)")

#: Thirty days, in seconds: the window whose quota differs between Buffer plans.
_THIRTY_DAYS = 30 * 24 * 60 * 60


def parse_rate_limit_policy(header: str | None) -> dict[int, int]:
    """Quota per window, keyed by the window's length in seconds."""
    if not header:
        return {}
    return {
        int(window): int(quota)
        for quota, window in _RATE_LIMIT_POLICY.findall(header)
    }


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


#: What a 30-day request quota says about a Buffer plan. Buffer publishes a
#: different figure per tier and returns the one in force on every response, so
#: the quota identifies the tier even though no endpoint will name it.
BUFFER_TIER_BY_REQUESTS: dict[int, str] = {
    3_000: "Free",
    7_500: "Essentials",
    15_000: "Team",
}

#: bundle.social's daily post cap separates its tiers the same way. Only the
#: free figure is published as a hard number; the paid tiers are higher without
#: being individually documented, which is enough to rule Free out but not
#: enough to name the tier.
BUNDLE_FREE_DAILY_POSTS = 20

#: How to name an engine in a sentence about its own pricing.
PLAN_LABELS: dict[str, str] = {"zernio": "Zernio", "woopsocial": "WoopSocial"}


@dataclass(frozen=True)
class PlanTier:
    """One row of an engine's published pricing.

    Strings, not numbers, because these are quotations. "Unlimited", "3-10" and
    "$6 per account" are the published answers, and coercing them into integers
    would mean inventing a reading of them that the engine did not give.
    """

    name: str
    price: str
    accounts: str
    posts: str
    note: str = ""


#: What each engine sells, from its own pricing page on `PUBLISHED_ON`.
#:
#: `FREE_PLAN` above is what an account is assumed to have until the engine
#: reports otherwise; this is the whole ladder, and it is reference material
#: rather than a claim about anybody's account. That distinction is why quoting
#: paid tiers is safe here and is not safe there: nothing below is presented as
#: the plan in force, so there is no row to pick wrongly.
#:
#: Never read from an API. A pricing page can change the day after it is read,
#: so every figure here is shown with the date it was checked and a link to the
#: page it came from - which is the only honest way to show a number nobody can
#: verify from inside the app.
PLAN_LADDER: dict[str, tuple[PlanTier, ...]] = {
    "bundle_social": (
        PlanTier("Free", "$0 / month", "3", "20 / month", "50 comments a month"),
        PlanTier("Pro", "$100 / month", "Unlimited", "10,000 / month", "5,000 comments a month"),
        PlanTier(
            "Business", "$400 / month", "Unlimited", "100,000 / month",
            "50,000 comments a month",
        ),
        PlanTier("Custom", "Priced on request", "Unlimited", "Negotiated", "Enterprise terms"),
    ),
    "zernio": (
        PlanTier(
            "Free", "$0", "First 2", "Unlimited",
            "Every feature is on every account; nothing is gated behind a tier",
        ),
        PlanTier("3 to 10 accounts", "$6 per account / month", "3-10", "Unlimited"),
        PlanTier("11 to 100 accounts", "$3 per account / month", "11-100", "Unlimited"),
        PlanTier("101 and above", "$1 per account / month", "101+", "Unlimited", "No cap above"),
    ),
    "woopsocial": (
        PlanTier("Free", "$0 / month", "2", "Unlimited", "30 AI credits, 1 GB, 1 seat"),
        PlanTier("Pro", "$19 / month", "20", "Unlimited", "1,000 AI credits, 10 GB, 1 seat"),
        PlanTier("Business", "$49 / month", "100", "Unlimited", "5,000 AI credits, 25 GB, 3 seats"),
        PlanTier(
            "Max", "$799 / month", "2,500", "Unlimited",
            "42,000 AI credits, 420 GB, 43 seats",
        ),
    ),
    "buffer": (
        PlanTier(
            "Free", "$0 / month", "3", "10 queued per channel",
            "One user; 30 days of analytics history",
        ),
        PlanTier(
            "Essentials", "$5 per channel / month", "Priced per channel", "Unlimited queued",
            "Adds first comments, advanced analytics, hashtag manager",
        ),
        PlanTier(
            "Team", "$10 per channel / month", "Priced per channel", "Unlimited queued",
            "Adds approvals and unlimited team members",
        ),
    ),
}

#: What the ladder above means for publishing through TrendRelay specifically.
#:
#: A pricing page sells the whole product and most of it is not this. Somebody
#: comparing tiers here is deciding whether a plan will let them post, so the
#: line that decides that is worth more than the fourteen features beside it -
#: and an allowance that sounds binding but is not, like WoopSocial's credits,
#: is worth naming before it is mistaken for a posting cap.
PLAN_CAVEATS: dict[str, str] = {
    "bundle_social": (
        "Posts and comments are the caps that bite. X is billed separately per "
        "post on top of the plan, at $0.015, or $0.20 with a link in it."
    ),
    "zernio": (
        "Charged per connected account, never per post. Two accounts cost "
        "nothing and the third is what starts a bill. Graduated, not banded: "
        "each rate applies only to the accounts inside its range, so twelve "
        "accounts are eight at $6 and two at $3 rather than twelve at $3."
    ),
    "woopsocial": (
        "Credits meter AI content generation, not publishing. TrendRelay writes "
        "its own captions, so the free tier's thirty are never spent and the "
        "account limit is the only one that applies."
    ),
    "buffer": (
        "Ten is a queue depth, not a monthly allowance: publishing a post frees "
        "its slot. First comments need Essentials or above."
    ),
}


def plan_ladder_payload(provider_id: str) -> dict[str, Any]:
    """An engine's published tiers, dated and sourced."""
    tiers = PLAN_LADDER.get(provider_id, ())
    free = FREE_PLAN.get(provider_id, {})
    return {
        "checked_on": PUBLISHED_ON,
        "source": free.get("source"),
        "caveat": PLAN_CAVEATS.get(provider_id, ""),
        "tiers": [
            {
                "name": tier.name,
                "price": tier.price,
                "accounts": tier.accounts,
                "posts": tier.posts,
                "note": tier.note,
                # The tier an account is on until the engine says otherwise, so
                # the row somebody is probably reading from is marked as such.
                "free": tier.name.casefold().startswith("free"),
            }
            for tier in tiers
        ],
    }


@dataclass(frozen=True)
class Plan:
    """Which plan an account is on, and how that was arrived at.

    ``name`` is None when nothing observed distinguishes one plan from another.
    That is a real answer and a different one from "Free": someone deciding
    whether to upgrade is worse off with a confident wrong tier than with none.
    """

    name: str | None
    confidence: Confidence
    note: str


#: Features an engine sells rather than includes, by the plan that has to be
#: bought for them. Buffer's free tier answered "First comment requires a paid
#: plan. Please upgrade to use this feature." after a post had already been
#: built and sent, which is the worst moment to learn it.
PAID_ONLY_FEATURES: dict[str, frozenset[str]] = {
    "buffer": frozenset({"first_comment"}),
}

#: The plan names that get nothing extra. Anything else is a paid tier.
FREE_PLAN_NAMES = frozenset({"Free"})


def feature_available(provider_id: str, feature: str, plan: Plan) -> bool:
    """Whether this account's plan includes a feature the engine sells.

    Unknown counts as available. A plan nothing observed could name is a real
    answer, and hiding a feature somebody is paying for - because a header was
    missing - is a worse failure than offering one they have to upgrade for.
    """
    if feature not in PAID_ONLY_FEATURES.get(provider_id, frozenset()):
        return True
    return plan.name not in FREE_PLAN_NAMES


def infer_plan(
    provider_id: str,
    *,
    policy: dict[int, int] | None = None,
    daily: dict[str, Any] | None = None,
    account_count: int = 0,
) -> Plan:
    """The account's plan, read off the limits the engine does report.

    None of the three engines expose a plan name - there is no endpoint to ask,
    and no field on any response that carries one. What they do report are the
    limits in force, and those differ per tier, so the limit identifies the tier
    by elimination. A quota matching exactly one published figure names that
    tier; anything else is left unnamed rather than rounded to the nearest one.
    """
    plan = FREE_PLAN.get(provider_id)
    assumed = plan["plan"] if plan else "free"

    if provider_id == "buffer":
        # The 30-day window specifically. The 15-minute one is 100 on every
        # tier, so reading the header that happens to be nearest expiry would
        # call a Team account Free.
        quota = (policy or {}).get(_THIRTY_DAYS)
        named = BUFFER_TIER_BY_REQUESTS.get(quota) if quota else None
        if named:
            return Plan(named, "measured", (
                f"{named}. Identified by the {quota:,} requests per 30 days the "
                "engine reports; Buffer has no endpoint that names the plan."
            ))
    elif provider_id == "bundle_social":
        posts = (daily or {}).get("posts") or {}
        limit = posts.get("limit")
        if limit is not None:
            limit = int(limit)
            if limit == BUNDLE_FREE_DAILY_POSTS:
                return Plan("Free", "measured", (
                    f"Free. Identified by the {limit} posts a day the engine "
                    "reports, which is the free plan's cap."
                ))
            return Plan("Paid", "measured", (
                f"Paid. The engine reports {limit} posts a day, above the free "
                "plan's cap. Which paid tier is not something it says."
            ))
    elif provider_id in {"zernio", "woopsocial"}:
        # Both sell connected accounts rather than posts, and both give the
        # first two away, so the count is the answer here rather than evidence
        # towards one.
        allowed = int((plan or {}).get("accounts") or 2)
        paid = account_count > allowed
        label = PLAN_LABELS[provider_id]
        return Plan("Paid" if paid else "Free", "counted", (
            f"{'Paid' if paid else 'Free'}. {label} charges per connected "
            f"account and the first {allowed} are free; {account_count} connected."
        ))

    return Plan(None, "published", (
        f"Assuming the {assumed} plan. This engine does not report which plan "
        "an account is on, and nothing it has reported distinguishes one."
    ))


def plan_payload(plan: Plan) -> dict[str, Any]:
    return {"name": plan.name, "confidence": plan.confidence, "note": plan.note}


def allowances(
    provider_id: str,
    *,
    account_count: int,
    rate_limit: dict[str, int] | None = None,
    policy: dict[int, int] | None = None,
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
    if plan.get("ai_credits_per_month"):
        # Shown precisely because it looks like a posting allowance and is not.
        # Someone reading "30 credits" beside an engine reasonably assumes 30
        # posts; saying what they are actually spent on is the only way that
        # figure stops being misleading. It can never block a post either - it
        # is published, and `exhausted` ignores anything without measured usage.
        found.append(Allowance(
            id="ai_credits",
            label="AI credits per month",
            confidence="published",
            limit=int(plan["ai_credits_per_month"]),
            used=None,
            note="Spent on this engine's own content generation, not on "
                 "publishing. TrendRelay writes its own captions, so posting "
                 f"never uses one. From {plan['source']}, checked {PUBLISHED_ON}.",
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
    if plan.get("requests_per_30_days"):
        # The engine's own figure where it sent one. Its rate-limit policy lists
        # a quota per window, and the 30-day window is this same budget - so
        # quoting the pricing page beside it would be a scraped number sitting
        # where a reported one was available. No usage with it: the policy says
        # what the window allows, and only the window nearest expiry reports
        # what is left of it.
        measured = (policy or {}).get(_THIRTY_DAYS)
        found.append(Allowance(
            id="requests_per_30_days",
            label="API requests per 30 days",
            confidence="measured" if measured else "published",
            limit=int(measured or plan["requests_per_30_days"]),
            used=None,
            note="Reported by the engine in its rate-limit policy." if measured
                 else f"From {plan['source']}, checked {PUBLISHED_ON}. "
                      "Not read from the engine.",
        ))
    return found


#: Allowances whose exhaustion stops a post going out.
#:
#: Deliberately not "accounts". A workspace at 3 of 3 connected accounts has
#: used up its room for *more* accounts, not its room to post - blocking on it
#: would switch off every destination at exactly the moment the plan is fully
#: in use. Nor "comments", which stops a first comment rather than a post.
BLOCKING_ALLOWANCES: tuple[str, ...] = ("daily_posts", "requests")


def exhausted(items: list[Allowance]) -> Allowance | None:
    """The allowance that has run out, if one has.

    Only a figure with both a ceiling and a count of what is gone can be
    exhausted, which rules out the published ones by construction: they are
    quoted from a pricing page and carry no usage. That is the right outcome -
    a scrape of a marketing page must never be what stops a publish.
    """
    for item in items:
        if item.id not in BLOCKING_ALLOWANCES or item.confidence == "published":
            continue
        if item.limit is not None and item.used is not None and item.remaining == 0:
            return item
    return None


def spent_note(item: Allowance) -> str:
    """How much went against the quota, which is the part worth reading.

    "Out of quota" alone gives no way to judge whether to wait an hour or fix
    something, so the sentence carries the numbers and, where the engine said
    so, when they come back.
    """
    sentence = f"{item.label}: {item.used} of {item.limit} used."
    return f"{sentence} Resets daily." if "resets daily" in item.note.lower() else sentence


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

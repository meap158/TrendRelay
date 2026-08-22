"""Which connected accounts fit a campaign, with reasons a person can check.

The recommendation is explainable or it is not offered. Every account carries
the sentences that argue for or against it - the link policy of its network,
what has actually been measured through it, whether its engine can deliver at
all - and a confidence that follows the app's standing rule: a figure is
measured or it is not claimed. There is no invented audience-fit score here,
because no engine reports the data that would ground one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.autopilot_models import CampaignAutopilot, CampaignDestination
from trendrelay_api.campaign_autopilot import resolve_placement
from trendrelay_api.campaign_scheduler import _performance

#: Below this many settled conversions a destination is not ranked, only
#: explored - the same threshold the scheduler's ranking refuses to print a
#: figure under.
RANKABLE_CONVERSIONS = 5


def _account_history(
    session: Session, workspace_id: str
) -> dict[tuple[str, str], dict[str, float]]:
    """What has been measured through each account, across every campaign.

    Keyed by (provider, integration_id), because the same account added to two
    campaigns is one audience - and a new campaign deserves to know what the
    account did for the others.
    """
    destinations = session.scalars(
        select(CampaignDestination).where(
            CampaignDestination.workspace_id == workspace_id
        )
    ).all()
    if not destinations:
        return {}
    measured = _performance(session, workspace_id, list(destinations))
    combined: dict[tuple[str, str], dict[str, float]] = {}
    for destination in destinations:
        key = (destination.provider, destination.integration_id)
        totals = combined.setdefault(
            key, {"clicks": 0.0, "conversions": 0.0, "net_commission_cents": 0.0}
        )
        for field, value in (measured.get(destination.id) or {}).items():
            totals[field] += value
    return combined


def recommend_accounts(
    session: Session,
    autopilot: CampaignAutopilot,
    *,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Every reachable account, argued for or against.

    `inventory` is the engines' own answer (`discover_all_integrations`),
    passed in rather than fetched so the reasoning stays testable without a
    network and the caller keeps the external call behind its confirmation.
    """
    destinations = session.scalars(select(CampaignDestination).where(
        CampaignDestination.campaign_id == autopilot.campaign_id
    )).all()
    existing = {(item.provider, item.integration_id): item for item in destinations}
    history = _account_history(session, autopilot.workspace_id)
    commerce = autopilot.offer_mode != "none"

    recommendations: list[dict[str, Any]] = []
    for account in inventory.get("accounts", []):
        key = (account.get("provider"), account.get("id"))
        reasons: list[str] = []
        available = bool(account.get("available", True))
        if not available:
            reasons.append(
                account.get("unavailable_reason")
                or "This account's engine cannot deliver right now."
            )

        placement = resolve_placement(account.get("platform", ""))
        if commerce and available:
            if placement.placement == "bio":
                reasons.append(
                    f"No clickable post link on {account.get('platform')}: captions "
                    "will point at the profile's bio link, which must carry the "
                    "campaign's tracking link."
                )
            else:
                reasons.append("Affiliate links are clickable in the post here.")

        measured = history.get(key)
        clicks = int(measured["clicks"]) if measured else 0
        conversions = int(measured["conversions"]) if measured else 0
        if conversions >= RANKABLE_CONVERSIONS:
            confidence = "high"
            reasons.append(
                f"Measured: {clicks} clicks and {conversions} settled conversions "
                "through this account's links."
            )
        elif clicks:
            confidence = "medium"
            reasons.append(
                f"Measured: {clicks} clicks so far; fewer than "
                f"{RANKABLE_CONVERSIONS} settled conversions, so it cannot be "
                "ranked yet."
            )
        else:
            confidence = "low"
            reasons.append(
                "No measured history through TrendRelay yet; exploration slots "
                "gather it."
            )

        from trendrelay_api.integrations.account_identity import stable_page_key

        page_key = stable_page_key(
            str(account.get("platform") or ""),
            account.get("handle"),
            str(account.get("provider") or ""),
            str(account.get("id") or ""),
        )
        # Destinations created before page schedules existed learn their stable
        # identity the next time the operator refreshes connected accounts.
        # This is the first trustworthy moment because it uses the engine's
        # current handle rather than guessing from a display label.
        if key in existing and not existing[key].page_key:
            existing[key].page_key = page_key

        recommendations.append({
            "provider": account.get("provider"),
            "provider_label": account.get("provider_label"),
            "integration_id": account.get("id"),
            "platform": account.get("platform"),
            "label": account.get("label"),
            "handle": account.get("handle"),
            "page_key": page_key,
            "available": available,
            "recommended": available,
            "already_added": key in existing,
            "link_placement": placement.placement,
            "confidence": confidence,
            "reasons": reasons,
        })

    # Strongest case first: deliverable, then measured, then by evidence size.
    recommendations.sort(key=lambda item: (
        not item["available"],
        {"high": 0, "medium": 1, "low": 2}[item["confidence"]],
        item["label"] or "",
    ))
    return {
        "accounts": recommendations,
        "engines": inventory.get("engines", []),
    }

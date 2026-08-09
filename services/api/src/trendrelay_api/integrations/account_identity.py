"""Recognising the same social page when two engines both report it.

A workspace that connects Buffer and Zernio to the same brand sees its Instagram
twice, once per engine, under two different ids. Left alone that is not just
untidy - it invites picking both and publishing the same post to the same
audience twice, which is the failure this module exists to prevent.

What can and cannot be matched
------------------------------
Engines report two names: a **handle** (``username`` / ``name``) and a **display
name** (``displayName``). Only the handle identifies the page. Display names are
chosen freely, change often, and repeat - a workspace can easily have two
accounts both called "Halcyon Books". Merging on one would silently point a post
at the wrong account, which is worse than showing a duplicate.

So: a match requires a handle on both sides, and the same platform. No handle,
no merge - the accounts stay separate and say why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Strips the decorations a handle picks up on its way through an API: a leading
#: at-sign, a profile URL, surrounding whitespace, case.
_URL = re.compile(r"^https?://[^/]+/(?:@)?", re.IGNORECASE)
_TRAILING = re.compile(r"[/?#].*$")


def normalise_handle(value: str | None) -> str | None:
    """The comparable form of a handle, or None when there is nothing to compare.

    Returning None rather than an empty string is deliberate: an empty handle
    would group every handle-less account on a platform into one, which is
    precisely the wrong merge.
    """
    if not value:
        return None
    handle = _URL.sub("", str(value).strip())
    handle = _TRAILING.sub("", handle).lstrip("@").strip()
    # A handle of one character is almost certainly a parsing accident rather
    # than a real account name.
    return handle.casefold() or None if len(handle) > 1 else None


@dataclass
class ConsolidatedPage:
    """One social page, and every engine that can reach it."""

    platform: str
    #: None when the engines gave no handle, in which case this page is one
    #: engine's account and was never a candidate for merging.
    handle: str | None
    label: str
    #: Ordered as the engines were read, so the first is the default deliverer.
    reachable_by: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity for the UI, distinct from any one engine's id."""
        if self.handle:
            return f"{self.platform}:@{self.handle}"
        first = self.reachable_by[0]
        return f"{self.platform}:{first['provider']}:{first['id']}"

    @property
    def shared(self) -> bool:
        return len(self.reachable_by) > 1


def consolidate(accounts: list[dict[str, Any]]) -> list[ConsolidatedPage]:
    """Group accounts that are the same page seen through different engines.

    Order is preserved: the engine that reported a page first stays first, and
    is what a caller should deliver through unless told otherwise. Arbitrary but
    stable beats a choice that reshuffles between reads.
    """
    pages: dict[str, ConsolidatedPage] = {}
    for account in accounts:
        platform = str(account.get("platform") or "")
        handle = normalise_handle(account.get("handle"))
        reach = {
            "provider": account.get("provider"),
            "provider_label": account.get("provider_label"),
            "id": account.get("id"),
            "label": account.get("label"),
            # Carried per route, not per page: a page reachable by two engines
            # is still reachable when one of them has run out of quota, and the
            # picker should send it through the other rather than grey it out.
            "available": account.get("available", True),
            "unavailable_reason": account.get("unavailable_reason"),
        }
        # A handle-less account is unmergeable and kept that way: its key
        # includes the engine and id, so it can never collide with another.
        key = (
            f"{platform}:@{handle}" if handle
            else f"{platform}:{reach['provider']}:{reach['id']}"
        )
        found = pages.get(key)
        if found is None:
            pages[key] = ConsolidatedPage(
                platform=platform,
                handle=handle,
                label=str(account.get("label") or handle or platform),
                reachable_by=[reach],
            )
            continue
        # Same page, another engine. A repeat of the same engine and id would be
        # the engine listing an account twice, which is not a second route.
        if any(
            item["provider"] == reach["provider"] and item["id"] == reach["id"]
            for item in found.reachable_by
        ):
            continue
        found.reachable_by.append(reach)
    return list(pages.values())


def page_payload(page: ConsolidatedPage) -> dict[str, Any]:
    """What the picker needs to show one page and post to it exactly once."""
    # The first route that can actually carry a post. An engine out of quota is
    # still listed - which engines reach a page is worth seeing - but it stops
    # being the one chosen by default, so a page with a second route keeps
    # working without anybody having to notice why.
    usable = [item for item in page.reachable_by if item.get("available", True)]
    routes = usable or page.reachable_by
    return {
        "key": page.key,
        "platform": page.platform,
        "handle": page.handle,
        "label": page.label,
        "shared": page.shared,
        "engine_count": len(page.reachable_by),
        "reachable_by": page.reachable_by,
        # Unavailable only when every engine that reaches it has run out. One
        # exhausted route out of two is not a page you cannot post to.
        "available": bool(usable),
        "unavailable_reason": None if usable else page.reachable_by[0].get(
            "unavailable_reason"
        ),
        # The engine a post goes through unless the operator picks another. One,
        # never all of them: delivering a consolidated page through every engine
        # that can reach it would publish the same post to the same audience
        # once per engine.
        "default_provider": routes[0]["provider"],
        "default_integration_id": routes[0]["id"],
    }

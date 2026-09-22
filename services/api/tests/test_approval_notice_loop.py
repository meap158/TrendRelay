"""The loop that produced four Telegram cards for one post, end to end.

`test_approval_notices` pins each piece. This walks the whole thing the way it
actually happened, through `run_campaign`, because every piece passed its own
test while the loop ran: the announcer was correct about what it had been
handed, and what it had been handed was the same post over and over.

The shape, from the live database it was found in - one queue item, one
account, one slot, three hours:

    exec 1  held -> card 1 -> approved -> delivery failed (provider unreachable)
    exec 2  held -> card 2 -> approved -> delivery failed
    exec 3  held -> card 3 -> dismissed
    exec 4  held -> card 4

A failed or cancelled execution frees its slot and its queue item, and only
`record_published` ever stamps the item - so the next pass re-planned the
identical post, froze it, found it held, and announced it.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import select

from trendrelay_api import approval_notices
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.campaign_runner import run_campaign
from trendrelay_api.integrations import telegram
from trendrelay_api.publication_models import (
    CampaignApprovalNotice,
    PublicationExecution,
)
from test_campaign_authority import (  # noqa: F401 - fixtures
    NOW,
    autopilot,
    campaign_setup,
    engine_stub,
    session,
)


@pytest.fixture
def chat(monkeypatch):
    sent: list[dict] = []
    edited: list[dict] = []
    monkeypatch.setattr(telegram, "ready", lambda: True)
    monkeypatch.setattr(telegram, "configured_chat_id", lambda: "-100200300")
    monkeypatch.setattr(telegram, "approver_ids", lambda: set())
    monkeypatch.setattr(
        telegram, "send_message",
        lambda text, **kwargs: sent.append({"text": text, **kwargs})
        or {"message_id": len(sent), "chat_id": "-100200300"},
    )
    monkeypatch.setattr(
        telegram, "send_card",
        lambda text, **kwargs: sent.append({"text": text, **kwargs})
        or {"message_id": len(sent), "chat_id": "-100200300", "media": 0, "skipped": []},
    )
    monkeypatch.setattr(telegram, "settle_button", lambda callback_id, **kwargs: None)
    monkeypatch.setattr(telegram, "edit_card", lambda **kwargs: edited.append(kwargs) or True)
    monkeypatch.setattr(
        approval_notices, "get_settings",
        lambda: type("S", (), {"public_web_url": "https://relay.example/"})(),
    )
    return {"sent": sent, "edited": edited}


def _held_now(session) -> PublicationExecution | None:
    return session.scalar(
        select(PublicationExecution).where(PublicationExecution.state == "proposed")
    )


def _press(execution_id: str, verb: str) -> dict:
    return {
        "update_id": 1,
        "callback": {
            "id": "cb", "data": f"{verb}:{execution_id}",
            "from": {"id": "42", "username": "ana", "name": "Ana"},
            "chat_id": "-100200300", "message_id": 1,
        },
    }


def test_a_post_that_fails_to_deliver_twice_and_is_then_skipped_draws_one_card(
    session, tmp_path, engine_stub, chat,
) -> None:
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    run_campaign(session, pilot, now=NOW)
    session.commit()

    first = _held_now(session)
    assert first is not None
    assert len(chat["sent"]) == 1, "the post is announced when it is first held"

    # Approved from the chat, and the delivery fails the way it did: the
    # engine could not be reached. The execution settles, which frees the
    # slot and the queue item.
    approval_notices.handle_update(lambda: nullcontext(session), _press(first.id, "apr"))
    first.state = "failed"
    first.failure_class = "provider"
    first.error = "woopsocial: Could not reach api.woopsocial.com"
    session.commit()

    run_campaign(session, pilot, now=NOW + timedelta(minutes=10))
    session.commit()
    second = _held_now(session)
    assert second is not None and second.id != first.id, "the same post, frozen again"
    assert len(chat["sent"]) == 1, "and not announced again"

    # Approved again, failed again.
    approval_notices.handle_update(lambda: nullcontext(session), _press(second.id, "apr"))
    second.state = "failed"
    second.failure_class = "provider"
    session.commit()

    run_campaign(session, pilot, now=NOW + timedelta(minutes=20))
    session.commit()
    third = _held_now(session)
    assert third is not None and third.id != second.id

    # Dismissed, which frees it too - and it is proposed straight back.
    approval_notices.handle_update(lambda: nullcontext(session), _press(third.id, "dis"))
    session.commit()
    assert session.get(PublicationExecution, third.id).state == "cancelled"

    run_campaign(session, pilot, now=NOW + timedelta(minutes=30))
    session.commit()

    assert len(chat["sent"]) == 1, "four executions, three decisions, one card"
    notices = session.scalars(select(CampaignApprovalNotice)).all()
    assert len(notices) == 1
    assert notices[0].settled_at is not None, "somebody answered it"


def test_the_one_card_keeps_deciding_the_post_that_is_actually_held(
    session, tmp_path, engine_stub, chat,
) -> None:
    """The half that makes the silence safe.

    A card carries an execution id, and the row it was sent for is gone by the
    time the post is frozen again. Re-pointing is what keeps the press landing
    on the execution that exists - otherwise the one card the approver has
    would answer "that post is no longer here" about a post in the inbox.
    """
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    run_campaign(session, pilot, now=NOW)
    session.commit()
    first = _held_now(session)

    # Failed without anybody deciding it, so the notice is still open. The
    # post comes back at its *next* slot rather than this one - a moment it
    # has already had is spent, see `plan_campaign`'s `burned` - so the clock
    # moves past the slot it was frozen for before the next pass.
    first.state = "failed"
    first.failure_class = "provider"
    session.commit()
    run_campaign(session, pilot, now=NOW + timedelta(hours=4))
    session.commit()

    second = _held_now(session)
    assert second.id != first.id
    [edit] = chat["edited"]
    assert edit["message_id"] == 1
    assert edit["buttons"][0][0]["callback"] == f"apr:{second.id}"

    # And the press on the re-pointed card decides the post that is held.
    approval_notices.handle_update(lambda: nullcontext(session), _press(second.id, "apr"))
    session.commit()

    assert session.get(PublicationExecution, second.id).state == "queued"
    assert len(chat["sent"]) == 1


def test_a_post_that_goes_out_is_announced_again_when_it_next_comes_round(
    session, tmp_path, engine_stub, chat,
) -> None:
    """Publishing is the only thing that forgets a card, because the clip goes
    back into the rotation and its next outing is a new posting decision."""
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    run_campaign(session, pilot, now=NOW)
    session.commit()
    first = _held_now(session)

    approval_notices.handle_update(lambda: nullcontext(session), _press(first.id, "apr"))
    first.state = "published"
    approval_notices.clear_notice(session, first)
    session.commit()
    assert session.scalars(select(CampaignApprovalNotice)).all() == []

    run_campaign(session, pilot, now=NOW + timedelta(days=40))
    session.commit()

    assert len(chat["sent"]) == 2, "a new outing is a new decision, and is announced"


def test_the_autopilot_row_is_the_one_the_campaign_set_up(session) -> None:
    """Guards the fixture these tests lean on: a second `autopilot()` would
    fail the campaign's unique index rather than hand back the first."""
    autopilot(session, authority="assist", approvals_telegram=True)
    session.commit()
    assert session.scalar(select(CampaignAutopilot)).approvals_telegram is True

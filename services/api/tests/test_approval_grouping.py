"""One card for the posts one pass is holding.

A run freezes its whole horizon at once, so a campaign with several posting
times in the next day and a half puts a card in the chat for each of them
inside a second - eight questions arriving together, each its own message with
its own buttons. That is a chat nobody reads to the bottom of, so the approver
stops reading the one message that exists to be acted on.

Grouped, they are one card that lists them, each line carrying its own time and
its own pair of buttons - because the posts are different posts - with an
"approve all" for the common case where the answer is the same. What these pin
is that the grouping changes the shape of the message and nothing else: one
notice per post still claims its own announcement, a press still settles
exactly what it names, and the posts beside it keep their buttons.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import select

from trendrelay_api import approval_notices
from trendrelay_api import approval_words as words
from trendrelay_api.autopilot_models import (
    CampaignDestination,
    CampaignQueueItem,
)
from trendrelay_api.campaign_runner import run_campaign
from trendrelay_api.models import PublishingSlot
from trendrelay_api.publication_models import (
    CampaignApprovalNotice,
    PublicationExecution,
)
from test_approval_notices import chat, press  # noqa: F401 - fixtures
from test_campaign_authority import (  # noqa: F401 - fixtures
    NOW,
    autopilot,
    engine_stub,
    session,
)


def two_accounts_two_slots(session, tmp_path, *, hours: tuple[int, ...] = (12, 18)) -> None:
    """Two accounts, a posting time each, and a post apiece to fill them.

    The planner takes one post per posting time across the whole campaign - the
    accounts take turns rather than posting together - so two posts waiting is
    two moments, which is what a pass holds and what a grouped card carries.
    """
    for index in range(2):
        session.add(CampaignDestination(
            id=f"d{index}", workspace_id="ws", campaign_id="camp", provider="buffer",
            integration_id=f"acct-{index}", platform="youtube" if index else "tiktok",
            label=f"account {index}", enabled=True,
        ))
    for hour in hours:
        session.add(PublishingSlot(
            id=f"slot-{hour}", workspace_id="ws", weekday=-1, hour=hour, minute=0
        ))
    for index in range(len(hours)):
        clip = tmp_path / f"clip{index}.mp4"
        clip.write_bytes(b"the approved bytes")
        session.add(CampaignQueueItem(
            id=f"q{index}", workspace_id="ws", campaign_id="camp", state="approved",
            video_path=str(clip), body=f"Post number {index} of this campaign.",
            hashtags=["coffee"], position=index, last_posted_by_destination={},
            created_by="user-1",
        ))
    session.commit()


def held(session) -> list[PublicationExecution]:
    return list(session.scalars(
        select(PublicationExecution).order_by(PublicationExecution.scheduled_at)
    ).all())


def notices(session) -> list[CampaignApprovalNotice]:
    return list(session.scalars(
        select(CampaignApprovalNotice)
        .order_by(CampaignApprovalNotice.created_at, CampaignApprovalNotice.id)
    ).all())


def factory(session):
    """A session factory handing the loop the test's own session."""
    return lambda: nullcontext(session)


@pytest.fixture
def grouped(session, tmp_path, engine_stub, chat):  # noqa: F811
    """Two posts held by one pass, announced as one card."""
    two_accounts_two_slots(session, tmp_path)
    pilot = autopilot(
        session, authority="assist", approvals_telegram=True, approvals_grouped=True,
    )
    result = run_campaign(session, pilot, now=NOW)
    session.commit()
    assert len(held(session)) == 2, "one post per posting time"
    return {"pilot": pilot, "result": result}


# --- the card -------------------------------------------------------------------


def test_the_posts_one_pass_holds_arrive_as_one_card(session, chat, grouped) -> None:  # noqa: F811
    [card] = chat["sent"]

    assert "2 posts waiting" in card["text"]
    assert "<b>1.</b>" in card["text"] and "<b>2.</b>" in card["text"]
    for post in held(session):
        assert post.caption[:30] in card["text"]
    assert "2 posts on Telegram, on 1 card" in grouped["result"]["note"]


def test_each_line_carries_its_own_time(session, chat, grouped) -> None:  # noqa: F811
    """The times differ, and the time is half of what is being agreed to."""
    [card] = chat["sent"]

    assert "12:00" in card["text"] and "18:00" in card["text"]


def test_every_post_keeps_its_own_pair_of_buttons(session, chat, grouped) -> None:  # noqa: F811
    posts = held(session)
    [card] = chat["sent"]
    labels = [[button["label"] for button in row] for row in card["buttons"]]

    assert labels[0] == ["✅ Approve 1", "🚫 Dismiss 1"]
    assert labels[1] == ["✅ Approve 2", "🚫 Dismiss 2"]
    assert labels[2] == ["✅ Approve all", "↗ Open in app"]
    # Numbered to match the list, and aimed at one post each.
    presses = [
        [button.get("callback") for button in row] for row in card["buttons"][:2]
    ]
    assert presses == [
        [f"apr:{posts[0].id}", f"dis:{posts[0].id}"],
        [f"apr:{posts[1].id}", f"dis:{posts[1].id}"],
    ]


def test_each_post_still_claims_its_own_announcement(session, chat, grouped) -> None:  # noqa: F811
    """The claim is what makes a second card impossible, and it is per post.

    Grouping changes the message, not the bookkeeping: a row per post, all of
    them pointing at the one message. The pass after this one gives the other
    account its turn with the same two clips - a pairing nobody has been asked
    about, so it is a card - and the pass after that has nothing left to ask.
    """
    rows = notices(session)
    assert len(rows) == 2
    assert len({row.group_id for row in rows}) == 1
    assert all(row.group_id for row in rows)
    assert len({row.message_id for row in rows}) == 1

    run_campaign(session, grouped["pilot"], now=NOW)
    session.commit()

    assert len(chat["sent"]) == 2, "the other account, asked once"
    assert len(notices(session)) == 4
    assert len({row.group_id for row in notices(session)}) == 2

    run_campaign(session, grouped["pilot"], now=NOW)

    assert len(chat["sent"]) == 2, "nothing was asked twice"
    assert len(notices(session)) == 4, "and nothing new was claimed"


def test_a_campaign_that_asked_for_one_card_each_gets_one_card_each(
    session, tmp_path, engine_stub, chat,  # noqa: F811
) -> None:
    two_accounts_two_slots(session, tmp_path)

    result = run_campaign(
        session,
        autopilot(
            session, authority="assist", approvals_telegram=True,
            approvals_grouped=False,
        ),
        now=NOW,
    )

    assert len(chat["sent"]) == 2
    assert "Announced 2 posts on Telegram." in result["note"]
    for card in chat["sent"]:
        labels = [[button["label"] for button in row] for row in card["buttons"]]
        assert labels[0] == ["✅ Approve", "🚫 Dismiss"]
        assert "🚀 Approve and post now" in labels[1]


def test_a_pass_holding_more_than_a_card_takes_is_split() -> None:
    """Past the limit the buttons are below the fold, which is the problem."""
    cards = approval_notices._cards_of(
        [{"at": NOW, "execution_id": f"e{index}"} for index in range(8)],
        grouped=True,
    )

    assert [len(card) for card in cards] == [approval_notices.GROUP_LIMIT, 2]


def test_the_pass_limit_counts_cards_and_the_rest_are_one_line(
    session, chat,  # noqa: F811
) -> None:
    """A budget of messages, not of posts - which is what fills a chat.

    Ungrouped it is eight cards and eight posts. Grouped it is eight cards and
    six posts on each, so a pass carries six times as much before anything has
    to wait for the next one - and what waits is still one line with a count.
    """
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    room = approval_notices.CARDS_PER_PASS * approval_notices.GROUP_LIMIT
    held = [
        {"execution_id": f"pubexec_{index}", "destination": "acct", "caption": "x",
         "at": NOW, "reason": ""}
        for index in range(room + 3)
    ]

    note = approval_notices.announce_held(session, pilot, held)

    assert note == f"Announced {room} posts on Telegram, on 8 cards."
    assert len(chat["sent"]) == approval_notices.CARDS_PER_PASS + 1
    assert "3 more waiting in the inbox." in chat["sent"][-1]["text"]


def test_one_post_is_still_its_own_card(session, tmp_path, engine_stub, chat) -> None:  # noqa: F811
    """Grouping is about a chat filling up, and one post does not fill it.

    A card of one keeps everything a card of one had: its pictures, its working
    notes, and the third button that posts it now.
    """
    two_accounts_two_slots(session, tmp_path, hours=(12,))

    run_campaign(
        session,
        autopilot(session, authority="assist", approvals_telegram=True),
        now=NOW,
    )

    [card] = chat["sent"]
    assert "posts waiting" not in card["text"]
    labels = [[button["label"] for button in row] for row in card["buttons"]]
    assert labels[0] == ["✅ Approve", "🚫 Dismiss"]
    assert notices(session)[0].group_id is None


# --- a press on one of them -----------------------------------------------------


def test_deciding_one_post_leaves_the_others_waiting(
    session, chat, grouped,  # noqa: F811
) -> None:
    posts = held(session)

    approval_notices.handle_update(
        factory(session), press(posts[0].id, approval_notices.APPROVE)
    )
    session.commit()

    assert posts[0].state == "queued", "approved"
    assert posts[1].state == "proposed", "still waiting"
    [answer] = chat["settled"]
    # The card still carries the other post's pair, and no longer carries the
    # decided one's - a button over a decided post can only be refused.
    labels = [[button["label"] for button in row] for row in answer["buttons"]]
    assert labels == [["✅ Approve 2", "🚫 Dismiss 2"], ["↗ Open in app"]]
    assert "✅ Approved" in answer["text"], "said on the card, on that post's line"
    assert posts[1].caption[:30] in answer["text"], "and the other post is still there"


def test_approve_all_answers_every_post_still_waiting(
    session, chat, grouped,  # noqa: F811
) -> None:
    posts = held(session)
    group_id = notices(session)[0].group_id

    toast = approval_notices.handle_update(
        factory(session), press(group_id or "", approval_notices.APPROVE_ALL)
    )
    session.commit()

    assert [post.state for post in posts] == ["queued", "queued"]
    assert toast == words.say("en", "approved_all_by", count=2, who="@ana")
    [answer] = chat["settled"]
    assert answer["buttons"] is None, "nothing is left to decide"


def test_approve_all_after_one_was_already_decided_takes_the_rest(
    session, chat, grouped,  # noqa: F811
) -> None:
    posts = held(session)
    group_id = notices(session)[0].group_id

    approval_notices.handle_update(
        factory(session), press(posts[0].id, approval_notices.DISMISS)
    )
    session.commit()
    toast = approval_notices.handle_update(
        factory(session), press(group_id or "", approval_notices.APPROVE_ALL)
    )
    session.commit()

    assert posts[0].state == "cancelled", "the dismissal stands"
    assert posts[1].state == "queued"
    assert toast == words.say("en", "approved_all_by", count=1, who="@ana")


def test_approve_all_on_a_card_nobody_is_waiting_on_says_so(
    session, chat, grouped,  # noqa: F811
) -> None:
    posts = held(session)
    group_id = notices(session)[0].group_id

    for post in posts:
        approval_notices.handle_update(
            factory(session), press(post.id, approval_notices.DISMISS)
        )
        session.commit()
    chat["settled"].clear()
    toast = approval_notices.handle_update(
        factory(session), press(group_id or "", approval_notices.APPROVE_ALL)
    )

    assert toast == words.say("en", "became_settled")
    # Refused, so the card is left exactly as it is rather than rewritten.
    assert chat["settled"][-1]["message_id"] is None


def test_a_press_from_another_chat_decides_nothing(session, chat, grouped) -> None:  # noqa: F811
    group_id = notices(session)[0].group_id

    toast = approval_notices.handle_update(
        factory(session),
        press(group_id or "", approval_notices.APPROVE_ALL, chat_id="-999"),
    )

    assert toast == words.say("en", "not_this_chat")
    assert [post.state for post in held(session)] == ["proposed", "proposed"]


# --- the clock catching up ------------------------------------------------------


def test_the_post_whose_time_has_passed_wears_the_clock(session, chat, grouped) -> None:  # noqa: F811
    """One card, several moments: the clock belongs to the line it is about."""
    between = NOW + timedelta(hours=4)  # past the 12:00 post, before the 18:00 one

    note = approval_notices.announce_overdue(session, grouped["pilot"], now=between)
    session.commit()

    assert "1 overdue post" in note, "one card, not one per post on it"
    [edit] = chat["edited"]
    assert "⏰" in edit["text"]
    assert edit["text"].count("⏰") == 1, "the late one, not the whole card"
    assert all(row.overdue_notified_at is not None for row in notices(session))

    # And not again on the next tick, whichever row it reads first.
    chat["edited"].clear()
    assert approval_notices.announce_overdue(
        session, grouped["pilot"], now=between
    ) == ""
    assert chat["edited"] == []

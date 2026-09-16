"""A held post is decided where the approver is, for a campaign that asked.

The runner holds every frozen post below autonomous authority and nothing
told anybody. These pin the card a planning pass sends over Telegram, that it
goes only when the campaign asked, that a chat that cannot be reached is said
on the run rather than failing it - and that a press on the card is the same
decision the inbox makes, under the same checks, refused with a reason when
it is not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from contextlib import nullcontext

from trendrelay_api import approval_notices
from trendrelay_api import approval_words as words
from trendrelay_api.campaign_runner import run_campaign
from trendrelay_api.integrations import telegram
from trendrelay_api.models import AuditEvent
from trendrelay_api.publication_models import PublicationExecution
from test_campaign_authority import (  # noqa: F401 - fixtures
    NOW,
    autopilot,
    campaign_setup,
    engine_stub,
    session,
)


@pytest.fixture
def chat(monkeypatch):
    """Telegram set up, with the sends and answers captured rather than made."""
    sent: list[dict] = []
    settled: list[dict] = []
    monkeypatch.setattr(telegram, "ready", lambda: True)
    monkeypatch.setattr(telegram, "configured_chat_id", lambda: "-100200300")
    monkeypatch.setattr(telegram, "approver_ids", lambda: set())
    monkeypatch.setattr(
        telegram, "send_message",
        lambda text, **kwargs: sent.append({"text": text, **kwargs}) or {"message_id": len(sent)},
    )
    monkeypatch.setattr(
        telegram, "send_card",
        lambda text, **kwargs: sent.append({"text": text, **kwargs})
        or {"message_id": len(sent), "media": len(kwargs.get("images") or []), "skipped": []},
    )
    monkeypatch.setattr(
        telegram, "settle_button",
        lambda callback_id, **kwargs: settled.append({"id": callback_id, **kwargs}),
    )
    monkeypatch.setattr(
        approval_notices, "get_settings",
        lambda: type("S", (), {"public_web_url": "https://relay.example/"})(),
    )
    return {"sent": sent, "settled": settled}


def same(session):
    """A factory handing the test's own session to the loop, which closes what it opens."""
    return lambda: nullcontext(session)


def press(execution_id: str, verb: str, *, chat_id: str = "-100200300", user: str = "42") -> dict:
    return {
        "update_id": 900,
        "callback": {
            "id": "cb-1", "data": f"{verb}:{execution_id}",
            "from": {"id": user, "username": "ana", "name": "Ana"},
            "chat_id": chat_id, "message_id": 5,
        },
    }


def held_one(session, tmp_path, engine_stub, **overrides) -> PublicationExecution:
    campaign_setup(session, tmp_path)
    run_campaign(session, autopilot(session, authority="assist", **overrides), now=NOW)
    session.commit()
    [execution] = session.scalars(select(PublicationExecution)).all()
    assert execution.state == "proposed"
    return execution


# --- the card -------------------------------------------------------------------


def test_a_campaign_that_asked_gets_a_card_per_held_post_with_the_inbox_s_buttons(
    session, tmp_path, engine_stub, chat,
) -> None:
    campaign_setup(session, tmp_path)
    result = run_campaign(
        session, autopilot(session, authority="assist", approvals_telegram=True), now=NOW,
    )

    assert "Announced 1 post on Telegram." in result["note"]
    [card] = chat["sent"]
    assert card["text"].startswith("<b>Launch</b> · youtube account · ")
    assert "Three ways to pull a better espresso." in card["text"]
    assert "<i>" in card["text"]  # the hold's reason, in the runner's own words
    [execution] = session.scalars(select(PublicationExecution)).all()
    labels = [[button["label"] for button in row] for row in card["buttons"]]
    assert labels == [
        ["✅ Approve", "🚫 Dismiss"],
        ["🚀 Approve and post now", "↗ Open in app"],
    ]
    # Each press names the post it is about, and the link opens the inbox at
    # the address the app is served at, not localhost.
    assert card["buttons"][0][0]["callback"] == f"apr:{execution.id}"
    assert card["buttons"][0][1]["callback"] == f"dis:{execution.id}"
    assert card["buttons"][1][0]["callback"] == f"now:{execution.id}"
    assert card["buttons"][1][1]["url"] == (
        "https://relay.example/campaigns?campaign=camp#campaign-approvals"
    )
    # The post as it will look: its video travels with the card.
    assert card["video"].endswith("clip.mp4") and card["images"] == []


def test_a_carousel_s_pictures_travel_with_its_card(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    note = approval_notices.announce_held(session, pilot, [{
        "execution_id": "pubexec_1", "destination": "instagram", "caption": "Three slides",
        "at": None, "reason": "Waiting.", "image_paths": ["/a.png", "/b.png", "/c.png"],
        "video_path": None,
    }])
    assert note == "Announced 1 post on Telegram."
    [card] = chat["sent"]
    assert card["images"] == ["/a.png", "/b.png", "/c.png"] and card["video"] is None


def test_the_inbox_as_it_stands_carries_its_media_too(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub)
    execution.image_paths = ["/x.png", "/y.png"]
    session.commit()
    pilot = session.get(type(execution), execution.id) and execution
    from trendrelay_api.autopilot_models import CampaignAutopilot
    autopilot_row = session.get(CampaignAutopilot, "auto")
    note = approval_notices.announce_executions(session, autopilot_row, [execution])
    assert note == "Announced 1 post on Telegram."
    assert chat["sent"][0]["images"] == ["/x.png", "/y.png"]
    assert pilot is execution


def test_pictures_left_off_a_card_are_said_on_the_run(session, chat, monkeypatch) -> None:
    monkeypatch.setattr(
        telegram, "send_card",
        lambda text, **kwargs: {"message_id": 1, "media": 1, "skipped": ["b.png: not on disk"]},
    )
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    note = approval_notices.announce_held(session, pilot, [{
        "execution_id": "pubexec_1", "destination": "x", "caption": "c", "at": None,
        "reason": "", "image_paths": ["/a.png", "/b.png"],
    }])
    assert note == "Announced 1 post on Telegram. Media left off a card: b.png: not on disk"


def test_the_test_card_s_buttons_only_answer(session, chat) -> None:
    outcome = approval_notices.handle_update(same(session), press("test", "tst"))
    assert outcome.startswith("This was the test card. Nothing was decided.")
    [settled] = chat["settled"]
    assert settled["message_id"] == 5


def test_a_campaign_s_cards_speak_its_language_and_a_chosen_one_wins(
    session, tmp_path, engine_stub, chat,
) -> None:
    """A Vietnamese campaign's approver reads Vietnamese: the buttons, the
    decision, the due date. A campaign may choose another language for the
    approver who reads one its audience does not."""
    execution = held_one(
        session, tmp_path, engine_stub, approvals_telegram=True, post_language="vi",
    )
    [card] = chat["sent"]
    labels = [[button["label"] for button in row] for row in card["buttons"]]
    assert labels == [
        ["✅ Duyệt", "🚫 Bỏ qua"],
        ["🚀 Duyệt và đăng ngay", "↗ Mở trong ứng dụng"],
    ]
    assert "/08, " in card["text"] and "Aug" not in card["text"]  # day-first, not "Aug"

    outcome = approval_notices.handle_update(same(session), press(execution.id, "apr"))
    assert outcome == "✅ @ana đã duyệt"
    again = approval_notices.handle_update(same(session), press(execution.id, "apr"))
    assert again == "Đã được quyết định trong ứng dụng: trạng thái queued."

    # The choice overrides the campaign's language.
    from trendrelay_api.autopilot_models import CampaignAutopilot
    pilot = session.get(CampaignAutopilot, "auto")
    pilot.approvals_telegram_language = "ja"
    session.commit()
    chat["sent"].clear()
    note = approval_notices.announce_held(session, pilot, [{
        "execution_id": "pubexec_x", "destination": "acct", "caption": "c", "at": None, "reason": "",
    }])
    assert note == "Announced 1 post on Telegram."
    assert chat["sent"][0]["buttons"][0][0]["label"] == "✅ 承認"


def test_a_card_carries_the_post_s_working_notes(session, tmp_path, engine_stub, chat) -> None:
    """The app's inbox shows them to whoever is deciding, so the card does:
    they are the one thing about a held post that lives nowhere else."""
    from trendrelay_api.autopilot_models import CampaignQueueItem

    campaign_setup(session, tmp_path)
    session.get(CampaignQueueItem, "q1").context = "Trying the before-and-after angle."
    session.commit()
    run_campaign(
        session, autopilot(session, authority="assist", approvals_telegram=True), now=NOW,
    )

    [card] = chat["sent"]
    assert "<b>Notes</b>" in card["text"]
    assert "Trying the before-and-after angle." in card["text"]


def test_a_post_with_no_notes_says_nothing_about_them(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    approval_notices.announce_held(session, pilot, [{
        "execution_id": "pubexec_1", "destination": "x", "caption": "c",
        "at": None, "reason": "Waiting.", "queue_item_id": "gone",
    }])
    assert "Notes" not in chat["sent"][0]["text"]


def test_long_notes_are_cut_rather_than_running_past_the_screen() -> None:
    text = approval_notices.card_text(
        "Launch", {"destination": "x", "caption": "c", "notes": "n" * 500}, language="en",
    )
    body = text.split("<b>Notes</b>\n")[1]
    assert len(body) == approval_notices.NOTES_CHARS and body.endswith("…")


def test_a_campaign_that_did_not_ask_sends_nothing(session, tmp_path, engine_stub, chat) -> None:
    campaign_setup(session, tmp_path)
    result = run_campaign(session, autopilot(session, authority="assist"), now=NOW)
    assert result["held"]
    assert chat["sent"] == []
    assert "Telegram" not in result["note"]


def test_a_chat_that_cannot_be_reached_is_said_on_the_run_not_raised(
    session, tmp_path, engine_stub, monkeypatch,
) -> None:
    campaign_setup(session, tmp_path)
    monkeypatch.setattr(telegram, "ready", lambda: True)

    def refuse(text, **kwargs):
        raise telegram.TelegramUnavailable("Telegram refused the message: Chat not found")

    # Both doors, so a test never reaches the real Telegram this machine may
    # have set up - it once did, and Telegram answered.
    monkeypatch.setattr(telegram, "send_message", refuse)
    monkeypatch.setattr(telegram, "send_card", refuse)
    result = run_campaign(
        session, autopilot(session, authority="assist", approvals_telegram=True), now=NOW,
    )
    # Held in the app regardless; the run says why the chat did not hear.
    assert result["held"]
    assert "Not announced on Telegram: Telegram refused the message: Chat not found" in result["note"]


def test_telegram_not_set_up_is_said_in_the_tool_s_words(session, tmp_path, engine_stub, monkeypatch) -> None:
    campaign_setup(session, tmp_path)
    monkeypatch.setattr(telegram, "ready", lambda: False)
    monkeypatch.setattr(
        telegram, "provider_status", lambda: {"reason": "No bot token is saved.", "configured": False},
    )
    result = run_campaign(
        session, autopilot(session, authority="assist", approvals_telegram=True), now=NOW,
    )
    assert "Not announced on Telegram: No bot token is saved." in result["note"]


def test_the_app_button_is_left_off_when_the_app_has_no_address_a_phone_can_open(monkeypatch) -> None:
    """Telegram refuses a button to localhost - and the whole card with it.
    The default web address is localhost, so a card from a machine that has
    not published its address goes without the button rather than not at all."""
    for base in ("http://localhost:3000", "http://127.0.0.1:3001/", "http://192.168.1.4:3000", "http://mybox:3000"):
        monkeypatch.setattr(
            approval_notices, "get_settings", lambda base=base: type("S", (), {"public_web_url": base})(),
        )
        assert approval_notices.app_link("camp") is None
        labels = [button["label"] for row in approval_notices.card_buttons("e", "camp") for button in row]
        assert "↗ Open in app" not in labels and len(labels) == 3
    monkeypatch.setattr(
        approval_notices, "get_settings", lambda: type("S", (), {"public_web_url": "https://relay.example"})(),
    )
    assert approval_notices.app_link("camp") == "https://relay.example/campaigns?campaign=camp#campaign-approvals"


def test_a_card_reads_in_the_workspace_s_own_time_and_is_escaped() -> None:
    item = {
        "destination": "tiktok <main>",
        "caption": "First line & the hook",
        "at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        "reason": "Waiting for approval.",
    }
    text = approval_notices.card_text("Launch <Q3>", item, zone="Asia/Ho_Chi_Minh")
    assert text.startswith("<b>Launch &lt;Q3&gt;</b> · tiktok &lt;main&gt; · Mon 10 Aug, 19:00")  # UTC+7
    assert "First line &amp; the hook" in text
    assert text.endswith("<i>Waiting for approval.</i>")


def test_past_the_card_limit_the_rest_are_one_line_with_a_count(session, chat, monkeypatch) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    held = [
        {"execution_id": f"pubexec_{index}", "destination": "acct", "caption": "x",
         "at": None, "reason": ""}
        for index in range(approval_notices.CARDS_PER_PASS + 3)
    ]
    note = approval_notices.announce_held(session, pilot, held)
    assert note == f"Announced {approval_notices.CARDS_PER_PASS} posts on Telegram."
    assert len(chat["sent"]) == approval_notices.CARDS_PER_PASS + 1
    assert "3 more waiting in the inbox." in chat["sent"][-1]["text"]


# --- overdue ---------------------------------------------------------------------


def _overdue_row(session, identifier: str, *, at, campaign_id: str = "camp", **overrides):
    """A held post already frozen, its own due time set directly rather than
    reached by advancing a clock through a planning pass."""
    fields: dict = {
        "workspace_id": "ws",
        "campaign_id": campaign_id,
        "state": "proposed",
        "scheduled_at": at,
        "platform": "youtube",
        "destination_label": "youtube account",
        "caption": "Three ways to pull a better espresso.",
        "media_path": "clip.mp4",
        "held_reason": "Waiting for approval.",
        "held_reason_code": "hold_waiting",
    }
    fields.update(overrides)
    row = PublicationExecution(id=identifier, created_by="user-1", **fields)
    session.add(row)
    session.commit()
    return row


def test_a_card_left_alone_says_nothing_about_being_overdue() -> None:
    item = {"destination": "acct", "caption": "x", "at": None, "reason": ""}
    assert "overdue" not in approval_notices.card_text("Launch", item).casefold()


def test_an_overdue_card_leads_with_that_fact() -> None:
    """Telegram's own notification preview shows a message's first line, so
    the one new thing this send says has to be that line."""
    item = {
        "destination": "acct", "caption": "x",
        "at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC), "reason": "",
    }
    text = approval_notices.card_text("Launch", item, overdue=True)
    assert text.startswith("⏰ Still waiting")
    assert "Mon 10 Aug, 12:00" in text.splitlines()[0]


def test_an_overdue_card_says_nothing_extra_with_no_due_time_to_name() -> None:
    """`overdue=True` is a fact about a `scheduled_at` this never has without
    one being past - but a caller passing the flag on a copy with no time at
    all should get the ordinary card back, not a sentence missing its noun."""
    item = {"destination": "acct", "caption": "x", "at": None, "reason": ""}
    text = approval_notices.card_text("Launch", item, overdue=True)
    assert "overdue" not in text.casefold() and text.startswith("<b>Launch</b>")


def test_decided_replaces_the_wait_and_keeps_the_rest_of_the_card() -> None:
    item = {
        "destination": "acct", "caption": "Launch clip",
        "at": None, "reason": "Waiting for approval.", "reason_code": "hold_waiting",
    }
    text = approval_notices.card_text("Launch", item, decided="✅ Approved by @ana")
    assert text.startswith("<b>Launch</b> · acct")
    assert "Launch clip" in text
    assert "Waiting for approval" not in text
    assert text.endswith("<i>✅ Approved by @ana</i>")


def test_decided_text_is_trusted_rather_than_escaped_again() -> None:
    """`decided` arrives already HTML-safe - `approve_execution`'s own
    `{who}` is escaped once, by `decide`, before it ever reaches here - so
    escaping it a second time here would turn a `&` into `&amp;amp;`."""
    item = {"destination": "acct", "caption": "x", "at": None, "reason": ""}
    text = approval_notices.card_text("Launch", item, decided="Approved by A &amp; B")
    assert "Approved by A &amp; B" in text
    assert "&amp;amp;" not in text


def test_a_held_post_is_reminded_once_its_own_time_has_passed(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    due = NOW + timedelta(hours=3)
    execution = _overdue_row(session, "exec-late", at=due)

    note = approval_notices.announce_overdue(session, pilot, now=due + timedelta(minutes=1))
    session.commit()

    assert note == "Reminded about 1 overdue post on Telegram."
    [card] = chat["sent"]
    assert card["text"].startswith("⏰ Still waiting")
    assert card["buttons"][0][0]["callback"] == f"apr:{execution.id}"
    session.refresh(execution)
    # SQLite gives a naive datetime back on refresh - the same wall clock,
    # its tzinfo the query already used and discarded.
    assert execution.overdue_notified_at == (due + timedelta(minutes=1)).replace(tzinfo=None)


def test_nothing_is_overdue_before_its_own_due_time(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    due = NOW + timedelta(hours=3)
    _overdue_row(session, "exec-not-yet", at=due)

    note = approval_notices.announce_overdue(session, pilot, now=due - timedelta(minutes=1))

    assert note == "" and chat["sent"] == []


def test_the_reminder_is_sent_once_and_never_repeated(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    due = NOW + timedelta(hours=3)
    _overdue_row(session, "exec-once", at=due)
    later = due + timedelta(hours=1)

    first = approval_notices.announce_overdue(session, pilot, now=later)
    session.commit()
    second = approval_notices.announce_overdue(session, pilot, now=later + timedelta(hours=5))

    assert first == "Reminded about 1 overdue post on Telegram."
    assert second == ""
    assert len(chat["sent"]) == 1


def test_a_campaign_that_never_asked_for_telegram_is_never_reminded(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=False)
    due = NOW + timedelta(hours=3)
    _overdue_row(session, "exec-quiet", at=due)

    note = approval_notices.announce_overdue(session, pilot, now=due + timedelta(hours=1))

    assert note == "" and chat["sent"] == []


def test_past_the_card_limit_the_overdue_rest_are_one_line_with_a_count(session, chat) -> None:
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    due = NOW + timedelta(hours=3)
    for index in range(approval_notices.CARDS_PER_PASS + 3):
        _overdue_row(session, f"exec-late-{index}", at=due)

    note = approval_notices.announce_overdue(session, pilot, now=due + timedelta(hours=1))

    assert note == f"Reminded about {approval_notices.CARDS_PER_PASS} overdue posts on Telegram."
    assert len(chat["sent"]) == approval_notices.CARDS_PER_PASS + 1
    assert "3 more overdue, waiting in the inbox." in chat["sent"][-1]["text"]


def test_a_planning_pass_reminds_about_a_post_held_on_an_earlier_one(
    session, tmp_path, engine_stub, chat,
) -> None:
    """The pass that reminds does not need to have held anything itself -
    the whole point is a post frozen well before today still gets told
    about once today's clock runs past its own due time."""
    campaign_setup(session, tmp_path)
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    run_campaign(session, pilot, now=NOW)
    session.commit()
    [execution] = session.scalars(select(PublicationExecution)).all()
    # Re-selected after a commit, so SQLite hands the naive form back - this
    # workspace deals only in UTC, so putting it back is exact.
    due = execution.scheduled_at.replace(tzinfo=UTC)

    result = run_campaign(session, pilot, now=due + timedelta(hours=1))

    assert "Reminded about 1 overdue post on Telegram." in result["note"]
    assert len(chat["sent"]) == 2, "the first hold and the overdue reminder"
    assert chat["sent"][1]["text"].startswith("⏰ Still waiting")


# --- the press ------------------------------------------------------------------


def test_a_press_approves_the_post_the_way_the_inbox_does(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    outcome = approval_notices.handle_update(same(session), press(execution.id, "apr"))

    assert outcome == "✅ Approved by @ana"
    session.refresh(execution)
    assert execution.state == "queued"
    assert engine_stub, "approving queues the publish, as the inbox would"
    # The toast is the short sentence; the card the message becomes is
    # longer, and the toast is what the buttons' `outcome` always was.
    [settled] = chat["settled"]
    assert settled["message_id"] == 5 and settled["toast"] == outcome
    assert settled["text"] != outcome
    # The decision is on the record with the Telegram identity beside it.
    [event] = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "campaign.exception_approved")
    ).all()
    assert event.detail["via"] == "telegram"
    assert event.detail["telegram_user"] == "@ana"
    assert event.detail["telegram_user_id"] == "42"
    assert event.detail["publish_now"] is False


def test_a_decided_card_still_says_what_it_was_about(session, tmp_path, engine_stub, chat) -> None:
    """The post the card was about does not leave with the buttons.

    A press used to rewrite the whole message down to its decision - "✅
    Approved by @ana" - which read the caption and the destination out of
    the chat at the exact moment somebody most needs to check them against
    what they just pressed. The card keeps them, and now says what happened
    where the wait used to be.
    """
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    approval_notices.handle_update(same(session), press(execution.id, "apr"))

    [settled] = chat["settled"]
    text = settled["text"]
    assert text.startswith("<b>Launch</b> · youtube account · ")
    assert "Three ways to pull a better espresso." in text
    assert "Waiting for approval" not in text
    assert text.endswith("<i>✅ Approved by @ana</i>")


def test_a_dismissed_card_keeps_its_content_too(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    approval_notices.handle_update(same(session), press(execution.id, "dis"))

    [settled] = chat["settled"]
    assert "Three ways to pull a better espresso." in settled["text"]
    assert settled["text"].endswith("<i>🚫 Dismissed by @ana</i>")


def test_working_notes_ride_along_with_a_decided_card_too(
    session, tmp_path, engine_stub, chat,
) -> None:
    from trendrelay_api.autopilot_models import CampaignQueueItem

    campaign_setup(session, tmp_path)
    session.get(CampaignQueueItem, "q1").context = "The client asked for this angle."
    session.commit()
    pilot = autopilot(session, authority="assist", approvals_telegram=True)
    run_campaign(session, pilot, now=NOW)
    session.commit()
    [execution] = session.scalars(select(PublicationExecution)).all()

    approval_notices.handle_update(same(session), press(execution.id, "apr"))

    [settled] = chat["settled"]
    assert "The client asked for this angle." in settled["text"]


def test_approve_and_post_now_asks_for_immediate_delivery(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    outcome = approval_notices.handle_update(same(session), press(execution.id, "now"))
    assert outcome == "🚀 Approved and posting now by @ana"
    assert engine_stub[-1]["delivery_override"] == "now"


def test_a_press_dismisses_the_post_and_frees_its_slot(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    outcome = approval_notices.handle_update(same(session), press(execution.id, "dis"))
    assert outcome == "🚫 Dismissed by @ana"
    session.refresh(execution)
    assert execution.state == "cancelled"
    assert engine_stub == []
    [event] = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "campaign.exception_dismissed")
    ).all()
    assert event.detail["via"] == "telegram"


def test_a_press_from_another_chat_or_an_unlisted_person_is_refused(
    session, tmp_path, engine_stub, chat, monkeypatch,
) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)

    outcome = approval_notices.handle_update(
        same(session), press(execution.id, "apr", chat_id="-999"),
    )
    assert "not the one TrendRelay was set up with" in outcome

    monkeypatch.setattr(telegram, "approver_ids", lambda: {"7"})
    outcome = approval_notices.handle_update(same(session), press(execution.id, "apr", user="42"))
    assert "not on the approvers list" in outcome

    session.refresh(execution)
    assert execution.state == "proposed"
    # Refused presses leave the card as it is; only the toast says why.
    assert all(item["message_id"] is None for item in chat["settled"])
    assert all(item["toast"] == item_outcome for item, item_outcome in zip(
        chat["settled"], [chat["settled"][0]["toast"], outcome],
    ))


def test_a_second_press_reads_as_a_fact_not_a_failure(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    approval_notices.handle_update(same(session), press(execution.id, "dis"))
    again = approval_notices.handle_update(same(session), press(execution.id, "apr"))
    assert again == "Already decided in the app: it is cancelled."
    session.refresh(execution)
    assert execution.state == "cancelled"


def test_a_button_that_is_not_ours_is_refused_and_nothing_changes(session, tmp_path, engine_stub, chat) -> None:
    execution = held_one(session, tmp_path, engine_stub, approvals_telegram=True)
    outcome = approval_notices.handle_update(same(session), press(execution.id, "zap"))
    assert outcome == "That button is not one of ours."
    gone = approval_notices.handle_update(same(session), press("pubexec_missing", "apr"))
    assert gone == "That post is no longer here."


def test_the_poll_reads_from_where_it_left_off_and_moves_on_per_press(session, monkeypatch, tmp_path) -> None:
    offsets: list[int] = []
    seen: list[int | None] = []
    monkeypatch.setattr(telegram, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(telegram, "write_offset", lambda offset: offsets.append(offset))
    monkeypatch.setattr(telegram, "read_offset", lambda: 41)
    monkeypatch.setattr(
        telegram, "fetch_updates",
        lambda offset, timeout=0: seen.append(offset) or [
            {"update_id": 41},  # not a press: skipped, still moved past
            {"update_id": 42, "callback": {"id": "x", "data": "apr:gone", "from": {"id": "1"},
                                          "chat_id": "-1", "message_id": 2}},
        ],
    )
    answered: list[str] = []
    monkeypatch.setattr(approval_notices, "handle_update", lambda factory, update: answered.append(
        update["callback"]["data"]) or "done" if "callback" in update else None)

    handled = approval_notices.poll_once(same(session), timeout=0)

    assert seen == [41]
    assert handled == 1 and answered == ["apr:gone"]
    assert offsets == [42, 43]


def test_the_card_names_the_network_a_post_is_going_to() -> None:
    """An account label is whatever its owner typed. "anisenpaitok" does not
    say where it posts, and where it posts is the first thing an approver
    wants of the words underneath."""
    item = {
        "destination": "anisenpaitok",
        "platform": "tiktok",
        "caption": "c",
        "at": None,
    }

    text = approval_notices.card_text("Storytelling", item, language="vi")

    assert text.startswith(f"<b>Storytelling</b> · TikTok · anisenpaitok")


def test_the_network_is_left_out_when_the_account_already_says_it() -> None:
    # Otherwise a card reads "TikTok · tiktok main", which is the same word
    # twice for a reader who only needed it once.
    item = {"destination": "tiktok main", "platform": "tiktok", "caption": "c", "at": None}

    text = approval_notices.card_text("Launch", item)

    assert text.startswith(f"<b>Launch</b> · tiktok main")


def test_an_unnamed_network_is_still_named() -> None:
    # Better a card that says `bluesky2` than one that quietly drops where a
    # post is going because nobody has added the proper name yet.
    item = {"destination": "an account", "platform": "bluesky2", "caption": "c", "at": None}

    assert "bluesky2" in approval_notices.card_text("Launch", item)


def test_why_a_post_waits_is_said_in_the_campaign_s_language() -> None:
    """The line the whole card was missing.

    Its buttons, its labels and its dates were already the campaign's; the
    one sentence explaining why the post is sitting there was the server's
    English underneath all of them.
    """
    item = {
        "destination": "anisenpaitok",
        "caption": "c",
        "at": None,
        "reason": "Waiting for approval: this exact frozen post reaches its engine "
                  "only after a person approves it.",
        "reason_code": "hold_waiting",
    }

    vietnamese = approval_notices.card_text("Storytelling", item, language="vi")
    english = approval_notices.card_text("Storytelling", item, language="en")

    assert words.say("vi", "hold_waiting") in vietnamese
    assert "Waiting for approval" not in vietnamese
    assert "Waiting for approval" in english


def test_a_post_held_before_the_reasons_had_keys_still_says_why() -> None:
    # Rows frozen before the key column existed carry the sentence and no
    # code. English is not what that reader asked for, but it is the reason.
    item = {
        "destination": "acct", "caption": "c", "at": None,
        "reason": "Waiting for approval.", "reason_code": None,
    }

    assert "Waiting for approval." in approval_notices.card_text("Launch", item, language="vi")


def test_a_reason_key_nothing_recognises_falls_back_to_the_sentence() -> None:
    item = {
        "destination": "acct", "caption": "c", "at": None,
        "reason": "Waiting for approval.", "reason_code": "hold_from_the_future",
    }

    assert "Waiting for approval." in approval_notices.card_text("Launch", item, language="vi")


def test_a_real_run_holds_a_post_in_words_its_approver_reads(
    session, tmp_path, engine_stub, chat,
) -> None:
    """End to end, which is where this was noticed: a Vietnamese campaign's
    card carried Vietnamese buttons over an English explanation of why the
    post was sitting there. The runner freezes the reason as a key now, and
    the card looks the words up in the language it is being read in."""
    execution = held_one(
        session, tmp_path, engine_stub, approvals_telegram=True, post_language="vi",
    )

    assert execution.held_reason_code == "hold_waiting"
    # The sentence is kept beside the key, because the app reads it.
    assert "Waiting for approval" in execution.held_reason

    [card] = chat["sent"]
    assert words.say("vi", "hold_waiting") in card["text"]
    assert "Waiting for approval" not in card["text"]

"""Approving a held post from the chat where the approver is.

The campaign runner holds every frozen post below autonomous authority for a
person, and the inbox in the app is where that person decides. Nothing told
them the inbox had something in it. This does, over Telegram, for a campaign
that asked - and lets them decide there: each held post is one message with
the choices the inbox offers under it, and pressing one settles it, the way
a card is swiped.

Two halves. `announce_held` runs in the campaign runner's planning pass and
sends one message per held post. `handle_update` runs in the worker's poll
loop and turns a press into the same `approve_execution` or dismissal the
inbox performs, under the same checks the post would meet there, with the
approver's Telegram identity on the audit event.

Who may press is the chat: the bot sends to the one chat the operator saved,
a press from any other chat is ignored, and an optional list of user ids
narrows the chat further. That is the trust boundary, and it is written down
in the tool's notes rather than implied.
"""

from __future__ import annotations

import html
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api import approval_words as words
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.config import get_settings
from trendrelay_api.foundation import audit
from trendrelay_api.models import Campaign, Workspace
from trendrelay_api.publication_models import PublicationExecution

#: How many held posts one planning pass sends as cards. Past this the rest
#: are one line with a count: the point is to be told, and the inbox lists
#: them all.
CARDS_PER_PASS = 8
#: How much of a caption a card quotes.
CAPTION_CHARS = 400
#: How much of a post's working notes a card quotes. Shorter than the
#: caption: the notes are context for a decision, and a card that runs past
#: a phone screen is one nobody reads to the end of.
NOTES_CHARS = 300

#: What a press means. Short, because Telegram allows 64 bytes of data and
#: the execution id takes forty of them.
APPROVE = "apr"
APPROVE_NOW = "now"
DISMISS = "dis"
#: The test card's buttons. They answer, and decide nothing.
TEST = "tst"

#: How long one poll holds its request open waiting for a press.
POLL_SECONDS = 25
#: How long to wait before asking again after Telegram could not be read, or
#: while the tool is not set up.
RETRY_SECONDS = 30


def _when(at: datetime | None, zone: str | None, language: str = "en") -> str:
    """The due time as the workspace keeps time, written as the reader writes dates."""
    if at is None:
        return ""
    try:
        local = at.astimezone(ZoneInfo(zone)) if zone else at
    except (ValueError, KeyError):
        local = at
    return words.when(local, language)


def card_language(autopilot: CampaignAutopilot) -> str:
    """The language a campaign's cards are written in.

    Its own choice when it made one; else the language it posts in, which is
    the approver's language far more often than the server's; else English.
    """
    return words.language_for(
        getattr(autopilot, "approvals_telegram_language", None), autopilot.post_language,
    )


def _shorten(text: str, limit: int) -> str:
    trimmed = (text or "").strip()
    if len(trimmed) > limit:
        trimmed = trimmed[: limit - 1].rstrip() + "…"
    return trimmed


def _excerpt(caption: str) -> str:
    return _shorten(caption, CAPTION_CHARS)


def approvals_url(campaign_id: str) -> str:
    """The campaign's approvals in the app, from the address the app is served at."""
    base = get_settings().public_web_url.rstrip("/")
    return f"{base}/campaigns?campaign={campaign_id}#campaign-approvals"


def app_link(campaign_id: str) -> str | None:
    """The approvals link as a button can carry it, or None when it cannot.

    Telegram refuses a button whose address is not one a phone could open -
    `localhost`, a bare hostname, a private address - and it refuses the whole
    message with it. The default web address is `http://localhost:3000`, so
    without this every card from a machine that has not published its address
    would have been refused for the sake of a button that could not work.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415

    url = approvals_url(campaign_id)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return None
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or "." not in host:
        return None
    if host.endswith(".local") or host.startswith(("10.", "192.168.", "172.")):
        return None
    return url


def card_text(
    campaign_name: str, item: dict[str, Any], *, zone: str | None = None, language: str = "en",
    overdue: bool = False,
) -> str:
    """One held post as a message: where, when, the words, why it waits.

    And the working notes, when the post carries any. The app's inbox shows
    them to whoever is deciding - they are the one thing about a held post
    that lives nowhere else - and a card that left them out would be the
    same decision made with less in front of it.

    `overdue` marks a card sent a second time, because the post's own due
    time has since passed - see `announce_overdue`. Put first, ahead of even
    the campaign's name: Telegram's own notification preview shows a
    message's first line, and the one new fact this send exists to carry is
    that the clock ran out, not which campaign it was already clear was late.
    """
    where = html.escape(str(item.get("destination") or "an account"))
    when = _when(item.get("at"), zone, language)
    # Which network, ahead of which account on it. An account's label is
    # whatever its owner called it - "anisenpaitok" says nothing about where
    # it posts - and where the words are going is the first thing an approver
    # wants of them. Left out when the label already says it, so a card never
    # reads "TikTok · tiktok main".
    network = words.platform_name(str(item.get("platform") or ""))
    if network and network.casefold() in str(item.get("destination") or "").casefold():
        network = ""
    lines: list[str] = []
    if overdue and when:
        lines.extend([
            html.escape(words.say(language, "overdue_notice", when=when)), "",
        ])
    lines.extend([
        f"<b>{html.escape(campaign_name)}</b>"
        + (f" · {html.escape(network)}" if network else "")
        + f" · {where}"
        + (f" · {html.escape(when)}" if when else ""),
        "",
    ])
    caption = _excerpt(str(item.get("caption") or ""))
    if caption:
        lines.extend([html.escape(caption), ""])
    notes = _shorten(str(item.get("notes") or ""), NOTES_CHARS)
    if notes:
        lines.extend([
            f"<b>{html.escape(words.say(language, 'notes'))}</b>",
            html.escape(notes),
            "",
        ])
    # The card's language for a reason it knows, the stored sentence for one
    # frozen before the reasons had keys - still English, and still better
    # than a card that says nothing about why the post is waiting.
    code = str(item.get("reason_code") or "").strip()
    reason = (
        words.say(language, code)
        if code in words.WORDS["en"]
        else str(item.get("reason") or "").strip()
    )
    if reason:
        lines.append(f"<i>{html.escape(reason)}</i>")
    return "\n".join(lines).rstrip()


def card_buttons(
    execution_id: str, campaign_id: str, *, test: bool = False, language: str = "en",
) -> list[list[dict[str, str]]]:
    """The inbox's choices, as buttons: decide here, or go and look.

    A test card has the same buttons so the hand learns the layout, but each
    of them only answers that it was the test.
    """
    verbs = (TEST, TEST, TEST) if test else (APPROVE, DISMISS, APPROVE_NOW)
    second_row = [{
        "label": words.say(language, "approve_now"), "callback": f"{verbs[2]}:{execution_id}",
    }]
    link = app_link(campaign_id)
    if link:
        second_row.append({"label": words.say(language, "open_app"), "url": link})
    return [
        [
            {"label": words.say(language, "approve"), "callback": f"{verbs[0]}:{execution_id}"},
            {"label": words.say(language, "dismiss"), "callback": f"{verbs[1]}:{execution_id}"},
        ],
        second_row,
    ]


def _media_of(item: dict[str, Any]) -> tuple[list[str], str | None]:
    """The post's pictures, or its video, as the card will show them."""
    images = [str(path) for path in (item.get("image_paths") or []) if path]
    video = str(item.get("video_path") or "") or None
    return images, video


def _notes_for(session: Session, held: list[dict[str, Any]]) -> None:
    """Fill each held item's working notes in, from the post it was frozen from.

    One query, for the cards about to go and only then: the column is
    deferred because nothing that sweeps this table wants it, and an
    approval card is exactly the thing that does.
    """
    from trendrelay_api.autopilot_models import CampaignQueueItem

    wanted = [str(item["queue_item_id"]) for item in held if item.get("queue_item_id")]
    if not wanted:
        return
    notes = dict(
        session.execute(
            select(CampaignQueueItem.id, CampaignQueueItem.context).where(
                CampaignQueueItem.id.in_(wanted)
            )
        ).all()
    )
    for item in held:
        item.setdefault("notes", notes.get(str(item.get("queue_item_id") or "")) or "")


def announce_held(
    session: Session, autopilot: CampaignAutopilot, held: list[dict[str, Any]],
) -> str:
    """Send each held post to the campaign's approver on Telegram, as a card.

    Returns one sentence for the run's note, whichever way it went. Never
    raises: the posts are held in the app regardless, and a chat that could
    not be reached is a thing to say, not a reason to fail the plan.
    """
    if not held:
        return ""
    from trendrelay_api.integrations import telegram

    if not telegram.ready():
        reason = telegram.provider_status()["reason"] or "Telegram is not set up."
        return f"Not announced on Telegram: {reason}"
    campaign = session.get(Campaign, autopilot.campaign_id)
    workspace = session.get(Workspace, autopilot.workspace_id)
    name = campaign.name if campaign else "Campaign"
    zone = workspace.timezone if workspace else None
    language = card_language(autopilot)
    _notes_for(session, held[:CARDS_PER_PASS])
    sent = 0
    left_out: list[str] = []
    try:
        for item in held[:CARDS_PER_PASS]:
            images, video = _media_of(item)
            outcome = telegram.send_card(
                card_text(name, item, zone=zone, language=language),
                buttons=card_buttons(
                    str(item["execution_id"]), autopilot.campaign_id, language=language,
                ),
                images=images, video=video,
            )
            left_out.extend(outcome.get("skipped") or [])
            sent += 1
        rest = len(held) - sent
        if rest > 0:
            link = app_link(autopilot.campaign_id)
            telegram.send_message(
                f"<b>{html.escape(name)}</b> · "
                + html.escape(words.say(language, "more_waiting", count=rest)),
                buttons=[[{"label": words.say(language, "open_app"), "url": link}]] if link else None,
            )
    except telegram.TelegramUnavailable as error:
        if sent:
            return f"Announced {sent} of {len(held)} on Telegram; then: {error}"
        return f"Not announced on Telegram: {error}"
    note = f"Announced {sent} post{'' if sent == 1 else 's'} on Telegram."
    if left_out:
        # Said, because a card without its pictures is a different decision.
        note = f"{note} Media left off a card: {'; '.join(left_out[:3])}"
    return note


def announce_executions(
    session: Session, autopilot: CampaignAutopilot, executions: list[PublicationExecution],
) -> str:
    """Send posts already held - the inbox as it stands - as cards.

    What switching the campaign's Telegram on does for what is waiting at that
    moment, so the switch is not "from the next pass on" while three posts sit
    unannounced. Same cards, from the execution's own record.
    """
    held = [
        {
            "execution_id": execution.id,
            "destination": execution.destination_label,
            "caption": execution.caption,
            "at": execution.scheduled_at,
            "platform": execution.platform,
            "reason": execution.held_reason,
            "reason_code": execution.held_reason_code,
            "image_paths": list(execution.image_paths or []),
            "video_path": execution.media_path,
            "queue_item_id": execution.queue_item_id,
        }
        for execution in executions
        if execution.state == "proposed"
    ]
    return announce_held(session, autopilot, held)


def announce_overdue(
    session: Session, autopilot: CampaignAutopilot, *, now: datetime | None = None,
) -> str:
    """Tell the approver once more, the first time a held post is found late.

    `announce_held` sends a card the moment a post is frozen for a person -
    usually well ahead of its own due time, since a campaign holds the next
    slot in front of somebody before the clock gets there. A card sent early
    says nothing once the clock catches up to it: Telegram does not remind on
    its own, and a chat with any traffic buries that first card under
    whatever came after it. This is the one follow-up a held post ever gets -
    once, the first tick its `scheduled_at` is found in the past - so a
    missed deadline is something the approver was told, rather than a
    silence nobody chose.

    Called every tick regardless of what that tick held, because the posts
    this looks for are not new: they were frozen minutes, hours or days ago
    and are still `proposed` now that their own time has run out. Never
    raises, for the reason `announce_held` does not either: the post stays
    held in the app whichever way the chat goes.
    """
    if not autopilot.approvals_telegram:
        return ""
    moment = now or datetime.now(UTC)
    from trendrelay_api.integrations import telegram

    if not telegram.ready():
        return ""
    overdue = session.scalars(
        select(PublicationExecution)
        .where(
            PublicationExecution.workspace_id == autopilot.workspace_id,
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state == "proposed",
            PublicationExecution.scheduled_at.is_not(None),
            PublicationExecution.scheduled_at < moment,
            PublicationExecution.overdue_notified_at.is_(None),
        )
        .order_by(PublicationExecution.scheduled_at)
    ).all()
    if not overdue:
        return ""
    campaign = session.get(Campaign, autopilot.campaign_id)
    workspace = session.get(Workspace, autopilot.workspace_id)
    name = campaign.name if campaign else "Campaign"
    zone = workspace.timezone if workspace else None
    language = card_language(autopilot)
    sent = 0
    try:
        for execution in overdue[:CARDS_PER_PASS]:
            item = {
                "destination": execution.destination_label,
                "caption": execution.caption,
                "at": execution.scheduled_at,
                "platform": execution.platform,
                "reason": execution.held_reason,
                "reason_code": execution.held_reason_code,
            }
            # Text only, with the same buttons the first card had: a press
            # here decides the execution by its id, not by which message
            # carried it, so this settles it exactly as the original card or
            # the inbox would. Re-uploading the pictures a second time would
            # be the loud part of this message, not the useful part - the
            # useful part is the one new fact, which is that time ran out.
            telegram.send_message(
                card_text(name, item, zone=zone, language=language, overdue=True),
                buttons=card_buttons(execution.id, autopilot.campaign_id, language=language),
            )
            execution.overdue_notified_at = moment
            sent += 1
        rest = len(overdue) - sent
        if rest > 0:
            link = app_link(autopilot.campaign_id)
            telegram.send_message(
                f"<b>{html.escape(name)}</b> · "
                + html.escape(words.say(language, "more_overdue", count=rest)),
                buttons=[[{"label": words.say(language, "open_app"), "url": link}]] if link else None,
            )
    except telegram.TelegramUnavailable as error:
        if sent:
            return f"Reminded about {sent} of {len(overdue)} overdue post(s) on Telegram; then: {error}"
        return f"Not reminded on Telegram: {error}"
    return f"Reminded about {sent} overdue post{'' if sent == 1 else 's'} on Telegram."


# --- a press ---------------------------------------------------------------------


class PressRefused(ValueError):
    """The press was understood and not acted on; the reason is for the presser."""


def _decision(data: str) -> tuple[str, str]:
    verb, _, execution_id = (data or "").partition(":")
    if verb not in (APPROVE, APPROVE_NOW, DISMISS, TEST) or not execution_id:
        raise PressRefused(words.say("en", "not_ours"))
    return verb, execution_id


def _who(callback: dict[str, Any]) -> str:
    person = callback.get("from") or {}
    handle = person.get("username")
    return f"@{handle}" if handle else (person.get("name") or f"user {person.get('id')}")


def decide(session: Session, callback: dict[str, Any]) -> str:
    """Carry out one press. Returns the line the message will say from now on.

    The same path the inbox takes: `approve_execution` with the post's own
    checks, or the dismissal that frees the slot. Refused with a reason when
    the post is no longer held - decided in the app meanwhile, or already
    pressed - so the second press reads as a fact rather than a failure.
    """
    from trendrelay_api.campaign_autopilot_api import _omit_unsupported_thread
    from trendrelay_api.campaign_runner import approve_execution
    from trendrelay_api.integrations import telegram
    from trendrelay_api.models import utc_now

    # Before the post is known, the reader's language is not: these first
    # refusals are English, and everything from the post on is the card's.
    chat = telegram.configured_chat_id()
    if not chat or str(callback.get("chat_id")) != chat:
        raise PressRefused(words.say("en", "not_this_chat"))
    allowed = telegram.approver_ids()
    presser = str((callback.get("from") or {}).get("id") or "")
    if allowed and presser not in allowed:
        raise PressRefused(words.say("en", "not_approver"))
    verb, execution_id = _decision(callback.get("data", ""))
    if verb == TEST:
        return words.say("en", "test_answer", who=html.escape(_who(callback)))

    execution = session.get(PublicationExecution, execution_id)
    if execution is None:
        raise PressRefused(words.say("en", "gone"))
    autopilot = session.scalar(
        select(CampaignAutopilot).where(
            CampaignAutopilot.campaign_id == execution.campaign_id,
            CampaignAutopilot.workspace_id == execution.workspace_id,
        )
    )
    language = card_language(autopilot) if autopilot else "en"
    if execution.state != "proposed":
        raise PressRefused(words.say(language, "already_decided", state=execution.state))
    if autopilot is None:
        raise PressRefused(words.say("en", "no_autopilot"))
    who = _who(callback)
    identity = {
        "via": "telegram",
        "telegram_user_id": presser,
        "telegram_user": who,
    }
    if verb == DISMISS:
        execution.state = "cancelled"
        execution.reconciled_at = utc_now()
        execution.updated_at = utc_now()
        audit(
            session, None, execution.workspace_id, autopilot.created_by,
            "campaign.exception_dismissed", "campaign", execution.campaign_id or "",
            {"execution_id": execution.id, "stop_proposing": False, "post_paused": False,
             **identity},
        )
        session.commit()
        return words.say(language, "dismissed_by", who=html.escape(who))
    omitted = _omit_unsupported_thread(execution)
    publish_now = verb == APPROVE_NOW
    try:
        approve_execution(session, autopilot, execution, publish_now=publish_now)
    except ValueError as error:
        session.rollback()
        raise PressRefused(str(error)) from error
    audit(
        session, None, execution.workspace_id, autopilot.created_by,
        "campaign.exception_approved", "campaign", execution.campaign_id or "",
        {"execution_id": execution.id, "state": execution.state, "publish_now": publish_now,
         "omitted_unsupported_replies": omitted, **identity},
    )
    session.commit()
    if execution.state == "failed":
        return words.say(
            language, "approved_but_failed", who=html.escape(who),
            reason=html.escape(execution.error or words.say(language, "see_the_app")),
        )
    return words.say(
        language, "approved_now_by" if publish_now else "approved_by", who=html.escape(who),
    )


def handle_update(session_factory: Any, update: dict[str, Any]) -> str | None:
    """One update from the poll: decide it, and rewrite the card to say so.

    Returns what the card now says, or None for an update with no press in it.
    Never raises to the loop: a refused press is answered with its reason and
    the card is left as it was, which is the honest state.
    """
    from trendrelay_api.integrations import telegram

    callback = update.get("callback")
    if not callback:
        return None
    original = ""
    try:
        with session_factory() as session:
            outcome = decide(session, callback)
        toast = outcome
        text = outcome
        original = ""
    except PressRefused as refusal:
        outcome = str(refusal)
        toast = outcome
        text = ""
    if text:
        telegram.settle_button(
            callback["id"], chat_id=str(callback.get("chat_id")),
            message_id=callback.get("message_id"), text=text, toast=toast,
        )
    else:
        # Refused: the card stays as it is, and only the toast says why.
        telegram.settle_button(
            callback["id"], chat_id=str(callback.get("chat_id")),
            message_id=None, text=original, toast=toast,
        )
    return outcome


def poll_once(session_factory: Any, *, timeout: int = 0) -> int:
    """Read the presses since the last poll and act on each. Returns how many."""
    from trendrelay_api.integrations import telegram

    offset = telegram.read_offset()
    updates = telegram.fetch_updates(offset, timeout=timeout)
    handled = 0
    for update in updates:
        try:
            if handle_update(session_factory, update) is not None:
                handled += 1
        except Exception as error:  # noqa: BLE001 - one press must not stop the rest
            print(f"Telegram press not handled: {error}", flush=True)
        # Advanced per update, so a crash mid-batch does not replay a press
        # that was already carried out.
        telegram.write_offset(int(update["update_id"]) + 1)
    return handled


def poll_forever(session_factory: Any, *, stop: threading.Event | None = None) -> None:
    """The worker's Telegram loop: wait for a press, decide it, wait again.

    Long-polled, so a press is answered within a second rather than on the
    next minute's tick; asleep while the tool is not set up, so a machine that
    never sends never asks Telegram anything.
    """
    from trendrelay_api.integrations import telegram

    while stop is None or not stop.is_set():
        if not telegram.ready():
            time.sleep(RETRY_SECONDS)
            continue
        try:
            poll_once(session_factory, timeout=POLL_SECONDS)
        except telegram.TelegramUnavailable as error:
            print(f"Telegram approvals paused: {error}", flush=True)
            time.sleep(RETRY_SECONDS)
        except Exception as error:  # noqa: BLE001 - the loop must not die here
            print(f"Telegram approvals loop failed: {error}", flush=True)
            time.sleep(RETRY_SECONDS)

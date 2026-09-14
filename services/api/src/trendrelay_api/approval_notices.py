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


def _when(at: datetime | None, zone: str | None) -> str:
    """The due time as the workspace keeps time, short enough for a chat."""
    if at is None:
        return ""
    try:
        local = at.astimezone(ZoneInfo(zone)) if zone else at
    except (ValueError, KeyError):
        local = at
    return local.strftime("%a %d %b, %H:%M")


def _excerpt(caption: str) -> str:
    text = (caption or "").strip()
    if len(text) > CAPTION_CHARS:
        text = text[: CAPTION_CHARS - 1].rstrip() + "…"
    return text


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
    campaign_name: str, item: dict[str, Any], *, zone: str | None = None,
) -> str:
    """One held post as a message: where, when, the words, and why it waits."""
    where = html.escape(str(item.get("destination") or "an account"))
    when = _when(item.get("at"), zone)
    lines = [
        f"<b>{html.escape(campaign_name)}</b> · {where}"
        + (f" · {html.escape(when)}" if when else ""),
        "",
    ]
    caption = _excerpt(str(item.get("caption") or ""))
    if caption:
        lines.extend([html.escape(caption), ""])
    reason = str(item.get("reason") or "").strip()
    if reason:
        lines.append(f"<i>{html.escape(reason)}</i>")
    return "\n".join(lines).rstrip()


def card_buttons(
    execution_id: str, campaign_id: str, *, test: bool = False,
) -> list[list[dict[str, str]]]:
    """The inbox's choices, as buttons: decide here, or go and look.

    A test card has the same buttons so the hand learns the layout, but each
    of them only answers that it was the test.
    """
    verbs = (TEST, TEST, TEST) if test else (APPROVE, DISMISS, APPROVE_NOW)
    second_row = [{"label": "🚀 Approve and post now", "callback": f"{verbs[2]}:{execution_id}"}]
    link = app_link(campaign_id)
    if link:
        second_row.append({"label": "↗ Open in app", "url": link})
    return [
        [
            {"label": "✅ Approve", "callback": f"{verbs[0]}:{execution_id}"},
            {"label": "🚫 Dismiss", "callback": f"{verbs[1]}:{execution_id}"},
        ],
        second_row,
    ]


def _media_of(item: dict[str, Any]) -> tuple[list[str], str | None]:
    """The post's pictures, or its video, as the card will show them."""
    images = [str(path) for path in (item.get("image_paths") or []) if path]
    video = str(item.get("video_path") or "") or None
    return images, video


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
    sent = 0
    left_out: list[str] = []
    try:
        for item in held[:CARDS_PER_PASS]:
            images, video = _media_of(item)
            outcome = telegram.send_card(
                card_text(name, item, zone=zone),
                buttons=card_buttons(str(item["execution_id"]), autopilot.campaign_id),
                images=images, video=video,
            )
            left_out.extend(outcome.get("skipped") or [])
            sent += 1
        rest = len(held) - sent
        if rest > 0:
            link = app_link(autopilot.campaign_id)
            telegram.send_message(
                f"<b>{html.escape(name)}</b> · {rest} more post{'' if rest == 1 else 's'} "
                "waiting in the inbox.",
                buttons=[[{"label": "↗ Open in app", "url": link}]] if link else None,
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
            "reason": execution.held_reason,
            "image_paths": list(execution.image_paths or []),
            "video_path": execution.media_path,
        }
        for execution in executions
        if execution.state == "proposed"
    ]
    return announce_held(session, autopilot, held)


# --- a press ---------------------------------------------------------------------


class PressRefused(ValueError):
    """The press was understood and not acted on; the reason is for the presser."""


def _decision(data: str) -> tuple[str, str]:
    verb, _, execution_id = (data or "").partition(":")
    if verb not in (APPROVE, APPROVE_NOW, DISMISS, TEST) or not execution_id:
        raise PressRefused("That button is not one of ours.")
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

    chat = telegram.configured_chat_id()
    if not chat or str(callback.get("chat_id")) != chat:
        raise PressRefused("This chat is not the one TrendRelay was set up with.")
    allowed = telegram.approver_ids()
    presser = str((callback.get("from") or {}).get("id") or "")
    if allowed and presser not in allowed:
        raise PressRefused("You are not on the approvers list for this workspace.")
    verb, execution_id = _decision(callback.get("data", ""))
    if verb == TEST:
        return f"This was the test card. Nothing was decided. Pressed by {html.escape(_who(callback))}."

    execution = session.get(PublicationExecution, execution_id)
    if execution is None:
        raise PressRefused("That post is no longer here.")
    if execution.state != "proposed":
        raise PressRefused(f"Already decided in the app: it is {execution.state}.")
    autopilot = session.scalar(
        select(CampaignAutopilot).where(
            CampaignAutopilot.campaign_id == execution.campaign_id,
            CampaignAutopilot.workspace_id == execution.workspace_id,
        )
    )
    if autopilot is None:
        raise PressRefused("That campaign no longer runs on its own.")
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
        return f"🚫 Dismissed by {html.escape(who)}"
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
        return f"⚠️ Approved by {html.escape(who)}, but it could not be queued: " \
            f"{html.escape(execution.error or 'see the app')}"
    return (f"🚀 Approved and posting now" if publish_now else "✅ Approved") \
        + f" by {html.escape(who)}"


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

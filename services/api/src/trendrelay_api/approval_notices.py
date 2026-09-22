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
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trendrelay_api import approval_words as words
from trendrelay_api.autopilot_models import CampaignAutopilot
from trendrelay_api.config import get_settings
from trendrelay_api.foundation import audit
from trendrelay_api.models import Campaign, Workspace
from trendrelay_api.publication_models import (
    CampaignApprovalNotice,
    PublicationExecution,
)

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
    # A due time read back from SQLite has lost its zone, and a naive time
    # converts as though it were the machine's local time - so a post held
    # for 11:00 UTC was announced as due at 11:00 in Bangkok, seven hours
    # early. Everything stored is UTC; say so before converting.
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
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
    overdue: bool = False, decided: str | None = None,
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

    `decided` is the other end of the same card, after a press: it stands in
    for the waiting line rather than adding to it, so the message a decision
    leaves behind is still the post - who, where, the words, the notes - with
    what happened to it in place of why it was waiting, not a single line
    that has forgotten what it was about. Passed in already built and already
    escaped, the way `approve_execution`'s own `{who}` already is by the time
    it reaches here - escaping it twice would show `&amp;` for an `&`.
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
    if decided is not None:
        if decided:
            lines.append(f"<i>{decided}</i>")
        return "\n".join(lines).rstrip()
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


def _pairing(item: dict[str, Any]) -> tuple[str, str] | None:
    """The clip-and-account a card is about, or None when it cannot be keyed.

    An execution's queue item and destination are both nullable - a one-off
    Publish post has neither - and a held post missing either is one this
    cannot remember. Those fall through to being announced, which is the
    behaviour there has always been for a case a campaign does not produce.
    """
    queue_item_id = str(item.get("queue_item_id") or "")
    destination_id = str(item.get("destination_id") or "")
    if not queue_item_id or not destination_id:
        return None
    return queue_item_id, destination_id


def notice_for(
    session: Session, campaign_id: str, pairing: tuple[str, str],
) -> CampaignApprovalNotice | None:
    """The card already sent for this clip and account, if one was."""
    queue_item_id, destination_id = pairing
    return session.scalar(
        select(CampaignApprovalNotice).where(
            CampaignApprovalNotice.campaign_id == campaign_id,
            CampaignApprovalNotice.queue_item_id == queue_item_id,
            CampaignApprovalNotice.destination_id == destination_id,
        )
    )


def settle_notice(session: Session, execution: PublicationExecution) -> None:
    """Mark the card for this execution's post decided, so nothing re-asks.

    A dismissed post is proposed again within the minute and a failed one on
    the next pass; both would otherwise be announced as though nobody had
    ever been asked. The notice stays behind to say somebody was.
    """
    if not (execution.campaign_id and execution.queue_item_id and execution.destination_id):
        return
    notice = notice_for(
        session, execution.campaign_id,
        (execution.queue_item_id, execution.destination_id),
    )
    if notice is not None and notice.settled_at is None:
        notice.settled_at = datetime.now(UTC)
        notice.updated_at = notice.settled_at


def clear_notice(session: Session, execution: PublicationExecution) -> None:
    """Forget the card, because this post has now actually gone out.

    The clip returns to the back of the rotation and its next outing is a new
    posting decision, not the same one asked again - so that one is announced,
    and the memory that would have suppressed it is spent here. The only
    place a notice is ever removed.
    """
    if not (execution.campaign_id and execution.queue_item_id and execution.destination_id):
        return
    notice = notice_for(
        session, execution.campaign_id,
        (execution.queue_item_id, execution.destination_id),
    )
    if notice is not None:
        session.delete(notice)


def _repoint(
    session: Session, notice: CampaignApprovalNotice, item: dict[str, Any],
    autopilot: CampaignAutopilot, *, language: str,
) -> None:
    """Aim the card already in the chat at the post's newest execution.

    The card the approver is looking at carries the id of the execution it was
    sent for, and that row may be long settled - failed, or dismissed - with
    the post since frozen again as a new one. Pressing it would answer "that
    post is gone" for a post that is sitting in the inbox right now. So the
    buttons are rewritten to decide the execution that actually exists, which
    is what makes a press land however many times the post has been re-frozen.

    Nothing is sent. A card that cannot be edited keeps the buttons it had and
    says so in the app, which is better than a second card.
    """
    from trendrelay_api.integrations import telegram

    execution_id = str(item.get("execution_id") or "")
    if not execution_id or execution_id == notice.execution_id:
        return
    if notice.chat_id and notice.message_id is not None and notice.settled_at is None:
        telegram.edit_card(
            chat_id=notice.chat_id, message_id=notice.message_id,
            buttons=card_buttons(execution_id, autopilot.campaign_id, language=language),
        )
    notice.execution_id = execution_id
    notice.updated_at = datetime.now(UTC)


def announce_held(
    session: Session, autopilot: CampaignAutopilot, held: list[dict[str, Any]],
) -> str:
    """Send each held post to the campaign's approver on Telegram, as a card.

    Once per post, and only the first time. A post whose card already went out
    is not announced again however often it is frozen again - a failed
    delivery and a dismissal both settle the execution and free the queue
    item, so the next pass re-proposes the identical post as a new row, and
    announcing that was one post drawing four cards in three hours. The card
    already in the chat is re-pointed at the new execution instead, so the
    press still lands; see `_repoint`.

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

    # Which of these the approver has already been shown. Settled here, before
    # a single card goes out, so a chat that fails halfway cannot re-ask about
    # the ones it did reach.
    fresh: list[dict[str, Any]] = []
    known = 0
    for item in held:
        pairing = _pairing(item)
        notice = notice_for(session, autopilot.campaign_id, pairing) if pairing else None
        if notice is None:
            fresh.append(item)
            continue
        known += 1
        _repoint(session, notice, item, autopilot, language=language)

    if not fresh:
        return (
            f"Already announced on Telegram: {known} post(s) were asked about before."
            if known else ""
        )
    _notes_for(session, fresh[:CARDS_PER_PASS])
    sent = 0
    left_out: list[str] = []
    try:
        for item in fresh[:CARDS_PER_PASS]:
            images, video = _media_of(item)
            execution_id = str(item["execution_id"])
            outcome = telegram.send_card(
                card_text(name, item, zone=zone, language=language),
                buttons=card_buttons(
                    execution_id, autopilot.campaign_id, language=language,
                ),
                images=images, video=video,
            )
            left_out.extend(outcome.get("skipped") or [])
            # Written the moment the card exists, and flushed, so a failure on
            # the next one cannot leave a card in the chat that nothing
            # remembers - which is exactly how a post gets asked about twice.
            #
            # In a savepoint because the worker's tick and a request that
            # announces - the campaign's Telegram switch - can be in this loop
            # at the same time, and the unique index is what decides which of
            # them owns the pairing. Losing that race means the other one has
            # the notice, so this one has nothing left to record; it must not
            # take the rest of the plan down for it.
            pairing = _pairing(item)
            if pairing:
                try:
                    with session.begin_nested():
                        session.add(CampaignApprovalNotice(
                            workspace_id=autopilot.workspace_id,
                            campaign_id=autopilot.campaign_id,
                            queue_item_id=pairing[0],
                            destination_id=pairing[1],
                            execution_id=execution_id,
                            chat_id=str(outcome.get("chat_id") or "") or None,
                            message_id=outcome.get("message_id"),
                        ))
                except IntegrityError:
                    print(
                        "Telegram card recorded by another pass: "
                        f"{pairing[0]} to {pairing[1]}",
                        flush=True,
                    )
            sent += 1
        rest = len(fresh) - sent
        if rest > 0:
            link = app_link(autopilot.campaign_id)
            telegram.send_message(
                f"<b>{html.escape(name)}</b> · "
                + html.escape(words.say(language, "more_waiting", count=rest)),
                buttons=(
                    [[{"label": words.say(language, "open_app"), "url": link}]]
                    if link else None
                ),
            )
    except telegram.TelegramUnavailable as error:
        if sent:
            return f"Announced {sent} of {len(fresh)} on Telegram; then: {error}"
        return f"Not announced on Telegram: {error}"
    note = f"Announced {sent} post{'' if sent == 1 else 's'} on Telegram."
    if known:
        note = f"{note} {known} was already asked about."
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
            # The pairing the announcement is remembered by, so a post the
            # chat has already been shown is not shown again by the switch.
            "destination_id": execution.destination_id,
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


def announce_unannounced(
    session: Session, autopilot: CampaignAutopilot, *, skip: set[str] | None = None,
) -> str:
    """Send the card for any held post the chat has never been shown.

    A card is sent the moment a post is frozen, and that was the only moment
    it was ever sent: when Telegram could not be reached just then, the run's
    note said so once and the post sat in the inbox with nobody told. Three
    posts were frozen that way on the evening of 22 September - the send
    failed, and the note that said so was overwritten seven minutes later by
    the campaign pausing itself - and the approver never heard of them.

    The notice is the memory of what was announced, so its absence is the
    list of what was not. This walks that list every pass, which is what
    turns one failed send into a delay rather than a silence; the unique
    index on the notice is what keeps a retry from ever sending twice. `skip`
    names the posts this pass has already tried, so a chat that is down is
    asked once per pass rather than twice. Posts with no pairing to remember
    them by are left alone: announced once at the freeze, as always, because
    a sweep could not tell them from ones it had already sent.
    """
    waiting = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.campaign_id == autopilot.campaign_id,
            PublicationExecution.state == "proposed",
            PublicationExecution.queue_item_id.is_not(None),
            PublicationExecution.destination_id.is_not(None),
        ).order_by(PublicationExecution.scheduled_at.asc())
    ).all()
    missing = [
        execution for execution in waiting
        if execution.id not in (skip or set())
        and notice_for(
            session, autopilot.campaign_id,
            (execution.queue_item_id or "", execution.destination_id or ""),
        ) is None
    ]
    if not missing:
        return ""
    return announce_executions(session, autopilot, missing)


def announce_overdue(
    session: Session, autopilot: CampaignAutopilot, *, now: datetime | None = None,
) -> str:
    """Say on the post's own card that its due time has passed, once.

    `announce_held` sends a card the moment a post is frozen for a person -
    usually well ahead of its own due time, since a campaign holds the next
    slot in front of somebody before the clock gets there. A card sent early
    says nothing once the clock catches up to it, and the app never says a
    held post is late.

    This used to be a second message, and a second message is the thing a
    post must never draw: the reminder was remembered on the execution, the
    execution is replaced every time a delivery fails or somebody skips, and
    a replaced execution re-armed it. So the one new fact - that time ran out
    - is written onto the card that is already in the chat, in place, above
    the campaign's own name. One post, one message, edited as the post's
    situation changes.

    Remembered on the notice rather than the execution, for the same reason
    the announcement is: the notice is the thing that survives the post being
    frozen again.

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
    marked = 0
    for execution in overdue:
        if not (execution.queue_item_id and execution.destination_id):
            continue
        notice = notice_for(
            session, autopilot.campaign_id,
            (execution.queue_item_id, execution.destination_id),
        )
        # No card to write on, already said, or already decided by somebody.
        if (
            notice is None
            or notice.overdue_notified_at is not None
            or notice.settled_at is not None
            or not notice.chat_id
            or notice.message_id is None
        ):
            continue
        item = {
            "destination": execution.destination_label,
            "caption": execution.caption,
            "at": execution.scheduled_at,
            "platform": execution.platform,
            "reason": execution.held_reason,
            "reason_code": execution.held_reason_code,
        }
        # The buttons go on again pointing at the execution that is held now,
        # which is what `_repoint` would have done anyway - an edit carrying
        # no markup would strip them and leave a card nobody can answer.
        changed = telegram.edit_card(
            chat_id=notice.chat_id, message_id=notice.message_id,
            text=card_text(name, item, zone=zone, language=language, overdue=True),
            buttons=card_buttons(
                execution.id, autopilot.campaign_id, language=language,
            ),
        )
        if not changed:
            continue
        notice.execution_id = execution.id
        notice.overdue_notified_at = moment
        notice.updated_at = moment
        marked += 1
    if not marked:
        return ""
    return f"Marked {marked} overdue post{'' if marked == 1 else 's'} on its Telegram card."


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


def decide(session: Session, callback: dict[str, Any]) -> tuple[str, str]:
    """Carry out one press. Returns (the toast, what the message says now).

    The same path the inbox takes: `approve_execution` with the post's own
    checks, or the dismissal that frees the slot. Refused with a reason when
    the post is no longer held - decided in the app meanwhile, or already
    pressed - so the second press reads as a fact rather than a failure.

    The two returned strings answer different questions. The toast is what
    Telegram pops over the chat for a second - a decision and who made it,
    short enough to read in passing. The message is what the card says from
    now on, and a press used to write the toast's own short sentence there
    too: "✅ Approved by @ana" replacing a card that had a caption, notes,
    and a destination on it, so a decided post could no longer say what it
    had been. The message keeps all of that and swaps only the line that
    said the post was waiting - see `card_text`'s `decided`.
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
        # No execution behind this one to read the rest of the card from -
        # the test card is built once, from nothing this function has access
        # to (`telegram_setup.launch_action`) - so the toast is also all the
        # message becomes, same as it always was.
        line = words.say("en", "test_answer", who=html.escape(_who(callback)))
        return line, line

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

    def settled(toast: str) -> tuple[str, str]:
        """The card as it reads once this decision has landed."""
        campaign = session.get(Campaign, execution.campaign_id)
        workspace = session.get(Workspace, execution.workspace_id)
        notes = ""
        if execution.queue_item_id:
            from trendrelay_api.autopilot_models import CampaignQueueItem

            queued = session.get(CampaignQueueItem, execution.queue_item_id)
            notes = (queued.context if queued else "") or ""
        item = {
            "destination": execution.destination_label,
            "caption": execution.caption,
            "at": execution.scheduled_at,
            "platform": execution.platform,
            "notes": notes,
        }
        message = card_text(
            campaign.name if campaign else "Campaign", item,
            zone=workspace.timezone if workspace else None,
            language=language, decided=toast,
        )
        return toast, message

    if verb == DISMISS:
        execution.state = "cancelled"
        execution.reconciled_at = utc_now()
        execution.updated_at = utc_now()
        # A dismissed post frees its slot and its queue item, so the next pass
        # proposes it straight back. Settling the notice is what keeps that
        # from arriving as a second card asking the question again.
        settle_notice(session, execution)
        audit(
            session, None, execution.workspace_id, autopilot.created_by,
            "campaign.exception_dismissed", "campaign", execution.campaign_id or "",
            {"execution_id": execution.id, "stop_proposing": False, "post_paused": False,
             **identity},
        )
        session.commit()
        return settled(words.say(language, "dismissed_by", who=html.escape(who)))
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
        return settled(words.say(
            language, "approved_but_failed", who=html.escape(who),
            reason=html.escape(execution.error or words.say(language, "see_the_app")),
        ))
    return settled(words.say(
        language, "approved_now_by" if publish_now else "approved_by", who=html.escape(who),
    ))


#: How many times a press is carried out again before it is given up on.
#: The database is SQLite and the worker writes to it from the job loop at the
#: same time, so "database is locked" is the ordinary reason a decision does
#: not land on the first go - and a press that is silently dropped is the
#: worst outcome there is, because the presser has no way to know.
PRESS_ATTEMPTS = 3
#: How long to wait between those attempts.
PRESS_RETRY_SECONDS = 0.6


def handle_update(session_factory: Any, update: dict[str, Any]) -> str | None:
    """One update from the poll: decide it, and rewrite the card to say so.

    Returns the toast - the short decision sentence, not the longer message
    the card itself now carries (see `decide`) - or None for an update with
    no press in it. Never raises to the loop: a refused press is answered
    with its reason and the card is left as it was, which is the honest
    state.

    A press is either carried out or answered. Those are the only two
    outcomes, because the presser is standing in a chat waiting for the
    button to stop spinning, and an unanswered press looks exactly like a
    stopped worker. So a decision that fails for a reason that is nobody's
    decision - the database busy under the job loop, most often - is tried
    again before it is given up on, and giving up on it still says so in the
    chat rather than only in a log the presser will never read.
    """
    from trendrelay_api.integrations import telegram

    callback = update.get("callback")
    if not callback:
        return None
    toast, text = "", ""
    for attempt in range(PRESS_ATTEMPTS):
        try:
            with session_factory() as session:
                toast, text = decide(session, callback)
            break
        except PressRefused as refusal:
            # Understood and declined: the post is gone, already decided, or
            # not finished. Trying again would refuse it again.
            toast, text = str(refusal), ""
            break
        except Exception as error:  # noqa: BLE001 - the presser has to hear something
            if attempt + 1 < PRESS_ATTEMPTS:
                time.sleep(PRESS_RETRY_SECONDS)
                continue
            print(f"Telegram press not carried out: {error}", flush=True)
            toast, text = words.say("en", "press_failed"), ""
    # Always answered, whichever way it went: the callback is what clears the
    # button, and an unanswered one spins until Telegram times it out.
    try:
        telegram.settle_button(
            callback["id"], chat_id=str(callback.get("chat_id")),
            # Refused or failed: the card stays as it is, buttons and all, so
            # the decision can still be made. Only a settled press rewrites it.
            message_id=callback.get("message_id") if text else None,
            text=text, toast=toast,
        )
    except telegram.TelegramUnavailable as error:
        # The decision landed; only the chat did not hear it. Said here
        # because a card still showing buttons over a decided post is the
        # visible symptom, and a second press on it reads as "already
        # decided" rather than doing anything twice.
        print(f"Telegram press carried out but the card was not updated: {error}", flush=True)
    return toast


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
        # that was already carried out. `handle_update` retries and answers
        # for itself, so by here the press has had its chances and replaying
        # it would only risk carrying it out twice.
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

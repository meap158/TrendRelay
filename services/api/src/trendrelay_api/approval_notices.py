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
from secrets import token_hex
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

#: How many posts one grouped card carries, and how much of each one's caption
#: it quotes.
#:
#: Six, because the card is read on a phone and the buttons are underneath it:
#: past six the decision is below the fold, which is the problem grouping is
#: here to solve rather than a smaller version of it. A moment holding more
#: than six is split into cards of six.
GROUP_LIMIT = 6
#: Shorter than a single card's excerpt, and for the same reason: six of these
#: are one message, and the card has to stay one screen.
GROUP_CAPTION_CHARS = 180

#: What a press means. Short, because Telegram allows 64 bytes of data and
#: the execution id takes forty of them.
APPROVE = "apr"
APPROVE_NOW = "now"
DISMISS = "dis"
#: Approve every post still waiting on one grouped card. Carries the card's
#: group id rather than a list of executions, which would not fit.
APPROVE_ALL = "all"
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


def group_card_text(
    campaign_name: str, items: list[dict[str, Any]], *, zone: str | None = None,
    language: str = "en", pictures: int = 0,
) -> str:
    """The posts one pass is holding, as one message.

    Numbered, because the buttons under it are numbered: the pairs of buttons
    Telegram can show carry no room for an account's name, so the number is
    what ties a press to a post and it has to be in front of the reader.

    Each post keeps what a single card gives it - when it goes out, which
    network, which account, its own words - and loses the room for its working
    notes: six sets of notes is a message nobody reaches the end of, and the
    app is one press away for the post that needs them.

    A post already decided says so on its own line, in place of nothing, so a
    card that has been half answered reads as half answered rather than as a
    card with fewer buttons than lines. A post whose own time has passed wears
    the clock on its line, for the same reason and in the same place.
    """
    times = {_when(item.get("at"), zone, language) for item in items} - {""}
    # One time for all of them belongs in the heading; several belong on the
    # lines they are about, which is the ordinary case - the planner spreads a
    # campaign's accounts across its posting times rather than posting them all
    # at once.
    shared = times.pop() if len(times) == 1 else ""
    waiting = [item for item in items if not item.get("decided")]
    heading = words.say(
        language, "group_waiting", count=len(waiting) or len(items),
    )
    lines: list[str] = [
        f"<b>{html.escape(campaign_name)}</b> · {html.escape(heading)}"
        + (f" · {html.escape(shared)}" if shared else ""),
        "",
    ]
    for index, item in enumerate(items, start=1):
        network = words.platform_name(str(item.get("platform") or ""))
        where = str(item.get("destination") or "an account")
        if network and network.casefold() in where.casefold():
            network = ""
        when = "" if shared else _when(item.get("at"), zone, language)
        head = f"<b>{index}.</b> " + " · ".join(
            html.escape(part) for part in (when, network, where) if part
        )
        if item.get("overdue") and not item.get("decided"):
            head = f"{head} ⏰"
        decided = item.get("decided")
        lines.append(f"{head} — <i>{decided}</i>" if decided else head)
        caption = _shorten(str(item.get("caption") or ""), GROUP_CAPTION_CHARS)
        if caption:
            lines.append(html.escape(caption))
        lines.append("")
    # One reason, when the posts share it, which they nearly always do: they
    # were frozen by the same pass under the same authority. Said once at the
    # bottom rather than six times in the middle.
    reasons = {
        words.say(language, str(item.get("reason_code") or ""))
        if str(item.get("reason_code") or "") in words.WORDS["en"]
        else str(item.get("reason") or "").strip()
        for item in items
    } - {""}
    if len(reasons) == 1:
        lines.append(f"<i>{html.escape(reasons.pop())}</i>")
    if pictures:
        lines.append(html.escape(words.say(language, "group_pictures")))
    return "\n".join(lines).rstrip()


def group_buttons(
    items: list[dict[str, Any]], campaign_id: str, group_id: str, *,
    language: str = "en",
) -> list[list[dict[str, str]]]:
    """A pair of buttons per post still waiting, and one for all of them.

    Numbered to match the message's own list. "Approve and post now" is not
    here: three buttons a post would be eighteen on a card, and the choice it
    offers - change this post's own timing - is a per-post decision that
    belongs on the post's own card or in the app.

    "Approve all" only appears while more than one is waiting, because with one
    left it is the button above it under a second name.
    """
    rows: list[list[dict[str, str]]] = []
    waiting = 0
    for index, item in enumerate(items, start=1):
        if item.get("decided"):
            continue
        waiting += 1
        execution_id = str(item.get("execution_id") or "")
        rows.append([
            {
                "label": f"{words.say(language, 'approve')} {index}",
                "callback": f"{APPROVE}:{execution_id}",
            },
            {
                "label": f"{words.say(language, 'dismiss')} {index}",
                "callback": f"{DISMISS}:{execution_id}",
            },
        ])
    last: list[dict[str, str]] = []
    if waiting > 1:
        last.append({
            "label": words.say(language, "approve_all"),
            "callback": f"{APPROVE_ALL}:{group_id}",
        })
    link = app_link(campaign_id)
    if link:
        last.append({"label": words.say(language, "open_app"), "url": link})
    if last:
        rows.append(last)
    return rows


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


#: Telegram errors that prove the card never left this machine.
#:
#: The distinction is the whole of whether a post may be announced twice, and
#: it is the one `campaign_runner.UNREACHED_MARKERS` draws before sending a
#: post again: a refused connection spoke to nobody, so no card exists and the
#: next pass may send it. Anything that got as far as a conversation is the
#: other case - `send_card` posts the message and then reads the answer, and
#: Telegram only answers once it has the message, so a read that timed out or
#: a connection reset mid-answer is very likely a card sitting in the chat.
#: Those keep their claim and are never sent again.
#:
#: When in doubt a card is not sent twice. Silence is visible in the inbox and
#: in the run note; a chat that asks the same question every minute taught the
#: approver to ignore it, which is the more expensive mistake.
NEVER_LEFT_MARKERS = (
    "could not be reached",
    "could not reach",
    "connection refused",
    "failed to establish",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "no route to host",
    "network is unreachable",
    "is not set up",
    "no chat is saved",
)


def never_delivered(error: str) -> bool:
    """Whether this failure proves no card was created. See `NEVER_LEFT_MARKERS`."""
    text = (error or "").casefold()
    return any(marker in text for marker in NEVER_LEFT_MARKERS)


def pairing_of(
    execution_id: str, queue_item_id: str | None, destination_id: str | None,
) -> tuple[str, str]:
    """The key a card is remembered by: this clip, to this account.

    An execution's queue item and destination are both nullable - a one-off
    Publish post has neither - and a post missing either used to be announced
    with nothing recorded at all, which is not "the old behaviour" but the
    original bug with a narrower door: nothing claimed it, so every pass sent
    it again, for as long as it stayed held.

    So a post that cannot be keyed by its pairing is keyed by itself. That is
    a weaker promise - a new execution for the same post is a new key, where a
    real pairing would have recognised it - but it is bounded, and the thing
    being prevented is a card a minute.
    """
    fallback = f"execution:{execution_id}"
    return (queue_item_id or fallback, destination_id or fallback)


def _pairing(item: dict[str, Any]) -> tuple[str, str]:
    """`pairing_of` for one entry of the list a planning pass hands over."""
    return pairing_of(
        str(item.get("execution_id") or ""),
        str(item.get("queue_item_id") or "") or None,
        str(item.get("destination_id") or "") or None,
    )


def _cards_of(
    held: list[dict[str, Any]], *, grouped: bool,
) -> list[list[dict[str, Any]]]:
    """Which posts share a card, in the order they will be sent.

    One pass, one card. What fills a chat is not a single moment - the planner
    takes one post per posting time across the whole campaign, so two accounts
    never wait for the same minute - it is the pass: a run freezes its whole
    horizon at once, so a campaign with eight slots in the next day and a half
    puts eight cards in the chat inside a second, one for each of them.

    Those eight are the approval queue as it stands, which is the thing the
    approver is actually looking at, so they arrive as one card that lists them
    - with each post's own time on its own line, because the times differ and
    the time is half of what is being agreed to.

    Order is kept: the posts arrive in the order the pass held them, which is
    by posting time, which is the order the timeline shows.
    """
    if not grouped:
        return [[item] for item in held]
    # Past the limit the buttons are below the fold, which is the problem this
    # exists to solve rather than a smaller version of it.
    return [
        held[start:start + GROUP_LIMIT]
        for start in range(0, len(held), GROUP_LIMIT)
    ]


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
    if not execution.campaign_id:
        return
    notice = notice_for(
        session, execution.campaign_id,
        pairing_of(execution.id, execution.queue_item_id, execution.destination_id),
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
    if not execution.campaign_id:
        return
    notice = notice_for(
        session, execution.campaign_id,
        pairing_of(execution.id, execution.queue_item_id, execution.destination_id),
    )
    if notice is not None:
        session.delete(notice)


def _repoint(
    session: Session, notice: CampaignApprovalNotice, item: dict[str, Any],
    autopilot: CampaignAutopilot, *, language: str,
    regroup: set[str] | None = None,
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
    notice.execution_id = execution_id
    notice.updated_at = datetime.now(UTC)
    if not (notice.chat_id and notice.message_id is not None and notice.settled_at is None):
        return
    if notice.group_id:
        # A grouped card is rewritten whole, once, after every row on it has
        # been aimed: the row that moved is one line of it, the numbers have to
        # keep meaning what they meant, and a card rendered halfway through the
        # loop would carry the executions the rest of the loop is replacing.
        if regroup is not None:
            regroup.add(notice.group_id)
        return
    telegram.edit_card(
        chat_id=notice.chat_id, message_id=notice.message_id,
        buttons=card_buttons(execution_id, autopilot.campaign_id, language=language),
    )


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
    regroup: set[str] = set()
    for item in held:
        pairing = _pairing(item)
        notice = notice_for(session, autopilot.campaign_id, pairing)
        if notice is None:
            fresh.append(item)
            continue
        known += 1
        _repoint(session, notice, item, autopilot, language=language, regroup=regroup)
    # The grouped cards whose rows moved, rewritten once each now that every
    # row on them has been aimed at the post that is actually held.
    for group_id in sorted(regroup):
        session.flush()
        notices = group_of(session, group_id)
        lead = next(
            (
                notice for notice in notices
                if notice.chat_id and notice.message_id is not None
                and notice.settled_at is None
            ),
            None,
        )
        if lead is None or lead.chat_id is None or lead.message_id is None:
            continue
        text, buttons = group_card(session, group_id, language)
        telegram.edit_card(
            chat_id=lead.chat_id, message_id=lead.message_id,
            text=text, buttons=buttons,
        )

    if not fresh:
        return (
            f"Already announced on Telegram: {known} post(s) were asked about before."
            if known else ""
        )
    # One card per posting time where the campaign asked for that, one card
    # per post where it did not.
    cards = _cards_of(fresh, grouped=autopilot.approvals_grouped)[:CARDS_PER_PASS]
    # Working notes go on a card that has room for them, which a grouped one
    # has not - see `group_card_text`.
    _notes_for(session, [card[0] for card in cards if len(card) == 1])

    # The claim, taken and committed before a single card exists.
    #
    # This used to be written after the send returned, which is the wrong way
    # round by exactly the failure the chat actually has. `send_card` posts
    # the message and then reads the answer, and the read is what times out
    # when the connection drops - so Telegram had the card, the send raised,
    # nothing was recorded, and the next pass a minute later sent it again.
    # A flaky link turned one held post into a card per tick, which is the
    # duplicate that survived remembering the announcement at all.
    #
    # Committed rather than flushed for the same reason: a notice that is
    # still inside the tick's transaction is a notice a crash, a rollback or
    # a failed later step can take away while the card stays in the chat.
    # After this line the claim outlives anything that happens next, and the
    # worst case is a post that was never announced and never will be - which
    # is in the inbox, visible, and is the side of the trade the operator
    # asked for.
    sent = 0
    announced = 0
    attempted = 0
    left_out: list[str] = []
    try:
        for card in cards:
            claimed: list[tuple[dict[str, Any], CampaignApprovalNotice]] = []
            for item in card:
                pairing = _pairing(item)
                notice = CampaignApprovalNotice(
                    workspace_id=autopilot.workspace_id,
                    campaign_id=autopilot.campaign_id,
                    queue_item_id=pairing[0],
                    destination_id=pairing[1],
                    execution_id=str(item["execution_id"]),
                )
                try:
                    # In a savepoint because the worker's tick and a request
                    # that announces - the campaign's Telegram switch - can be
                    # here at the same time, and the unique index is what
                    # decides which of them owns the pairing. Losing that race
                    # means the other one is sending the card, so this one must
                    # not send a second.
                    with session.begin_nested():
                        session.add(notice)
                except IntegrityError:
                    known += 1
                    print(
                        f"Telegram card already claimed by another pass:"
                        f" {pairing[0]} to {pairing[1]}",
                        flush=True,
                    )
                    continue
                claimed.append((item, notice))
            # Every post on this card belonged to another pass. There is
            # nothing left to ask about, so there is no card.
            if not claimed:
                continue
            session.commit()
            attempted += len(claimed)
            items = [item for item, _notice in claimed]
            # A grouped card is one message about several posts, so the group
            # is what a press on "approve all" has to find and what a decision
            # on one post has to rewrite. Only for a card that carries more
            # than one: a single post's card is exactly what it was.
            group_id = token_hex(8) if len(claimed) > 1 else None
            if group_id:
                for _item, notice in claimed:
                    notice.group_id = group_id
                # One picture per post, in the card's own order, so the album
                # above the message can be read against its list. A carousel's
                # remaining cards and a clip are what the app is for.
                images = [
                    str(item["image_paths"][0]) for item in items
                    if item.get("image_paths")
                ]
                video = None
                text = group_card_text(
                    name, items, zone=zone, language=language, pictures=len(images),
                )
                buttons = group_buttons(
                    items, autopilot.campaign_id, group_id, language=language,
                )
            else:
                images, video = _media_of(items[0])
                text = card_text(name, items[0], zone=zone, language=language)
                buttons = card_buttons(
                    str(items[0]["execution_id"]), autopilot.campaign_id,
                    language=language,
                )
            try:
                outcome = telegram.send_card(
                    text, buttons=buttons, images=images, video=video,
                )
            except telegram.TelegramUnavailable as error:
                # Whether the claim is given back turns on one question, and
                # it is the same question publishing asks before sending a
                # post twice: did this reach Telegram at all? A refused
                # connection reached nothing, so the card does not exist and
                # the next pass may send it. A read that timed out is the
                # other case entirely - Telegram answers only once it has the
                # message, so the answer is what a dropped link loses, and
                # sending again is how one held post became a card a minute.
                if never_delivered(str(error)):
                    for _item, notice in claimed:
                        session.delete(notice)
                    session.commit()
                raise
            left_out.extend(outcome.get("skipped") or [])
            # Which message the card is, so it can be re-pointed, marked
            # overdue and settled. Committed per card: the claim already stops
            # a second send, and this is what makes the first one editable.
            for _item, notice in claimed:
                notice.chat_id = str(outcome.get("chat_id") or "") or None
                notice.message_id = outcome.get("message_id")
                notice.updated_at = datetime.now(UTC)
            session.commit()
            sent += 1
            announced += len(claimed)
        # What this pass had no room for, which is not the same as what it
        # did not send: a pairing another pass claimed while this one was
        # working is a post already being announced, and counting it here
        # sent "1 more waiting in the inbox" about a card that was on its way.
        rest = max(0, len(fresh) - sum(len(card) for card in cards))
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
        # The ones that did not go out are not tried again, and the note says
        # so rather than leaving it to be discovered. Their claim is already
        # committed, because a send that raises may still have delivered the
        # card - Telegram answers after it has the message, and the answer is
        # what a dropped connection loses. Sending again to be sure is how one
        # post became a card a minute, so the post waits in the inbox instead.
        held_back = attempted - announced if not never_delivered(str(error)) else 0
        tail = (
            f" {held_back} post(s) may already have reached the chat and are"
            " not sent again; they wait in the inbox."
            if held_back else ""
        )
        if announced:
            return f"Announced {announced} of {len(fresh)} on Telegram; then: {error}{tail}"
        return f"Not announced on Telegram: {error}{tail}"
    if not sent and known:
        # Everything this pass had turned out to belong to another one.
        return f"Already announced on Telegram: {known} post(s) were asked about before."
    note = f"Announced {announced} post{'' if announced == 1 else 's'} on Telegram."
    if sent != announced:
        # Grouped, so the count of cards is not the count of posts and the note
        # would otherwise read as though five posts had gone out five times.
        note = (
            f"Announced {announced} posts on Telegram, "
            f"on {sent} card{'' if sent == 1 else 's'}."
        )
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
        if notice.group_id:
            # Every post on a grouped card shares the moment that has passed,
            # A grouped card holds several moments, so the clock goes on the
            # line whose time has passed rather than over the whole card - and
            # the card is rewritten whole, once, with every row on it
            # remembering that it was said. Without that the next tick would say
            # it again for the row it happened to read first.
            notice.execution_id = execution.id
            text, buttons = group_card(session, notice.group_id, language, now=moment)
            if not telegram.edit_card(
                chat_id=notice.chat_id, message_id=notice.message_id,
                text=text, buttons=buttons,
            ):
                continue
            for row in group_of(session, notice.group_id):
                if row.overdue_notified_at is None:
                    row.overdue_notified_at = moment
                    row.updated_at = moment
            marked += 1
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
    """The verb pressed and what it is about - an execution, or a whole card."""
    verb, _, target = (data or "").partition(":")
    if verb not in (APPROVE, APPROVE_NOW, DISMISS, TEST, APPROVE_ALL) or not target:
        raise PressRefused(words.say("en", "not_ours"))
    return verb, target


def _what_became_of(
    session: Session, execution: PublicationExecution, language: str,
) -> str:
    """What happened to this post, where it was decided, and by whom.

    A card outlives the question it asks. The same post can be approved in the
    app a minute after the card went out, or approved from another card, or
    published by the campaign itself - and the card in the chat still shows
    two live buttons either way. Somebody presses one and deserves a better
    answer than "already decided", which was both vague and, for every
    decision actually made on Telegram, untrue.

    Read from the audit log, because that is where a decision records who made
    it and how it arrived - the Telegram path writes `via: telegram` and the
    presser's handle for exactly this reason. A post that reached its engine
    without anybody deciding it - an autonomous campaign, or one whose
    delivery has already failed - has no such event, and is described by its
    own state instead.
    """
    from sqlalchemy import String, cast

    from trendrelay_api.models import AuditEvent, UserProfile

    event = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.action.in_((
                "campaign.exception_approved", "campaign.exception_dismissed",
            )),
            # The execution is inside the JSON detail rather than in a column
            # of its own. Compared as text because the column is portable JSON
            # and an execution id cannot contain the quotes this looks for.
            cast(AuditEvent.detail, String).like(f'%"{execution.id}"%'),
        )
        .order_by(AuditEvent.created_at.desc())
    )
    if event is None:
        # Nobody decided it; it simply got on with itself.
        return words.say(language, f"became_{_shape_of(execution.state)}")
    detail = event.detail or {}
    approved = event.action.endswith("approved")
    if approved and detail.get("publish_now"):
        action = words.say(language, "was_approved_now")
    elif approved:
        action = words.say(language, "was_approved")
    else:
        action = words.say(language, "was_dismissed")
    if detail.get("via") == "telegram":
        where = words.say(
            language, "from_a_card", who=str(detail.get("telegram_user") or "?"),
        )
    else:
        # Punctuation rather than a word, so the name reads the same in every
        # language the card speaks and the sentence needs no grammar for it.
        person = session.get(UserProfile, event.actor_user_id or "")
        named = (person.email if person else "") or ""
        where = words.say(
            language, "in_the_app", who=f" · {named}" if named else "",
        )
    return f"{action} {where}"


def group_of(session: Session, group_id: str) -> list[CampaignApprovalNotice]:
    """The posts that share one card, in the order the card lists them.

    By when each row was claimed, which is the order the card was built in -
    the numbers beside the buttons are what tie a press to a post, so they have
    to mean the same thing on every later edit as they did when it was sent.
    """
    return list(session.scalars(
        select(CampaignApprovalNotice)
        .where(CampaignApprovalNotice.group_id == group_id)
        .order_by(
            CampaignApprovalNotice.created_at.asc(),
            CampaignApprovalNotice.id.asc(),
        )
    ).all())


def _group_view(
    session: Session, notices: list[CampaignApprovalNotice], language: str,
    *, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """What each post on a grouped card is, and what has become of it.

    Read from the executions the rows point at rather than from anything kept
    on the card, for the reason the notice exists at all: a held post is frozen
    again whenever a delivery fails or somebody skips it, and the card is
    re-pointed at the row that is actually held. So the card is rendered from
    what is held now, every time, and a post decided since the last render says
    so instead of showing buttons that would answer for it.
    """
    moment = now or datetime.now(UTC)
    items: list[dict[str, Any]] = []
    for notice in notices:
        execution = session.get(PublicationExecution, notice.execution_id)
        if execution is None:
            continue
        # Read back from SQLite a due time has lost its zone, and a naive one
        # compares as though it were local - which is the same trap `_when`
        # documents. Everything stored is UTC; say so before comparing.
        due = execution.scheduled_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        items.append({
            # Its own time having passed is a fact about the post rather than
            # about the card, and a card holding six moments has six answers to
            # it - so the clock goes on the line it belongs to.
            "overdue": due is not None and due < moment,
            "execution_id": execution.id,
            "destination_id": execution.destination_id,
            "destination": execution.destination_label,
            "platform": execution.platform,
            "caption": execution.caption,
            "at": execution.scheduled_at,
            "reason": execution.held_reason,
            "reason_code": execution.held_reason_code,
            "image_paths": list(execution.image_paths or []),
            "queue_item_id": execution.queue_item_id,
            "decided": (
                None if execution.state == "proposed"
                else _what_became_of(session, execution, language)
            ),
        })
    return items


def group_card(
    session: Session, group_id: str, language: str, *, now: datetime | None = None,
) -> tuple[str, list[list[dict[str, str]]] | None]:
    """A grouped card as it reads now: its words, and the buttons still live.

    None for the buttons when nothing on it is waiting any more, which is what
    strips them: a card whose every post has been decided is a record, and a
    record with buttons invites a press that can only be refused.
    """
    notices = group_of(session, group_id)
    if not notices:
        return "", None
    items = _group_view(session, notices, language, now=now)
    campaign = session.get(Campaign, notices[0].campaign_id)
    workspace = session.get(Workspace, notices[0].workspace_id)
    text = group_card_text(
        campaign.name if campaign else "Campaign", items,
        zone=workspace.timezone if workspace else None,
        language=language,
        pictures=len([item for item in items if item.get("image_paths")]),
    )
    waiting = [item for item in items if not item.get("decided")]
    if not waiting:
        return text, None
    return text, group_buttons(
        items, notices[0].campaign_id, group_id, language=language,
    )


def _group_for(session: Session, execution: PublicationExecution) -> str | None:
    """Which grouped card this post is on, if it is on one."""
    if not execution.campaign_id:
        return None
    notice = notice_for(
        session, execution.campaign_id,
        pairing_of(execution.id, execution.queue_item_id, execution.destination_id),
    )
    return notice.group_id if notice else None


def _shape_of(state: str) -> str:
    """The three ends a post can come to, for a reader who is not a database."""
    if state in ("published", "measured"):
        return "posted"
    if state in ("failed", "uncertain"):
        return "not_posted"
    return "settled"


def _who(callback: dict[str, Any]) -> str:
    person = callback.get("from") or {}
    handle = person.get("username")
    return f"@{handle}" if handle else (person.get("name") or f"user {person.get('id')}")


def _approve_group(
    session: Session, group_id: str, callback: dict[str, Any],
) -> tuple[str, str]:
    """Approve every post still waiting on one grouped card.

    The button exists because the common answer to a card holding four posts is
    the same answer four times, and pressing four pairs on a phone is how an
    approver ends up not pressing any. It is deliberately not the only way to
    answer: each post keeps its own pair beside it, because the posts are
    different posts.

    One post refusing does not stop the others. A post that cannot be approved
    - its engine switched off, its media gone - keeps its buttons and its line
    on the card, and the toast counts what actually went.
    """
    from trendrelay_api.campaign_autopilot_api import _omit_unsupported_thread
    from trendrelay_api.campaign_runner import approve_execution

    notices = group_of(session, group_id)
    if not notices:
        raise PressRefused(words.say("en", "gone"))
    autopilot = session.scalar(
        select(CampaignAutopilot).where(
            CampaignAutopilot.campaign_id == notices[0].campaign_id,
            CampaignAutopilot.workspace_id == notices[0].workspace_id,
        )
    )
    if autopilot is None:
        raise PressRefused(words.say("en", "no_autopilot"))
    language = card_language(autopilot)
    who = _who(callback)
    identity = {
        "via": "telegram",
        "telegram_user_id": str((callback.get("from") or {}).get("id") or ""),
        "telegram_user": who,
    }
    approved = 0
    refusals: list[str] = []
    for notice in notices:
        execution = session.get(PublicationExecution, notice.execution_id)
        if execution is None or execution.state != "proposed":
            continue
        omitted = _omit_unsupported_thread(execution)
        try:
            approve_execution(session, autopilot, execution)
        except ValueError as error:
            # Left as it was, buttons and all: this one still needs a decision,
            # and the reason is about this post rather than about the press.
            session.rollback()
            refusals.append(str(error))
            continue
        audit(
            session, None, execution.workspace_id, autopilot.created_by,
            "campaign.exception_approved", "campaign", execution.campaign_id or "",
            {"execution_id": execution.id, "state": execution.state,
             "publish_now": False, "omitted_unsupported_replies": omitted,
             "approved_with_the_card": True, **identity},
        )
        approved += 1
        session.commit()
    if not approved:
        # Nothing was waiting, or nothing could go. The card is left exactly as
        # it is, because nothing about it has changed, and the presser hears the
        # reason.
        raise PressRefused(
            refusals[0] if refusals else words.say(language, "became_settled")
        )
    toast = words.say(
        language, "approved_all_by", count=approved, who=html.escape(who),
    )
    text, _buttons = group_card(session, group_id, language)
    return toast, text


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
    if verb == APPROVE_ALL:
        return _approve_group(session, execution_id, callback)

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
    who = _who(callback)
    identity = {
        "via": "telegram",
        "telegram_user_id": presser,
        "telegram_user": who,
    }

    def settled(toast: str) -> tuple[str, str]:
        """The card as it reads once this decision has landed.

        A grouped card is rewritten as the whole card: this post's line says
        what became of it and the posts beside it go on waiting, with their
        buttons. Only a card about one post is replaced by that post's own
        decided version.
        """
        group_id = _group_for(session, execution)
        if group_id:
            text, _buttons = group_card(session, group_id, language)
            return toast, text
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

    if execution.state != "proposed":
        # Answered already - here, or in the app, or by the campaign itself.
        #
        # This used to refuse the press with "Already decided in the app",
        # which is a guess and was wrong every time the decision had been made
        # from a card. It said nothing about what the decision was, and it
        # left the card standing with live buttons, so the same stale card
        # could be pressed again and answered the same unhelpful way.
        #
        # Now the press settles the card it was made on: what happened to the
        # post, where that was decided and by whom, in place of the line that
        # said it was waiting. Not a refusal - the presser asked a fair
        # question of a card that was out of date, and this is the answer.
        outcome = _what_became_of(session, execution, language)
        return settled(outcome)
    if autopilot is None:
        raise PressRefused(words.say("en", "no_autopilot"))

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


def surviving_buttons(
    session: Session, callback: dict[str, Any],
) -> list[list[dict[str, str]]] | None:
    """The buttons a card still needs after this press, or None to take them off.

    A card about one post has none: it has been decided, and buttons over a
    decided post invite a press that can only be refused. A grouped card has
    the pairs belonging to the posts nobody has answered yet - asked of the
    group as it stands now, so it is right whether the press decided one post
    or all of them.
    """
    try:
        verb, target = _decision(callback.get("data", ""))
    except PressRefused:
        return None
    if verb == TEST:
        return None
    group_id = target if verb == APPROVE_ALL else None
    if group_id is None:
        execution = session.get(PublicationExecution, target)
        if execution is None:
            return None
        group_id = _group_for(session, execution)
    if not group_id:
        return None
    notices = group_of(session, group_id)
    if not notices:
        return None
    autopilot = session.scalar(
        select(CampaignAutopilot).where(
            CampaignAutopilot.campaign_id == notices[0].campaign_id,
            CampaignAutopilot.workspace_id == notices[0].workspace_id,
        )
    )
    _text, buttons = group_card(
        session, group_id, card_language(autopilot) if autopilot else "en",
    )
    return buttons


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
    # What the card keeps after the press. Only a grouped card has anything
    # left: deciding one of its posts leaves the others waiting, and the
    # keyboard they are waiting with has to survive the edit.
    keep: list[list[dict[str, str]]] | None = None
    for attempt in range(PRESS_ATTEMPTS):
        try:
            with session_factory() as session:
                toast, text = decide(session, callback)
                keep = surviving_buttons(session, callback)
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
            text=text, toast=toast, buttons=keep,
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

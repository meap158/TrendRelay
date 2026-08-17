"""Collecting audience comments, and deciding which ones must reach a person.

The same boundary shape as measurement: a reader per provider where one can
honestly exist, an empty registry today because no current engine exposes
authenticated comment reading, and a collection loop that says which providers
it cannot ask instead of inventing an empty inbox that looks like silence.

Escalation is rules, not a model. A keyword class is crude, but it is
readable, testable, and errs toward a person seeing the message - which is the
correct direction for complaints, refunds, privacy, harassment, and anything
regulated. Sentiment is deliberately absent rather than guessed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.conversation_models import ConversationMessage

#: One reader per provider id, returning comment dicts for a published
#: execution: `{"remote_comment_id", "remote_post_id", "author_handle",
#: "text", "posted_at"}`. Empty on purpose; registration is the extension
#: point, and the bar for registering is the brief's: authenticated reading,
#: moderation, rate limits, deduplication, identity-safe endpoints.
PROVIDER_COMMENT_READERS: dict[str, Callable[..., list[dict[str, Any]]]] = {}

#: Words that route a message to a person, by class. Matched case-insensitively
#: as substrings, in English and Vietnamese because those are the audiences
#: this app posts to. Over-matching is the intended failure direction: a false
#: escalation costs a glance, a missed complaint costs a customer.
ESCALATION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("refund", (
        "refund", "money back", "charge back", "chargeback", "hoàn tiền",
        "trả lại tiền", "đòi lại tiền",
    )),
    ("complaint", (
        "scam", "fake", "broken", "never arrived", "did not arrive",
        "doesn't work", "does not work", "terrible", "worst",
        "lừa đảo", "hàng giả", "không nhận được", "kém chất lượng", "tệ quá",
    )),
    ("privacy", (
        "delete my", "remove my", "my face", "without my permission",
        "without consent", "xóa giúp", "xoá giúp", "mặt tôi", "không xin phép",
    )),
    ("harassment", (
        "kill you", "hate you", "ugly", "stupid", "idiot",
        "đồ ngu", "câm mồm", "biến đi",
    )),
    ("regulated", (
        "medical", "cure", "treatment", "diagnosis", "investment advice",
        "guaranteed returns", "chữa khỏi", "điều trị", "cam kết lợi nhuận",
    )),
    ("legal", (
        "lawyer", "lawsuit", "sue you", "legal action", "copyright",
        "luật sư", "kiện", "bản quyền",
    )),
)


def classify_escalation(text: str) -> tuple[str, str | None]:
    """The class a message escalates under, and the rule that matched.

    First match wins in declaration order, which puts money and complaints -
    the classes with a clock on them - ahead of the rest.
    """
    lowered = (text or "").casefold()
    for escalation_class, markers in ESCALATION_RULES:
        for marker in markers:
            if marker in lowered:
                return escalation_class, marker
    return "none", None


def reader_status() -> dict[str, Any]:
    """Which engines can be listened to, said plainly for a screen."""
    return {
        "readable_providers": sorted(PROVIDER_COMMENT_READERS),
        "note": (
            "No publishing engine currently exposes authenticated comment "
            "reading; the conversation inbox fills when one does."
            if not PROVIDER_COMMENT_READERS else None
        ),
    }


def ingest_comment(
    session: Session,
    *,
    workspace_id: str,
    provider: str,
    comment: dict[str, Any],
    campaign_id: str | None = None,
    execution_id: str | None = None,
    platform: str | None = None,
    now: datetime | None = None,
) -> ConversationMessage:
    """File one comment, once.

    Idempotent on the id the platform gave it: collection runs on a loop and
    the same comment arrives on every pass until it ages out of the window.
    Escalation is decided at ingestion and never silently downgraded - a rule
    change applies to new messages, not to a queue someone already triaged.
    """
    moment = now or datetime.now(UTC)
    remote_id = str(comment.get("remote_comment_id") or "").strip()
    if not remote_id:
        raise ValueError("A comment needs the id its platform gave it.")
    existing = session.scalar(
        select(ConversationMessage).where(
            ConversationMessage.provider == provider,
            ConversationMessage.remote_comment_id == remote_id,
        )
    )
    if existing:
        return existing
    text = str(comment.get("text") or "")[:4000]
    escalation_class, marker = classify_escalation(text)
    message = ConversationMessage(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        execution_id=execution_id,
        provider=provider,
        platform=platform,
        remote_post_id=(
            str(comment["remote_post_id"]) if comment.get("remote_post_id") else None
        ),
        remote_comment_id=remote_id,
        author_handle=(
            str(comment["author_handle"])[:200]
            if comment.get("author_handle") else None
        ),
        text=text,
        posted_at=comment.get("posted_at"),
        state="escalated" if escalation_class != "none" else "new",
        escalation_class=escalation_class,
        escalation_reason=(
            f"Matched {escalation_class!r} rule: {marker!r}." if marker else None
        ),
        collected_at=moment,
    )
    session.add(message)
    session.flush()
    return message


def collect_comments(
    session: Session, *, now: datetime | None = None
) -> dict[str, Any]:
    """Ask every readable provider for new comments on published posts.

    Free while the registry is empty, and honest about it: the providers that
    cannot be asked are named rather than reported as quiet audiences.
    """
    from trendrelay_api.publication_models import PublicationExecution

    moment = now or datetime.now(UTC)
    ingested = 0
    unreadable: set[str] = set()
    rows = session.scalars(
        select(PublicationExecution).where(
            PublicationExecution.state.in_(("published", "measured")),
            PublicationExecution.remote_post_ids != [],
        )
    ).all()
    for execution in rows:
        reader = PROVIDER_COMMENT_READERS.get(execution.provider or "")
        if reader is None:
            if execution.provider:
                unreadable.add(execution.provider)
            continue
        for comment in reader(execution) or []:
            message = ingest_comment(
                session,
                workspace_id=execution.workspace_id,
                provider=execution.provider or "",
                comment=comment,
                campaign_id=execution.campaign_id,
                execution_id=execution.id,
                platform=execution.platform,
                now=moment,
            )
            if message.collected_at == moment:
                ingested += 1
    return {"ingested": ingested, "unreadable_providers": sorted(unreadable)}

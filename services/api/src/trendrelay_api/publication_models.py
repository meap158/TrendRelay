"""One publication, from reservation to provider-confirmed fact.

The campaign bookkeeping used to have a single moment of truth: the instant a
publishing job was *created*, the queue item was stamped posted, its rest
interval started, and `times_posted` went up. Everything after that moment -
the provider refusing the post, the job dying, an ambiguous timeout - happened
to a record that had already been counted, so a failed post rested a clip for
a month and a retry risked posting twice.

`PublicationExecution` separates the two things that were conflated: the
*reservation* (this content, this account, this time - decided and frozen) and
the *publication* (the provider confirmed it exists). Only the second updates
rest intervals, rotation, and learning data.

The frozen inputs are the other half of the contract. What was previewed is
what is delivered: the exact media version by id and hash, the composed text,
the placement, the links, the destination as it was capable at the time. A
render that finishes after reservation does not change an execution already
frozen, and a file that goes missing fails the execution by name rather than
silently substituting the unedited original.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now

#: Every state an execution can be in. The full vocabulary is declared now so
#: later phases do not need a migration to start using the planning states;
#: the campaign runner currently moves through ready -> queued -> published /
#: failed / uncertain, with cancelled available to an operator.
#:
#: `uncertain` is its own terminal-until-reviewed state on purpose: a timeout
#: after the request went out means the post may exist, so it must neither be
#: counted as published nor retried as if nothing happened.
EXECUTION_STATES = (
    "proposed",
    "preparing",
    "ready",
    "reserved",
    "queued",
    "provider_accepted",
    "published",
    "measured",
    "failed",
    "uncertain",
    "cancelled",
    "paused",
)

#: States that still occupy their slot and their queue item. A failed or
#: cancelled execution frees both; an uncertain one keeps them held, because
#: the post may exist and re-planning the slot could double-post.
ACTIVE_STATES = frozenset(
    {"proposed", "preparing", "ready", "reserved", "queued", "provider_accepted"}
)
HOLDING_STATES = ACTIVE_STATES | {"uncertain"}

#: The states a provider outcome resolves to.
SETTLED_STATES = frozenset({"published", "measured", "failed", "uncertain", "cancelled"})

#: Why an execution failed, in classes coarse enough to act on. `auth` sends
#: somebody to reconnect the engine, `media` to the library, `uncertain` to the
#: provider's own dashboard; a class nobody can act on is not a class.
FAILURE_CLASSES = ("auth", "validation", "media", "provider", "uncertain")


class PublicationExecution(Base):
    __tablename__ = "publication_executions"
    __table_args__ = (
        CheckConstraint(
            "state IN ({})".format(",".join(f"'{state}'" for state in EXECUTION_STATES)),
            name="valid_publication_execution_state",
        ),
        # One execution per publishing job. Nullable, because an execution
        # exists before its job does; SQLite and PostgreSQL both allow many
        # NULLs under a unique index.
        Index("unique_publication_execution_job", "job_id", unique=True),
        # The double-planning guard reads this pair on every tick.
        Index("ix_publication_execution_slot", "destination_id", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("pubexec")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    #: Nullable, because a one-off Publish post is an execution too and has no
    #: campaign behind it. The campaign timeline filters on this.
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    queue_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_queue_items.id", ondelete="SET NULL"), index=True
    )
    destination_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_destinations.id", ondelete="SET NULL"), index=True
    )
    #: The durable publishing job this execution rode in, once one exists.
    #: A string rather than a foreign key: jobs are prunable history, and an
    #: execution outlives its job record.
    job_id: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    delivery: Mapped[str] = mapped_column(String(16), default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )

    # --- frozen media ------------------------------------------------------ #
    asset_id: Mapped[str | None] = mapped_column(String(64), index=True)
    #: The exact Library version chosen at freeze time, by id and content hash.
    #: Delivery verifies the file at `media_path` still matches `media_sha256`
    #: and fails by name when it does not - never substituting another cut.
    asset_version_id: Mapped[str | None] = mapped_column(String(64))
    media_path: Mapped[str] = mapped_column(String(1200))
    #: A carousel's pictures, in order. Empty for a video post, and
    #: `media_path` is empty for a carousel - the same shape the queue uses, so
    #: the two rows describe one post the same way.
    image_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    media_sha256: Mapped[str | None] = mapped_column(String(64))
    effect_ids: Mapped[list[str]] = mapped_column(JSON, default=list)

    # --- frozen content ---------------------------------------------------- #
    title: Mapped[str | None] = mapped_column(String(200))
    caption: Mapped[str] = mapped_column(String(6000), default="")
    first_comment: Mapped[str | None] = mapped_column(String(2000))
    thread: Mapped[list[str]] = mapped_column(JSON, default=list)
    placement: Mapped[str] = mapped_column(String(24), default="caption")
    #: Why TrendRelay chose this - destination rank, placement rule, product
    #: match - written at freeze time so the explanation cannot drift from the
    #: decision it explains.
    reason: Mapped[str] = mapped_column(String(1000), default="")

    # --- frozen commerce --------------------------------------------------- #
    offer_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: The links minted for this execution, one per offer and placement:
    #: ``[{"offer_id", "product_id", "placement", "tracking_link_id", "code"}]``.
    #: Per execution rather than per destination, so clicks can be attributed
    #: to one post - which is what makes clip, copy and time effects learnable.
    tracking_links: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # --- frozen destination ------------------------------------------------ #
    provider: Mapped[str | None] = mapped_column(String(32))
    integration_id: Mapped[str | None] = mapped_column(String(200))
    platform: Mapped[str | None] = mapped_column(String(24), index=True)
    destination_label: Mapped[str | None] = mapped_column(String(200))
    post_type: Mapped[str | None] = mapped_column(String(24))
    #: What the delivering engine said it could do when this was planned.
    capability_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # --- provider outcome -------------------------------------------------- #
    remote_post_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    permalinks: Mapped[list[str]] = mapped_column(JSON, default=list)
    failure_class: Mapped[str | None] = mapped_column(String(24))
    error: Mapped[str | None] = mapped_column(String(1000))
    #: Why a `proposed` execution is waiting for a person - the sentence the
    #: exception inbox shows. Empty on anything that was never held.
    held_reason: Mapped[str | None] = mapped_column(String(500))
    #: Timestamped native-performance snapshots, filled by the measurement
    #: phase. Declared now so measuring needs no migration.
    performance_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )

    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the provider outcome was read back and this record settled.
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)

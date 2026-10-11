"""Characters a creative draft can feature.

The pictures are ordinary Library images: the row only points at them, so a
character's portrait and sheet show up, and can be used, wherever any other
Library picture can. A built-in character is copied from the shipped cast
the first time a workspace lists its characters; `slug` is what makes that
copy happen once.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trendrelay_api.models import Base, new_id, utc_now


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generating','ready','failed')", name="valid_character_status",
        ),
        UniqueConstraint("workspace_id", "slug", name="unique_character_slug"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("character")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))
    #: Who they are, in the words a prompt uses.
    appearance: Mapped[str] = mapped_column(String(1000))
    #: What they wear on the sheet.
    outfit: Mapped[str] = mapped_column(String(500))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    #: generating while a new one is drawn, failed with `error` when it stopped.
    status: Mapped[str] = mapped_column(String(16), default="ready")
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    portrait_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    sheet_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_profiles.id"), nullable=True
    )
    #: Hidden from the picker. Kept, so a draft that named it still reads.
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)

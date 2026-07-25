"""Add immutable media library and creative intelligence."""

import sqlalchemy as sa
from alembic import op

revision = "20260726_0009"
down_revision = "20260726_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("media_kind", sa.String(12), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("platform", sa.String(80)),
        sa.Column("creator", sa.String(200)),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("caption", sa.String(5000)),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("audio_identifier", sa.String(300)),
        sa.Column("engagement", sa.JSON(), nullable=False),
        sa.Column("rights_status", sa.String(24), nullable=False),
        sa.Column("rights_basis", sa.String(2000)),
        sa.Column("original_path", sa.String(1200), nullable=False),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("video_codec", sa.String(80)),
        sa.Column("audio_codec", sa.String(80)),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(128),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "rights_status IN "
            "('owned','licensed','public-domain','reference-only','unknown','prohibited')",
            name="valid_media_rights_status",
        ),
        sa.CheckConstraint(
            "media_kind IN ('video','audio','image')",
            name="valid_media_kind",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "original_sha256",
            name="unique_workspace_media_sha256",
        ),
    )
    for column in (
        "workspace_id",
        "media_kind",
        "source_type",
        "platform",
        "creator",
        "rights_status",
        "original_sha256",
        "duration_ms",
        "collected_at",
        "created_at",
    ):
        op.create_index(f"ix_media_assets_{column}", "media_assets", [column])

    op.create_table(
        "media_asset_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(64),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_kind", sa.String(16), nullable=False),
        sa.Column("path", sa.String(1200), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "version_kind IN ('original','proxy','thumbnail','audio')",
            name="valid_media_version_kind",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "version_kind",
            "sha256",
            name="unique_asset_version_hash",
        ),
    )
    for column in (
        "workspace_id",
        "asset_id",
        "version_kind",
        "sha256",
        "created_at",
    ):
        op.create_index(
            f"ix_media_asset_versions_{column}",
            "media_asset_versions",
            [column],
        )

    op.create_table(
        "media_transcripts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(64),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("language", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("text", sa.String(100_000), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(128),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('speech','ocr')",
            name="valid_media_transcript_kind",
        ),
        sa.CheckConstraint(
            "status IN ('machine','reviewed')",
            name="valid_media_transcript_status",
        ),
    )
    for column in ("workspace_id", "asset_id", "kind", "status", "created_at"):
        op.create_index(
            f"ix_media_transcripts_{column}",
            "media_transcripts",
            [column],
        )

    op.create_table(
        "creative_analyses",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(64),
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spoken_hook", sa.String(1000)),
        sa.Column("text_hook", sa.String(1000)),
        sa.Column("call_to_action", sa.String(1000)),
        sa.Column("product_shown", sa.String(500)),
        sa.Column("creative_format", sa.String(160)),
        sa.Column("emotional_angle", sa.String(300)),
        sa.Column("structure_tags", sa.JSON(), nullable=False),
        sa.Column("scene_boundaries_ms", sa.JSON(), nullable=False),
        sa.Column("shot_count", sa.Integer()),
        sa.Column("average_shot_ms", sa.Integer()),
        sa.Column("product_reveal_ms", sa.Integer()),
        sa.Column("caption_density", sa.Integer()),
        sa.Column("storyboard", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("analyst_notes", sa.String(5000)),
        sa.Column(
            "created_by",
            sa.String(128),
            sa.ForeignKey("user_profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "asset_id",
            "version",
            name="unique_asset_analysis_version",
        ),
    )
    for column in (
        "workspace_id",
        "asset_id",
        "product_shown",
        "creative_format",
        "product_reveal_ms",
        "created_at",
    ):
        op.create_index(
            f"ix_creative_analyses_{column}",
            "creative_analyses",
            [column],
        )


def downgrade() -> None:
    op.drop_table("creative_analyses")
    op.drop_table("media_transcripts")
    op.drop_table("media_asset_versions")
    op.drop_table("media_assets")

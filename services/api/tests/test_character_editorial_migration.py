"""The recipe upgrade preserves draft prompts and admits the new carousel."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_editorial_recipe_migration_preserves_drafts_and_downgrades_safely() -> None:
    path = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
            / "20261015_0097_character_editorial_carousel.py")
    spec = importlib.util.spec_from_file_location("editorial_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    drafts = sa.Table(
        "product_creative_drafts", metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("recipe", sa.String(40), nullable=False),
        sa.Column("prompt", sa.String(4000), nullable=False),
        sa.Column("card_count", sa.Integer, nullable=False),
        sa.Column("staged_asset_ids", sa.JSON, nullable=False),
        sa.CheckConstraint(migration._check(migration._OLD),
                           name="valid_product_creative_recipe"),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(drafts.insert(), {
            "id": "old", "recipe": "sleepwear_lookbook", "prompt": "saved old prompt",
            "card_count": 3, "staged_asset_ids": ["asset_already_filed"],
        })
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(drafts.insert(), {
            "id": "new", "recipe": "character_editorial_carousel",
            "prompt": "saved editorial prompt", "card_count": 5,
            "staged_asset_ids": ["asset_card_1", "asset_card_2"],
        })
        assert connection.execute(sa.select(drafts.c.prompt).where(
            drafts.c.id == "old")).scalar_one() == "saved old prompt"
        checks = sa.inspect(connection).get_check_constraints("product_creative_drafts")
        assert "character_editorial_carousel" in next(
            check["sqltext"] for check in checks
            if check["name"] == "valid_product_creative_recipe"
        )
        migration.downgrade()
        restored = connection.execute(sa.select(drafts).where(
            drafts.c.id == "new")).mappings().one()
        assert restored["recipe"] == "sleepwear_lookbook"
        assert restored["prompt"] == "saved editorial prompt"
        assert restored["card_count"] == 5
        assert restored["staged_asset_ids"] == ["asset_card_1", "asset_card_2"]
        checks = sa.inspect(connection).get_check_constraints("product_creative_drafts")
        assert all("character_editorial_carousel" not in check["sqltext"] for check in checks)

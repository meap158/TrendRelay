"""Allow the two recipes written from single reference posts on 2026-10-25.

The recipes are data in
`trendrelay_api/product_creative_library/batch-2026-10-25.json`; the ids are
written out here so the migration keeps meaning what it meant.

Revision ID: 20261025_0102
Revises: 20261025_0101
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20261025_0102"
down_revision: str | None = "20261025_0101"
branch_labels: str | None = None
depends_on: str | None = None

_OLD = (
    "bed_flat_lay", "mannequin_transition", "mirror_selfie", "mannequin_walkaround",
    "styled_flat_lay", "flat_lay_reveal", "sleepwear_lookbook", "held_up_garment",
    "hikaru_lookbook", "ceiling_fan_transition", "hanger_rail_reveal", "hikaru_walk",
    "character_editorial_carousel",
    "floral_bow_set_portraits", "plain_wall_outfit_check", "cafe_street_magazine",
    "seamless_collection_lineup", "night_city_lookbook", "day_out_photo_dump",
    "fabric_macro_to_portrait", "faux_fur_salon_studio", "single_bloom_window_light",
    "plush_armchair_lounge", "morning_kitchen_lounge", "resort_sunset_story",
    "catalogue_colourway_card", "dress_form_arch_duo", "draped_garment_still_life",
    "profile_grid_checkerboard", "seamless_swipe_triptych", "window_knit_portrait_series",
    "studio_turn_showcase",
    "arched_door_coat_story", "frilled_cushion_sleep_set", "workroom_parcel_set",
    "card_then_try_on", "same_set_zoom_out", "pastel_seamless_pose_ladder",
    "heart_balloon_gift_set", "ivy_garden_door_series", "retro_prop_corner",
    "chair_display_colourway_run", "profile_grid_split_scene", "grid_detail_tile",
    "pouf_stool_seamless_series",
    # Third pass, batch 2026-10-23c (migration 20261025_0101).
    "autumn_leaf_coat_walk", "two_location_top_day", "bright_room_outfit_roundup",
    "flowering_garden_dress", "hotel_interior_walkthrough", "colour_wall_pop",
    "floor_curl_series", "editorial_cover_then_poses", "vintage_sideboard_scarf",
    "pink_tile_salon_chair", "curtain_corner_quiet_poses", "panelled_corner_sporty",
    "studio_cafe_table_knit", "pastel_corner_back_detail", "collection_card_then_looks",
    "sheer_drape_floor_props", "two_look_alternating", "winter_knit_window",
    "collection_sheet_card", "cake_stand_colourways", "prop_still_life_tile",
    "garden_twirl_video", "door_coat_reveal_video", "bed_lounge_settle_video",
    "fabric_touch_pullback_video", "corridor_walk_toward_video",
)
_BATCH = (
    "sunlit_floor_shadow_heap", "night_window_chair_mirror_selfie",
)
_NEW = _OLD + _BATCH


def _check(recipes: tuple[str, ...]) -> sa.TextClause:
    names = ",".join(f"'{name}'" for name in recipes)
    return sa.text(f"recipe IN ({names})")


def upgrade() -> None:
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_NEW))


def downgrade() -> None:
    # As in 0099 to 0101: a draft keeps its prompt, cards and links; only its
    # recipe name falls back to the closest hand-written one by kind.
    batch_ids = ",".join(f"'{name}'" for name in _BATCH)
    op.execute(
        "UPDATE product_creative_drafts SET recipe = CASE kind "
        "WHEN 'video' THEN 'mannequin_walkaround' "
        "WHEN 'image' THEN 'styled_flat_lay' "
        "ELSE 'sleepwear_lookbook' END "
        f"WHERE recipe IN ({batch_ids})"
    )
    with op.batch_alter_table("product_creative_drafts") as batch:
        batch.drop_constraint("valid_product_creative_recipe", type_="check")
        batch.create_check_constraint("valid_product_creative_recipe", _check(_OLD))

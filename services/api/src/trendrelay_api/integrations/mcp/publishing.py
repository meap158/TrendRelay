"""Publish composer context and validation, shared with the GUI services."""

from typing import Any

from pydantic import ConfigDict, Field, create_model
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.integrations import publishing
from trendrelay_api.integrations.publishing import MAX_CAROUSEL_IMAGES, PublishRequest
from trendrelay_api.integrations.publishing_matrix import capability_matrix

from .access import confirm_external_action, require_access

# Use the GUI field definitions so platforms, constraints and validators cannot
# acquire a second hand-maintained schema. The bound workspace and campaign
# provenance are never accepted from a standalone caller.
COMPOSER_EXCLUDED = {
    "workspace_id",
    "campaign_id",
    "queue_item_id",
    "destination_id",
    "confirm_external_action",
}
PublishComposer = create_model(
    "PublishComposer",
    __config__=ConfigDict(extra="forbid"),
    asset_ids=(list[str], Field(default_factory=list, max_length=MAX_CAROUSEL_IMAGES)),
    **{
        name: (field.rebuild_annotation(), field)
        for name, field in PublishRequest.model_fields.items()
        if name not in COMPOSER_EXCLUDED
    },
)


def library_media(
    session: Session,
    workspace_id: str,
    asset_ids: list[str],
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Use the Library GUI's latest completed cut, falling back to originals."""
    from trendrelay_api.campaign_scheduler import RENDERED_MEDIA_KINDS
    from trendrelay_api.media_models import MediaAssetVersion

    from .intake import _media_package, resolve_post_assets

    if not asset_ids or len(asset_ids) > MAX_CAROUSEL_IMAGES:
        raise ValueError(f"Select between 1 and {MAX_CAROUSEL_IMAGES} Library assets.")
    if fields.get("video_path") or fields.get("image_paths") or fields.get("media_url"):
        raise ValueError("Select Library asset_ids or explicit media paths/URL, not both.")
    assets = resolve_post_assets(session, workspace_id, asset_ids)
    media = _media_package(assets)
    latest = {}
    for version in session.scalars(
        select(MediaAssetVersion)
        .where(
            MediaAssetVersion.workspace_id == workspace_id,
            MediaAssetVersion.asset_id.in_([a.id for a in assets]),
            MediaAssetVersion.version_kind.in_(RENDERED_MEDIA_KINDS),
        )
        .order_by(MediaAssetVersion.created_at)
    ).all():
        latest[version.asset_id] = version.path
    if "image_paths" in media:
        media["image_paths"] = [latest.get(a.id, a.original_path) for a in assets]
    else:
        media["video_path"] = latest.get(assets[0].id, assets[0].original_path)
    return {
        **fields,
        "video_path": "",
        "image_paths": [],
        **media,
        "asset_id": assets[0].id,
    }


def composer_request(
    workspace_id: str,
    request: dict[str, Any],
    *,
    session: Session | None = None,
) -> PublishRequest:
    supplied = request.get("workspace_id", workspace_id)
    if supplied != workspace_id:
        raise ValueError("The publishing request belongs to another workspace.")
    if any(request.get(field) for field in ("campaign_id", "queue_item_id", "destination_id")):
        raise ValueError("Use campaign tools for campaign posts and their provenance.")
    fields = dict(request)
    asset_ids = fields.pop("asset_ids", [])
    if asset_ids:
        if session is None:
            raise ValueError("Library selection requires a workspace session.")
        fields = library_media(session, workspace_id, asset_ids, fields)
    return PublishRequest.model_validate({**fields, "workspace_id": workspace_id})


def get_publishing_context(workspace_id: str) -> dict[str, Any]:
    """Configuration and capabilities without probing accounts or returning keys."""
    return {
        "workspace_id": workspace_id,
        "connection": publishing.connection_status(probe=False),
        "capabilities": capability_matrix(),
    }


def preview_publishing(
    session: Session,
    workspace_id: str,
    request: dict[str, Any],
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    from trendrelay_api.publishing_api import _with_media_credit

    body = composer_request(workspace_id, request, session=session)
    if confirmed:
        require_access(session, workspace_id, {"owner", "editor", "approver"}, governed=True)
    return {
        "preview": publishing.preview_publish(
            _with_media_credit(session, body),
            validate_remote=confirmed,
        ),
        "remote_validation_enabled": confirmed,
    }


def list_publishing_accounts(
    session: Session, workspace_id: str, *, confirmed: bool = False
) -> dict[str, Any]:
    confirm_external_action(confirmed)
    require_access(session, workspace_id, {"owner", "approver"}, governed=True)
    return publishing.discover_all_integrations()


def list_publishing_boards(
    session: Session,
    workspace_id: str,
    provider: str,
    integration_id: str,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    from trendrelay_api import publishing_api

    confirm_external_action(confirmed)
    actor = require_access(session, workspace_id, {"owner", "editor", "approver"}, governed=True)
    return publishing_api.publishing_account_boards(
        workspace_id,
        provider,
        integration_id,
        publishing_api.ExternalConfirmation(confirm_external_action=True),
        actor,
        session,
    )


def submit_publishing(
    session: Session, workspace_id: str, request: dict[str, Any], *, confirmed: bool = False
) -> dict[str, Any]:
    """Send via the GUI's durable job path, including its claim/idempotency rules.

    Runs synchronously in the MCP tool's worker thread. The durable job is
    created before delivery, so an interrupted client can recover its result
    through the job reads rather than submitting a different post.
    """
    from trendrelay_api.publishing_api import _with_media_credit

    confirm_external_action(confirmed)
    require_access(session, workspace_id, {"owner", "approver"}, governed=True)
    body = composer_request(workspace_id, request, session=session).model_copy(
        update={"confirm_external_action": True}
    )
    job = publishing.create_publish_job(_with_media_credit(session, body))
    publishing.run_publish_job(job["id"])
    return get_publishing_job(workspace_id, job["id"])


def list_publishing_jobs(workspace_id: str, limit: int = 20) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    return {"jobs": publishing.list_publish_jobs(workspace_id, limit=limit)}


def get_publishing_job(workspace_id: str, job_id: str) -> dict[str, Any]:
    try:
        job = publishing.publish_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise ValueError("Publishing job not found.") from error
    if job.get("workspace_id") != workspace_id:
        raise ValueError("Publishing job not found.")
    return {"job": job}

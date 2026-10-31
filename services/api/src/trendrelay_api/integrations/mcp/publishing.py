"""Publish composer context and validation, shared with the GUI services."""

from typing import Any

from sqlalchemy.orm import Session

from trendrelay_api.integrations import publishing
from trendrelay_api.integrations.publishing import PublishRequest
from trendrelay_api.integrations.publishing_matrix import capability_matrix


def get_publishing_context(workspace_id: str) -> dict[str, Any]:
    """Configuration and capabilities without probing accounts or returning keys."""
    return {
        "workspace_id": workspace_id,
        "connection": publishing.connection_status(probe=False),
        "capabilities": capability_matrix(),
    }


def preview_publishing(
    session: Session, workspace_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    from trendrelay_api.publishing_api import _with_media_credit

    supplied = request.get("workspace_id", workspace_id)
    if supplied != workspace_id:
        raise ValueError("The publishing request belongs to another workspace.")
    # Campaign provenance is assigned by Campaigns, never by a standalone caller.
    if any(request.get(field) for field in ("campaign_id", "queue_item_id", "destination_id")):
        raise ValueError("Use campaign tools for campaign posts and their provenance.")
    body = PublishRequest.model_validate({**request, "workspace_id": workspace_id})
    return {"preview": publishing.preview_publish(_with_media_credit(session, body))}


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

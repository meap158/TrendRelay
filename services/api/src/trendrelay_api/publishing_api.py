"""Authenticated, role-gated social publishing API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.env_store import EnvWriteError
from trendrelay_api.foundation import membership, require_role
from trendrelay_api.integrations import media_hosting, posting_slots
from trendrelay_api.integrations.publishing import (
    PublishRequest,
    connection_status,
    create_publish_job,
    discover_all_integrations,
    discover_integrations,
    list_publish_jobs,
    preview_publish,
    publish_job,
    run_publish_job,
    save_provider_credentials,
    set_active_provider,
    test_provider,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/publishing", tags=["publishing"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


class ExternalConfirmation(BaseModel):
    confirm_external_action: bool = False
    provider: str | None = None


class ProviderSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=40)


class ProviderCredentials(ProviderSelection):
    values: dict[str, str] = Field(default_factory=dict)
    confirm_external_action: bool = False
    activate: bool = False


class HostingCredentials(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    confirm_external_action: bool = False


def validate_workspace(body: PublishRequest, workspace_id: str) -> None:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")


def require_local_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Credential changes are local-machine only.")


@router.get("/connection")
def publishing_connection(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"connection": connection_status()}


@router.post("/providers/credentials")
def save_credentials(
    workspace_id: str,
    body: ProviderCredentials,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Saving credentials requires confirmation.")
    try:
        result = save_provider_credentials(body.provider, body.values)
        if body.activate:
            result |= set_active_provider(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status()}


@router.post("/media-hosting/credentials")
def save_media_hosting_credentials(
    workspace_id: str,
    body: HostingCredentials,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Store object-storage settings so fetch-only engines can reach local media."""
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Saving credentials requires confirmation.")
    try:
        result = media_hosting.save_credentials(body.values)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status(probe=False)}


@router.post("/media-hosting/probe")
def probe_media_hosting(
    workspace_id: str,
    body: ExternalConfirmation,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Try the whole path a published clip takes through object storage.

    Confirmed like the other outward calls, and for a stronger reason than most:
    this one writes a small object into the bucket, because fetching it back
    through the public URL is the only way to prove an engine could.
    """
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Testing storage requires confirmation.")
    return {"probe": media_hosting.probe()}


class SlotEntry(BaseModel):
    weekday: int = Field(default=-1, ge=-1, le=6)
    time: str = Field(min_length=3, max_length=5)


class SlotUpdate(BaseModel):
    slots: list[SlotEntry] = Field(default_factory=list, max_length=40)


@router.get("/slots")
def publishing_slots(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """This workspace's posting times, plus the presets it can start from."""
    membership(session, workspace_id, user.id)
    return {
        "slots": posting_slots.list_slots(workspace_id),
        "presets": posting_slots.preset_payload(),
    }


# POST rather than PUT: the API is exposed to the local browser under a CORS
# policy that allows GET and POST only, and every other route follows that.
@router.post("/slots")
def save_publishing_slots(
    workspace_id: str,
    body: SlotUpdate,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        slots = posting_slots.replace_slots(
            workspace_id, [entry.model_dump() for entry in body.slots]
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"slots": slots, "presets": posting_slots.preset_payload()}


@router.post("/providers/test")
def test_provider_credentials(
    workspace_id: str,
    body: ProviderSelection,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        return {"provider": test_provider(body.provider)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/providers/activate")
def activate_provider(
    workspace_id: str,
    body: ProviderSelection,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_local_request(request)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    try:
        result = set_active_provider(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result, "connection": connection_status()}


@router.post("/integrations")
def publishing_integrations(
    workspace_id: str,
    body: ExternalConfirmation,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Discovery requires explicit confirmation.")
    try:
        return discover_integrations(body.provider)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/integrations/all")
def publishing_integrations_all(
    workspace_id: str,
    body: ExternalConfirmation,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Connected accounts across every configured engine.

    One post can address destinations on several engines, so the page needs all
    of them together rather than whichever engine happens to be active.
    """
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Discovery requires explicit confirmation.")
    return discover_all_integrations()


@router.post("/preview")
def preview_publishing(
    workspace_id: str,
    body: PublishRequest,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    validate_workspace(body, workspace_id)
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    try:
        return {"preview": preview_publish(body)}
    except (PermissionError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/jobs", status_code=202)
def submit_publishing(
    workspace_id: str,
    body: PublishRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    validate_workspace(body, workspace_id)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    try:
        job = create_publish_job(body)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_publish_job, job["id"])
    return {"job": job}


@router.get("/jobs")
def publishing_jobs(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"jobs": list_publish_jobs(workspace_id)}


@router.get("/jobs/{job_id}")
def get_publishing_job(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    try:
        job = publish_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Publishing job not found.") from error
    if job["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Publishing job not found.")
    return {"job": job}

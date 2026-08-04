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
from trendrelay_api.integrations.publishing import (
    PublishRequest,
    connection_status,
    create_publish_job,
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

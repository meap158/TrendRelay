"""Authenticated, role-gated social publishing API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.foundation import membership, require_role
from trendrelay_api.integrations.postiz import (
    PublishRequest,
    create_publish_job,
    discover_integrations,
    list_publish_jobs,
    preview_publish,
    publish_job,
    run_publish_job,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/publishing", tags=["publishing"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


class ExternalConfirmation(BaseModel):
    confirm_external_action: bool = False


def validate_workspace(body: PublishRequest, workspace_id: str) -> None:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")


@router.post("/postiz/integrations")
def postiz_integrations(
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
        return discover_integrations()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/postiz/preview")
def preview_postiz_publish(
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


@router.post("/postiz/jobs", status_code=202)
def submit_postiz_publish(
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


@router.get("/postiz/jobs")
def postiz_jobs(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"jobs": list_publish_jobs(workspace_id)}


@router.get("/postiz/jobs/{job_id}")
def get_postiz_job(
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

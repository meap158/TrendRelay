"""Authenticated production API for OpenMontage preflights and local renders."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.integrations.openmontage import (
    ProductionApproval,
    ProductionRequest,
    approve_proposal,
    create_proposal,
    get_production,
    list_pipelines,
    list_productions,
)
from trendrelay_api.integrations.openmontage_runtime import (
    RenderRequest,
    create_render_job,
    list_render_jobs,
    render_job,
    run_render_job,
    runtime_status,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/studio", tags=["production"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


def _workspace_matches(body_workspace: str, workspace_id: str) -> None:
    if body_workspace != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")


@router.get("/status")
def status(workspace_id: str, user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"runtime": runtime_status(), "pipelines": list_pipelines()}


@router.get("/productions")
def productions(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {
        "productions": list_productions(workspace_id),
        "renders": list_render_jobs(workspace_id),
    }


@router.post("/productions", status_code=201)
def propose(
    workspace_id: str,
    body: ProductionRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    _workspace_matches(body.workspace_id, workspace_id)
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    try:
        production = create_proposal(body)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "production.proposed",
        "production",
        production["id"],
    )
    return {"production": production}


@router.post("/productions/{production_id}/approval")
def approve(
    workspace_id: str,
    production_id: str,
    body: ProductionApproval,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    try:
        existing = get_production(production_id)
        if existing["workspace_id"] != workspace_id:
            raise FileNotFoundError(production_id)
        production = approve_proposal(
            production_id,
            ProductionApproval(
                approved_by=user.id,
                confirm_external_action=body.confirm_external_action,
            ),
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Production not found.") from error
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session, request, workspace_id, user.id, "production.approved", "production", production_id
    )
    return {"production": production}


@router.post("/renders", status_code=202)
def render(
    workspace_id: str,
    body: RenderRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    _workspace_matches(body.workspace_id, workspace_id)
    require_role(membership(session, workspace_id, user.id), {"owner", "approver"})
    require_governed_assurance(user)
    try:
        job = create_render_job(body)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_render_job, job["id"])
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "production.render_submitted",
        "render",
        job["id"],
        {"production_id": body.production_id, "clip_count": len(body.segments)},
    )
    return {"job": job}


@router.get("/renders/{job_id}")
def get_render(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    try:
        job = render_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Render not found.") from error
    if job["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Render not found.")
    return {"job": job}

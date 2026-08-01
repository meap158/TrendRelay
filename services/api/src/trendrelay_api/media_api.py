"""Authenticated media acquisition API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.integrations.douyin import (
    DownloadRequest,
    clear_download_history,
    create_download_job,
    download_job,
    list_download_jobs,
    provider_status,
    reconcile_downloads_to_library,
    resume_download_job,
    start_connection,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/media", tags=["media"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


class ConnectionRequest(BaseModel):
    confirm_external_action: bool = False
    force_refresh: bool = False


class LibrarySyncRequest(BaseModel):
    confirm_external_action: bool = False


class ClearDownloadsRequest(BaseModel):
    confirm_external_action: bool = False


class ResumeDownloadRequest(BaseModel):
    confirm_external_action: bool = False
    from_saved_files: bool = False


@router.get("/status")
def media_status(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {
        "douyin": provider_status(),
        "tiktok": {
            "installed": False,
            "active": False,
            "reason": "A reviewed TikTok acquisition provider is not installed.",
        },
    }


@router.post("/douyin/connection", status_code=202)
def connect_douyin(
    workspace_id: str,
    body: ConnectionRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Douyin login is local-machine only.")
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Opening the Douyin login browser requires explicit confirmation.",
        )
    connection = start_connection(force_refresh=body.force_refresh)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.douyin_connection_started",
        "provider_connection",
        "douyin-downloader",
        {"state": connection["state"]},
    )
    return {"connection": connection}


@router.get("/downloads")
def downloads(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"jobs": list_download_jobs(workspace_id)}


@router.post("/downloads/clear")
def clear_downloads(
    workspace_id: str,
    body: ClearDownloadsRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Clearing download history requires confirmation.",
        )
    result = clear_download_history(workspace_id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_history_cleared",
        "workspace",
        workspace_id,
        {
            "removed_count": len(result["removed_job_ids"]),
            "preserved_active_count": len(result["preserved_active_job_ids"]),
            "preserved_on_disk_count": len(result["preserved_on_disk_job_ids"]),
        },
    )
    return {"cleanup": result}


@router.post("/douyin/downloads", status_code=202)
def submit_download(
    workspace_id: str,
    body: DownloadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    if body.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace path and body must match.")
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    require_governed_assurance(user)
    try:
        job = create_download_job(body, actor_user_id=user.id)
    except PermissionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_submitted",
        "download",
        job["id"],
        {"provider": "douyin-downloader", "source_count": len(body.urls)},
    )
    return {"job": job}


@router.post("/downloads/library-sync", status_code=202)
def sync_downloads_to_library(
    workspace_id: str,
    body: LibrarySyncRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner", "editor", "approver"})
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400, detail="Adding downloads to Library requires confirmation."
        )
    result = reconcile_downloads_to_library(workspace_id, user.id)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.downloads_library_sync_queued",
        "workspace",
        workspace_id,
        {
            "scanned_downloads": result["scanned_downloads"],
            "queued_count": len(result["queued"]),
            "error_count": len(result["errors"]),
            "removed_count": len(result["removed_asset_ids"]),
        },
    )
    return {"sync": result}


@router.post("/downloads/{job_id}/resume", status_code=202)
def resume_download(
    workspace_id: str,
    job_id: str,
    body: ResumeDownloadRequest,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(
        membership(session, workspace_id, user.id),
        {"owner", "editor", "approver"},
    )
    require_governed_assurance(user)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Resuming a download requires confirmation.",
        )
    try:
        job = resume_download_job(
            job_id, workspace_id, from_saved_files=body.from_saved_files
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Download not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "media.download_resumed",
        "download",
        job_id,
        {
            "files_on_disk": job["progress"]["files_downloaded"],
            "from_saved_files": body.from_saved_files,
        },
    )
    return {"job": job}


@router.get("/downloads/{job_id}")
def get_download(
    workspace_id: str,
    job_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    try:
        job = download_job(job_id)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Download not found.") from error
    if job["workspace_id"] != workspace_id:
        raise HTTPException(status_code=404, detail="Download not found.")
    return {"job": job}

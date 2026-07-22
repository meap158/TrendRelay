"""Authenticated workspace, role, secret-reference, and audit API."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.models import (
    AuditEvent,
    SecretReference,
    UserProfile,
    Workspace,
    WorkspaceMember,
)

router = APIRouter(prefix="/api", tags=["workspaces"])
Role = Literal["owner", "editor", "approver", "analyst"]
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=80)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("Use lowercase letters, numbers, and single hyphens.")
        return value


class MemberCreate(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    role: Role


class SecretReferenceCreate(BaseModel):
    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    locator: str = Field(min_length=3, max_length=500)

    @field_validator("locator")
    @classmethod
    def reference_only(cls, value: str) -> str:
        allowed = ("os-keyring://", "vault://", "supabase-vault://", "env://")
        if not value.startswith(allowed):
            raise ValueError("locator must reference an approved secret store")
        return value


def serialize_workspace(item: Workspace, role: str) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "slug": item.slug,
        "role": role,
        "created_at": item.created_at,
    }


def ensure_profile(session: Session, user: CurrentUser) -> UserProfile:
    profile = session.get(UserProfile, user.id)
    if not profile:
        profile = UserProfile(id=user.id, email=user.email)
        session.add(profile)
        session.flush()
    elif user.email and profile.email != user.email:
        profile.email = user.email
    return profile


def membership(session: Session, workspace_id: str, user_id: str) -> WorkspaceMember:
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id
        )
    )
    if not member:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return member


def require_role(member: WorkspaceMember, allowed: set[str]) -> None:
    if member.role not in allowed:
        raise HTTPException(status_code=403, detail="Workspace role does not permit this action.")


def audit(
    session: Session,
    request: Request,
    workspace_id: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail or {},
            request_id=request.headers.get("x-request-id"),
        )
    )


@router.get("/workspaces")
def list_workspaces(user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    rows = session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember)
        .where(WorkspaceMember.user_id == user.id)
    ).all()
    return {"workspaces": [serialize_workspace(item, role) for item, role in rows]}


@router.post("/workspaces", status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    ensure_profile(session, user)
    if session.scalar(select(Workspace).where(Workspace.slug == body.slug)):
        raise HTTPException(status_code=409, detail="Workspace slug is already in use.")
    workspace = Workspace(name=body.name.strip(), slug=body.slug, created_by=user.id)
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    audit(session, request, workspace.id, user.id, "workspace.created", "workspace", workspace.id)
    return {"workspace": serialize_workspace(workspace, "owner")}


@router.get("/workspaces/{workspace_id}/members")
def list_members(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    rows = session.execute(
        select(WorkspaceMember, UserProfile.email)
        .join(UserProfile, UserProfile.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    return {
        "members": [
            {"id": item.id, "user_id": item.user_id, "email": email, "role": item.role}
            for item, email in rows
        ]
    }


@router.post("/workspaces/{workspace_id}/members", status_code=201)
def add_member(
    workspace_id: str,
    body: MemberCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    if session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == body.user_id
        )
    ):
        raise HTTPException(status_code=409, detail="User is already a workspace member.")
    profile = session.get(UserProfile, body.user_id) or UserProfile(
        id=body.user_id, email=body.email
    )
    session.add(profile)
    member = WorkspaceMember(workspace_id=workspace_id, user_id=body.user_id, role=body.role)
    session.add(member)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "workspace.member_added",
        "workspace_member",
        member.id,
        {"role": body.role},
    )
    return {"member": {"id": member.id, "user_id": member.user_id, "role": member.role}}


@router.get("/workspaces/{workspace_id}/secret-references")
def list_secret_references(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    items = session.scalars(
        select(SecretReference)
        .where(SecretReference.workspace_id == workspace_id)
        .order_by(SecretReference.created_at)
    ).all()
    return {
        "secret_references": [
            {"id": item.id, "provider": item.provider, "name": item.name, "locator": item.locator}
            for item in items
        ]
    }


@router.post("/workspaces/{workspace_id}/secret-references", status_code=201)
def create_secret_reference(
    workspace_id: str,
    body: SecretReferenceCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    if session.scalar(
        select(SecretReference).where(
            SecretReference.workspace_id == workspace_id,
            SecretReference.provider == body.provider,
            SecretReference.name == body.name,
        )
    ):
        raise HTTPException(status_code=409, detail="Secret reference already exists.")
    item = SecretReference(
        workspace_id=workspace_id,
        provider=body.provider,
        name=body.name,
        locator=body.locator,
        created_by=user.id,
    )
    session.add(item)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "secret_reference.created",
        "secret_reference",
        item.id,
        {"provider": item.provider, "name": item.name},
    )
    return {
        "secret_reference": {
            "id": item.id,
            "provider": item.provider,
            "name": item.name,
            "locator": item.locator,
        }
    }


@router.get("/workspaces/{workspace_id}/audit-events")
def list_audit_events(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(100)
    ).all()
    return {
        "events": [
            {
                "id": item.id,
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "detail": item.detail,
                "actor_user_id": item.actor_user_id,
                "created_at": item.created_at,
            }
            for item in events
        ]
    }

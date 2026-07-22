"""Authenticated workspace, role, secret-reference, and audit API."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.config import get_settings
from trendrelay_api.database import get_session
from trendrelay_api.email_delivery import send_invitation_email
from trendrelay_api.models import (
    AuditEvent,
    SecretReference,
    UserProfile,
    Workspace,
    WorkspaceInvitation,
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


class InvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role
    expires_hours: int = Field(default=72, ge=1, le=168)
    deliver_email: bool = False

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class InvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=200)


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
    require_governed_assurance(user)
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


def invitation_status(item: WorkspaceInvitation) -> str:
    expires_at = (
        item.expires_at.replace(tzinfo=UTC) if item.expires_at.tzinfo is None else item.expires_at
    )
    if item.accepted_at:
        return "accepted"
    if item.revoked_at:
        return "revoked"
    if expires_at <= datetime.now(UTC):
        return "expired"
    return "pending"


def enforce_invitation_delivery_rate_limit(session: Session, workspace_id: str) -> None:
    session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    attempts = (
        session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action == "workspace.invitation_delivery_attempted",
                AuditEvent.created_at >= cutoff,
            )
        )
        or 0
    )
    if attempts >= get_settings().invitation_delivery_hourly_limit:
        raise HTTPException(status_code=429, detail="Invitation email rate limit reached.")


def serialize_invitation(item: WorkspaceInvitation) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "email": item.email,
        "role": item.role,
        "status": invitation_status(item),
        "created_at": item.created_at,
        "expires_at": item.expires_at,
    }


@router.get("/workspaces/{workspace_id}/invitations")
def list_invitations(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    items = session.scalars(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id)
        .order_by(WorkspaceInvitation.created_at.desc())
        .limit(100)
    ).all()
    return {"invitations": [serialize_invitation(item) for item in items]}


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def create_invitation(
    workspace_id: str,
    body: InvitationCreate,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    existing_member = session.scalar(
        select(WorkspaceMember)
        .join(UserProfile, UserProfile.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            func.lower(UserProfile.email) == body.email,
        )
    )
    if existing_member:
        raise HTTPException(status_code=409, detail="Email already belongs to a workspace member.")
    candidates = session.scalars(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.email == body.email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
        )
    ).all()
    if any(invitation_status(item) == "pending" for item in candidates):
        raise HTTPException(status_code=409, detail="A pending invitation already exists.")
    if body.deliver_email:
        enforce_invitation_delivery_rate_limit(session, workspace_id)
    raw_token = token_urlsafe(32)
    item = WorkspaceInvitation(
        workspace_id=workspace_id,
        email=body.email,
        role=body.role,
        token_hash=sha256(raw_token.encode("utf-8")).hexdigest(),
        invited_by=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=body.expires_hours),
    )
    session.add(item)
    session.flush()
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "workspace.invitation_created",
        "workspace_invitation",
        item.id,
        {"email": item.email, "role": item.role, "expires_at": item.expires_at.isoformat()},
    )
    delivery = {"requested": body.deliver_email, "status": "skipped", "detail": None}
    if body.deliver_email:
        audit(
            session,
            request,
            workspace_id,
            user.id,
            "workspace.invitation_delivery_attempted",
            "workspace_invitation",
            item.id,
            {"channel": "email", "recipient": item.email},
        )
        # Commit the digest before the external send so a delivered link is always valid.
        session.commit()
        workspace = session.get(Workspace, workspace_id)
        result = send_invitation_email(
            recipient=item.email,
            workspace_name=workspace.name if workspace else "TrendRelay",
            role=item.role,
            token=raw_token,
            expires_at=item.expires_at,
        )
        delivery = {"requested": True, "status": result.status, "detail": result.detail}
        audit(
            session,
            request,
            workspace_id,
            user.id,
            f"workspace.invitation_delivery_{result.status}",
            "workspace_invitation",
            item.id,
            {"channel": "email", "recipient": item.email, "detail": result.detail},
        )
    return {
        "invitation": serialize_invitation(item),
        "token": raw_token,
        "delivery": delivery,
    }


@router.post("/workspaces/{workspace_id}/invitations/{invitation_id}/revoke")
def revoke_invitation(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
    item = session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if invitation_status(item) != "pending":
        raise HTTPException(status_code=409, detail="Only pending invitations can be revoked.")
    item.revoked_at = datetime.now(UTC)
    audit(
        session,
        request,
        workspace_id,
        user.id,
        "workspace.invitation_revoked",
        "workspace_invitation",
        item.id,
    )
    return {"invitation": serialize_invitation(item)}


@router.post("/invitations/accept")
def accept_invitation(
    body: InvitationAccept,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    if not user.email:
        raise HTTPException(
            status_code=403, detail="The authenticated account has no email address."
        )
    item = session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.token_hash == sha256(body.token.encode("utf-8")).hexdigest()
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if invitation_status(item) != "pending":
        raise HTTPException(status_code=409, detail="Invitation is no longer active.")
    if user.email.strip().lower() != item.email:
        raise HTTPException(status_code=403, detail="Invitation email does not match this account.")
    ensure_profile(session, user)
    if session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == item.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    ):
        raise HTTPException(status_code=409, detail="User is already a workspace member.")
    member = WorkspaceMember(workspace_id=item.workspace_id, user_id=user.id, role=item.role)
    session.add(member)
    session.flush()
    item.accepted_at = datetime.now(UTC)
    item.accepted_by = user.id
    audit(
        session,
        request,
        item.workspace_id,
        user.id,
        "workspace.invitation_accepted",
        "workspace_invitation",
        item.id,
        {"role": item.role},
    )
    workspace = session.get(Workspace, item.workspace_id)
    return {"workspace": serialize_workspace(workspace, item.role), "member_id": member.id}


@router.get("/workspaces/{workspace_id}/secret-references")
def list_secret_references(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_role(membership(session, workspace_id, user.id), {"owner"})
    require_governed_assurance(user)
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
    require_governed_assurance(user)
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

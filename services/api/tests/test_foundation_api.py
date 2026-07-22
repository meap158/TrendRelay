import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import auth, foundation
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.email_delivery import DeliveryResult
from trendrelay_api.main import app
from trendrelay_api.models import AuditEvent, Base, WorkspaceInvitation

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="owner-user", email="owner@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_workspace_owner_members_secret_references_and_audit() -> None:
    created = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    )
    assert created.status_code == 201
    workspace = created.json()["workspace"]
    assert workspace["role"] == "owner"

    member = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/members",
            json={"user_id": "analyst-user", "email": "analyst@example.com", "role": "analyst"},
        )
    )
    assert member.status_code == 201
    listed_members = asyncio.run(request("GET", f"/api/workspaces/{workspace['id']}/members"))
    assert {item["role"] for item in listed_members.json()["members"]} == {"owner", "analyst"}

    secret = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/secret-references",
            json={
                "provider": "postiz",
                "name": "POSTIZ_API_KEY",
                "locator": "os-keyring://trendrelay/postiz/editorial",
            },
        )
    )
    assert secret.status_code == 201
    assert "value" not in secret.json()["secret_reference"]
    listed_secrets = asyncio.run(
        request("GET", f"/api/workspaces/{workspace['id']}/secret-references")
    )
    assert listed_secrets.json()["secret_references"][0]["name"] == "POSTIZ_API_KEY"

    duplicate = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/secret-references",
            json={
                "provider": "postiz",
                "name": "POSTIZ_API_KEY",
                "locator": "os-keyring://trendrelay/postiz/editorial",
            },
        )
    )
    assert duplicate.status_code == 409

    audit = asyncio.run(request("GET", f"/api/workspaces/{workspace['id']}/audit-events"))
    assert audit.status_code == 200
    assert [event["action"] for event in audit.json()["events"]] == [
        "secret_reference.created",
        "workspace.member_added",
        "workspace.created",
    ]


def test_non_owner_cannot_manage_secret_references() -> None:
    created = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{created['id']}/members",
            json={"user_id": "analyst-user", "role": "analyst"},
        )
    )
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="analyst-user")

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{created['id']}/secret-references",
            json={"provider": "postiz", "name": "POSTIZ_API_KEY", "locator": "os-keyring://ref"},
        )
    )

    assert response.status_code == 403


def test_raw_secret_value_is_rejected() -> None:
    created = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{created['id']}/secret-references",
            json={
                "provider": "postiz",
                "name": "POSTIZ_API_KEY",
                "locator": "sk_live_not_a_reference",
            },
        )
    )

    assert response.status_code == 422


def test_workspace_slug_is_normalized_and_validated() -> None:
    normalized = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "My-Team"})
    )
    invalid = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "bad slug!"})
    )

    assert normalized.status_code == 201
    assert normalized.json()["workspace"]["slug"] == "my-team"
    assert invalid.status_code == 422


def test_workspace_invitation_acceptance_is_email_bound_and_single_use() -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={"email": "Editor@Example.com", "role": "editor", "expires_hours": 24},
        )
    )
    assert created.status_code == 201
    token = created.json()["token"]
    assert created.json()["invitation"]["email"] == "editor@example.com"
    assert token not in str(created.json()["invitation"])
    with TestingSession() as session:
        stored = session.scalar(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.id == created.json()["invitation"]["id"]
            )
        )
        assert stored is not None
        assert stored.token_hash != token
        assert len(stored.token_hash) == 64

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="wrong-user", email="wrong@example.com"
    )
    mismatch = asyncio.run(request("POST", "/api/invitations/accept", json={"token": token}))
    assert mismatch.status_code == 403

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="editor-user", email="editor@example.com"
    )
    accepted = asyncio.run(request("POST", "/api/invitations/accept", json={"token": token}))
    replay = asyncio.run(request("POST", "/api/invitations/accept", json={"token": token}))
    assert accepted.status_code == 200
    assert accepted.json()["workspace"]["role"] == "editor"
    assert replay.status_code == 409


def test_workspace_invitation_can_be_revoked_by_owner() -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={"email": "analyst@example.com", "role": "analyst"},
        )
    ).json()
    invitation_id = created["invitation"]["id"]
    revoked = asyncio.run(
        request("POST", f"/api/workspaces/{workspace['id']}/invitations/{invitation_id}/revoke")
    )
    assert revoked.status_code == 200
    assert revoked.json()["invitation"]["status"] == "revoked"

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="analyst-user", email="analyst@example.com"
    )
    acceptance = asyncio.run(
        request("POST", "/api/invitations/accept", json={"token": created["token"]})
    )
    assert acceptance.status_code == 409


def test_expired_workspace_invitation_cannot_be_accepted() -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    created = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={"email": "late@example.com", "role": "analyst"},
        )
    ).json()
    with TestingSession() as session:
        invitation = session.get(WorkspaceInvitation, created["invitation"]["id"])
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="late-user", email="late@example.com"
    )
    response = asyncio.run(
        request("POST", "/api/invitations/accept", json={"token": created["token"]})
    )
    assert response.status_code == 409


def test_requested_invitation_delivery_is_audited_without_persisting_token(
    monkeypatch,
) -> None:
    delivered: dict[str, str] = {}

    def fake_delivery(**kwargs):
        delivered.update({key: str(value) for key, value in kwargs.items()})
        return DeliveryResult("sent")

    monkeypatch.setattr(foundation, "send_invitation_email", fake_delivery)
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={
                "email": "editor@example.com",
                "role": "editor",
                "deliver_email": True,
            },
        )
    )

    assert response.status_code == 201
    payload = response.json()
    token = payload["token"]
    assert payload["delivery"] == {"requested": True, "status": "sent", "detail": None}
    assert delivered["token"] == token
    with TestingSession() as session:
        invitation = session.get(WorkspaceInvitation, payload["invitation"]["id"])
        events = session.scalars(select(AuditEvent)).all()
        assert invitation is not None
        assert invitation.token_hash != token
        assert token not in str([event.detail for event in events])
        assert any(event.action == "workspace.invitation_delivery_sent" for event in events)


def test_invitation_email_delivery_is_rate_limited_per_workspace(monkeypatch) -> None:
    deliveries: list[str] = []

    def fake_delivery(**kwargs):
        deliveries.append(kwargs["recipient"])
        return DeliveryResult("sent")

    monkeypatch.setattr(foundation, "send_invitation_email", fake_delivery)
    monkeypatch.setattr(
        foundation,
        "get_settings",
        lambda: type("Config", (), {"invitation_delivery_hourly_limit": 1})(),
    )
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    first = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={"email": "first@example.com", "role": "editor", "deliver_email": True},
        )
    )
    second = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={"email": "second@example.com", "role": "editor", "deliver_email": True},
        )
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert deliveries == ["first@example.com"]


def test_invitation_delivery_failure_preserves_copy_link(monkeypatch) -> None:
    monkeypatch.setattr(
        foundation,
        "send_invitation_email",
        lambda **_kwargs: DeliveryResult("failed", "SMTP delivery is not configured."),
    )
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace['id']}/invitations",
            json={"email": "fallback@example.com", "role": "editor", "deliver_email": True},
        )
    )

    assert response.status_code == 201
    payload = response.json()
    assert len(payload["token"]) >= 32
    assert payload["delivery"]["status"] == "failed"
    assert payload["delivery"]["detail"] == "SMTP delivery is not configured."
    with TestingSession() as session:
        invitation = session.get(WorkspaceInvitation, payload["invitation"]["id"])
        assert invitation is not None
        assert invitation.token_hash != payload["token"]


def test_governed_workspace_actions_can_require_aal2(monkeypatch) -> None:
    workspace = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Editorial", "slug": "editorial"})
    ).json()["workspace"]
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: type("Config", (), {"require_aal2_for_governed_actions": True})(),
    )

    rejected = asyncio.run(request("GET", f"/api/workspaces/{workspace['id']}/invitations"))
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="owner-user", email="owner@example.com", assurance_level="aal2"
    )
    accepted = asyncio.run(request("GET", f"/api/workspaces/{workspace['id']}/invitations"))

    assert rejected.status_code == 403
    assert accepted.status_code == 200

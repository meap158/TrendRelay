import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base

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

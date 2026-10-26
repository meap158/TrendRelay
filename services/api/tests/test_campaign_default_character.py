import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import campaign_characters
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.character_models import Character
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


def call(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(request(method, path, **kwargs))


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    campaign_characters._column_seen = False
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="campaign-owner", email="owner@example.com", assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()
    campaign_characters._column_seen = False


def workspace_with_campaign() -> tuple[str, str]:
    made = call("POST", "/api/workspaces", json={"name": "Sleep Lab", "slug": "sleep-lab"})
    assert made.status_code == 201
    workspace_id = made.json()["workspace"]["id"]
    campaign = call("POST", f"/api/workspaces/{workspace_id}/campaigns", json={
        "name": "Sleepwear", "objective": "Sell sleep sets", "audience": "Shoppers",
    })
    assert campaign.status_code == 201
    with TestingSession.begin() as session:
        session.add(Character(
            id="char-ready", workspace_id=workspace_id, slug="ha-my", name="Ha My",
            appearance="calm", outfit="nightgown",
        ))
        session.add(Character(
            id="char-archived", workspace_id=workspace_id, slug="old", name="Old",
            appearance="calm", outfit="plain", archived_at=datetime.now(UTC),
        ))
    return workspace_id, campaign.json()["campaign"]["id"]


def update(workspace_id: str, campaign_id: str, **fields) -> httpx.Response:
    return call("POST", f"/api/workspaces/{workspace_id}/campaigns/{campaign_id}", json={
        "name": "Sleepwear", "objective": "Sell sleep sets", "audience": "Shoppers", **fields,
    })


def listed_default(workspace_id: str) -> str | None:
    rows = call("GET", f"/api/workspaces/{workspace_id}/campaigns").json()["campaigns"]
    return rows[0]["default_character_id"]


def tagged_default(workspace_id: str) -> str | None:
    rows = call("GET", f"/api/workspaces/{workspace_id}/attribution/campaign-tags").json()
    return rows["campaigns"][0]["default_character_id"]


def test_a_campaign_names_its_default_character_and_both_reads_carry_it() -> None:
    workspace_id, campaign_id = workspace_with_campaign()
    assert listed_default(workspace_id) is None

    assert update(workspace_id, campaign_id, default_character_id="char-ready").status_code == 200
    assert listed_default(workspace_id) == "char-ready"
    assert tagged_default(workspace_id) == "char-ready"

    # Leaving the field out keeps it; an empty string clears it.
    assert update(workspace_id, campaign_id).status_code == 200
    assert listed_default(workspace_id) == "char-ready"
    assert update(workspace_id, campaign_id, default_character_id="").status_code == 200
    assert listed_default(workspace_id) is None


def test_only_a_ready_character_of_the_workspace_can_be_the_default() -> None:
    workspace_id, campaign_id = workspace_with_campaign()
    for refused in ("char-archived", "char-missing"):
        response = update(workspace_id, campaign_id, default_character_id=refused)
        assert response.status_code == 422
    assert listed_default(workspace_id) is None


def test_a_default_whose_character_was_archived_reads_as_none() -> None:
    workspace_id, campaign_id = workspace_with_campaign()
    assert update(workspace_id, campaign_id, default_character_id="char-ready").status_code == 200
    with TestingSession.begin() as session:
        session.get(Character, "char-ready").archived_at = datetime.now(UTC)
    assert listed_default(workspace_id) is None
    assert tagged_default(workspace_id) is None


def test_campaign_reads_answer_before_the_column_is_migrated() -> None:
    workspace_id, campaign_id = workspace_with_campaign()
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE campaigns DROP COLUMN default_character_id"))
    campaign_characters._column_seen = False

    listed = call("GET", f"/api/workspaces/{workspace_id}/campaigns")
    assert listed.status_code == 200
    assert listed.json()["campaigns"][0]["default_character_id"] is None
    assert call("GET", f"/api/workspaces/{workspace_id}/attribution/campaign-tags").status_code == 200
    # An ordinary edit still saves; asking for a default says why it cannot yet.
    assert update(workspace_id, campaign_id).status_code == 200
    refused = update(workspace_id, campaign_id, default_character_id="char-ready")
    assert refused.status_code == 422
    assert "database" in refused.json()["detail"]

import asyncio

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base, DevicePairing

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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


async def request(
    method: str,
    path: str,
    *,
    client_host: str = "127.0.0.1",
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_pairing_requires_local_start_and_one_time_browser_approval() -> None:
    blocked = asyncio.run(
        request(
            "POST",
            "/api/device-pairings",
            client_host="10.20.30.40",
            json={"device_name": "Remote laptop"},
        )
    )
    assert blocked.status_code == 403

    started = asyncio.run(
        request("POST", "/api/device-pairings", json={"device_name": "Editorial laptop"})
    )
    assert started.status_code == 201
    pairing = started.json()
    assert len(pairing["user_code"]) == 8
    public_fields = {key: value for key, value in pairing.items() if key != "device_code"}
    assert pairing["device_code"] not in str(public_fields)
    with TestingSession() as session:
        stored = session.scalar(
            select(DevicePairing).where(DevicePairing.id == pairing["pairing_id"])
        )
        assert stored is not None
        assert stored.device_code_hash != pairing["device_code"]

    pending = asyncio.run(
        request(
            "POST",
            "/api/device-pairings/token/exchange",
            json={"device_code": pairing["device_code"]},
        )
    )
    assert pending.status_code == 428

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="paired-user", email="editor@example.com"
    )
    review = asyncio.run(request("GET", f"/api/device-pairings/{pairing['user_code']}"))
    approval = asyncio.run(
        request("POST", f"/api/device-pairings/{pairing['user_code']}/approve")
    )
    assert review.json()["device_name"] == "Editorial laptop"
    assert approval.status_code == 200

    del app.dependency_overrides[current_user]
    exchanged = asyncio.run(
        request(
            "POST",
            "/api/device-pairings/token/exchange",
            json={"device_code": pairing["device_code"]},
        )
    )
    assert exchanged.status_code == 200
    token = exchanged.json()["access_token"]
    authenticated = asyncio.run(
        request("GET", "/api/workspaces", headers={"Authorization": f"Bearer {token}"})
    )
    replay = asyncio.run(
        request(
            "POST",
            "/api/device-pairings/token/exchange",
            json={"device_code": pairing["device_code"]},
        )
    )
    assert authenticated.status_code == 200
    assert authenticated.json() == {"workspaces": []}
    assert replay.status_code == 409

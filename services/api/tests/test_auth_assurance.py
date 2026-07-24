from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from trendrelay_api import auth


def test_current_user_carries_verified_assurance_from_token(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "decode_device_token",
        lambda _token: {"sub": "user-1", "email": "owner@example.com", "aal": "aal2"},
    )
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(require_aal2_for_governed_actions=False),
    )
    user = auth.current_user(None, "Bearer device-token")
    assert user.id == "user-1"
    assert user.assurance_level == "aal2"


def test_missing_assurance_claim_defaults_to_aal1(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "decode_device_token",
        lambda _token: {"sub": "user-1", "email": "owner@example.com"},
    )
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(require_aal2_for_governed_actions=False),
    )
    assert auth.current_user(None, "Bearer device-token").assurance_level == "aal1"


def test_governed_policy_rejects_aal1_and_accepts_aal2(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(require_aal2_for_governed_actions=True),
    )
    with pytest.raises(HTTPException) as error:
        auth.require_governed_assurance(auth.CurrentUser(id="user-1"))
    assert error.value.status_code == 403

    auth.require_governed_assurance(auth.CurrentUser(id="user-1", assurance_level="aal2"))


def local_request(host: str = "127.0.0.1") -> Request:
    return Request({"type": "http", "client": (host, 50000), "headers": []})


def test_local_development_bypass_is_loopback_only(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(environment="development", local_auth_bypass=True),
    )

    user = auth.current_user(local_request(), None)

    assert user.id == "local-admin"
    assert user.assurance_level == "aal2"
    assert user.local_development is True

    with pytest.raises(HTTPException) as error:
        auth.current_user(local_request("192.168.1.25"), None)
    assert error.value.status_code == 401


def test_local_bypass_is_disabled_outside_development(monkeypatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(environment="production", local_auth_bypass=True),
    )

    with pytest.raises(HTTPException) as error:
        auth.current_user(local_request(), None)
    assert error.value.status_code == 401

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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
    user = auth.current_user("Bearer device-token")
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
    assert auth.current_user("Bearer device-token").assurance_level == "aal1"


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

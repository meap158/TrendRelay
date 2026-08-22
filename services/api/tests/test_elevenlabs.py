"""The key, whether the service accepts it, and what is left of the allowance.

Stage one of voice generation. Nothing here makes speech; what it fixes is the
fact everything later depends on and that is most likely to be wrong at the
worst moment - how many characters are left, and whether the key still works.

Two properties are worth more than the rest. A refused key, an unreachable
service and no key at all have to stay three different answers, because they
need three different things done about them. And the key itself must never come
back in a payload the interface renders.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from trendrelay_api.integrations import elevenlabs

KEY = "sk_eleven_secret_value_do_not_leak"

PLAN = {
    "tier": "creator",
    "character_count": 40_000,
    "character_limit": 220_000,
    "character_refresh_period": "monthly",
    "can_extend_character_limit": False,
}


@pytest.fixture()
def saved_key(monkeypatch):
    monkeypatch.setenv(elevenlabs.API_KEY_ENV, KEY)
    return KEY


def answering(payload: dict, monkeypatch) -> list[urllib.request.Request]:
    """Stand in for the service, and record what was asked of it."""
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        body = io.BytesIO(json.dumps(payload).encode("utf-8"))
        body.__enter__ = lambda: body  # type: ignore[method-assign]
        body.__exit__ = lambda *args: None  # type: ignore[method-assign]
        return body

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)
    return seen


def refusing(code: int, monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "no", {}, None)

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)


def test_no_key_is_reported_without_reaching_out(monkeypatch) -> None:
    """A missing key is a form to fill in, not a failed request."""
    monkeypatch.delenv(elevenlabs.API_KEY_ENV, raising=False)
    monkeypatch.setattr(elevenlabs, "configured_keys", lambda keys: {keys[0]: False})

    def refuse(*args, **kwargs):
        raise AssertionError("asked the service without a key")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", refuse)

    status = elevenlabs.provider_status()

    assert status["configured"] is False
    assert status["reachable"] is False
    assert "No ElevenLabs API key" in status["reason"]


def test_the_probe_can_be_declined(saved_key, monkeypatch) -> None:
    """A page listing every tool must not make a network call per card."""

    def refuse(*args, **kwargs):
        raise AssertionError("probed when asked not to")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", refuse)

    status = elevenlabs.provider_status(probe=False)

    assert status["configured"] is True
    assert status["reachable"] is False


def test_a_working_key_reports_the_plan_the_service_names(saved_key, monkeypatch) -> None:
    """No publishing engine here can name its own tier; this one is told.

    So the plan is taken from the service rather than inferred from a quota,
    and the usage is what it actually is rather than what a tier implies.
    """
    seen = answering(PLAN, monkeypatch)

    status = elevenlabs.provider_status()

    assert status["reachable"] is True
    assert status["tier"] == "creator"
    assert seen[0].full_url.endswith("/v1/user/subscription")
    # Their header, not `Authorization`. Matched case-insensitively, because
    # `urllib` capitalises header names on the way in.
    sent = {name.lower(): value for name, value in seen[0].header_items()}
    assert sent[elevenlabs.AUTH_HEADER] == KEY
    assert "authorization" not in sent


def test_the_allowance_is_measured_and_says_what_is_left(saved_key, monkeypatch) -> None:
    """The number anybody is about to act on, from the service, not a price list."""
    answering(PLAN, monkeypatch)

    status = elevenlabs.provider_status()

    allowance = status["allowances"][0]
    assert allowance["confidence"] == "measured"
    assert (allowance["limit"], allowance["used"]) == (220_000, 40_000)
    assert allowance["remaining"] == 180_000
    # Lifted out for the pre-flight count any generation path owes: this library
    # holds ~1,800 clips and speech is billed per character.
    assert status["characters_remaining"] == 180_000


def test_an_unlimited_allowance_is_not_reported_as_zero(saved_key, monkeypatch) -> None:
    """No cap is a real answer, and a different one from "we do not know"."""
    answering({**PLAN, "character_limit": None}, monkeypatch)

    allowance = elevenlabs.provider_status()["allowances"][0]

    assert allowance["unlimited"] is True
    assert allowance["remaining"] is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [(401, "refused the key"), (403, "refused the key"), (429, "rate limiting")],
)
def test_a_refusal_says_which_kind_it_was(saved_key, monkeypatch, code, expected) -> None:
    """A rate limit is a wait and a refused key is a new key. Collapsing them
    into "failed" sends somebody to replace a key that was never the problem."""
    refusing(code, monkeypatch)

    status = elevenlabs.provider_status()

    assert status["reachable"] is False
    assert expected in status["reason"]


def test_an_unreachable_service_says_nothing_was_spent(saved_key, monkeypatch) -> None:
    """Speech costs money, so "did that just bill me" is the first question."""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)

    status = elevenlabs.provider_status()

    assert "could not be reached" in status["reason"]
    assert "nothing was spent" in status["reason"]


def test_the_key_never_comes_back_in_the_status(saved_key, monkeypatch) -> None:
    """This payload is rendered by the interface. The key is not for rendering."""
    answering(PLAN, monkeypatch)

    status = elevenlabs.provider_status()

    assert KEY not in json.dumps(status)
    assert status["credential_values_exposed"] is False
    # The name of the variable is fine, and is what the card tells somebody to set.
    assert status["api_key_env"] == "ELEVENLABS_API_KEY"


def test_the_setup_card_shows_the_allowance(saved_key, monkeypatch) -> None:
    """What the operator actually reads, assembled from the same status."""
    answering(PLAN, monkeypatch)
    from trendrelay_api.tool_setup import setup_report

    report = setup_report("elevenlabs")

    rows = {item["id"]: item for item in report["requirements"]}
    assert rows["api-key"]["status"] == "ready"
    assert rows["reachable"]["status"] == "ready"
    assert "180,000 of 220,000 left" in rows["characters"]["detail"]
    # Masked, never whole.
    assert KEY not in json.dumps(report)


def test_the_catalogue_says_it_reaches_out_and_needs_no_install() -> None:
    """The two facts about this entry that the page reads.

    `runs` is the one that matters: it is the first Library tool that is not
    local, and the group heading no longer speaks for it, so the card has to.
    """
    from trendrelay_api.tool_registry import list_tools

    tool = next(item for item in list_tools() if item["id"] == "elevenlabs")

    assert tool["runs"] == "network"
    assert tool["surface"] == "library"
    # Nothing to install: the key is the switch.
    assert tool["install_allowed"] is False
    assert tool["activation_allowed"] is False

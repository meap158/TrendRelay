"""Fetching the pinned model files: what is trusted, and what is refused.

A model is code in the sense that matters — it decides what the software does to
somebody's face — so "downloaded at setup time" is only acceptable with a hash
check that cannot be talked out of. Most of what is checked here is that the
check holds when the download misbehaves, and that a machine which cannot reach
the network still installs.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from scripts import model_assets
from scripts.model_assets import ModelAsset

PAYLOAD = b"a pretend onnx file"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def asset(tmp_path, monkeypatch) -> ModelAsset:
    monkeypatch.setattr(model_assets, "ROOT", tmp_path)
    monkeypatch.delenv(model_assets.SKIP, raising=False)
    return ModelAsset(
        name="Test model",
        path="models/test.onnx",
        urls=("https://first.example/model.onnx", "https://second.example/model.onnx"),
        sha256=DIGEST,
        size_bytes=len(PAYLOAD),
        licence="MIT",
        without_it="the fallback runs instead",
    )


def serves(monkeypatch, **bodies):
    """Stand in for the network. Records which URLs were actually asked for."""
    asked: list[str] = []

    def fake_download(url, into, timeout):
        asked.append(url)
        body = bodies.get(url)
        if body is None:
            raise OSError("unreachable")
        into.write_bytes(body)
        return len(body)

    monkeypatch.setattr(model_assets, "_download", fake_download)
    return asked


# --- the happy path ---------------------------------------------------------------


def test_a_pinned_file_is_installed_where_the_loader_looks(asset, monkeypatch) -> None:
    serves(monkeypatch, **{asset.urls[0]: PAYLOAD})
    ok, reason = model_assets.fetch(asset)
    assert (ok, reason) == (True, "installed")
    assert asset.destination.read_bytes() == PAYLOAD


def test_an_installed_file_is_not_downloaded_again(asset, monkeypatch) -> None:
    asset.destination.parent.mkdir(parents=True)
    asset.destination.write_bytes(PAYLOAD)
    asked = serves(monkeypatch, **{asset.urls[0]: PAYLOAD})
    ok, reason = model_assets.fetch(asset)
    assert (ok, reason) == (True, "already installed")
    assert asked == [], "a present, correct file was fetched again"


def test_the_second_mirror_covers_the_first_being_unreachable(asset, monkeypatch) -> None:
    asked = serves(monkeypatch, **{asset.urls[1]: PAYLOAD})
    ok, _reason = model_assets.fetch(asset)
    assert ok
    assert asked == list(asset.urls)


# --- what is refused --------------------------------------------------------------


def test_a_file_that_is_not_the_pinned_one_is_never_installed(asset, monkeypatch) -> None:
    serves(monkeypatch, **{asset.urls[0]: b"x" * len(PAYLOAD)})
    ok, reason = model_assets.fetch(asset)
    assert ok is False
    assert "pins" in reason
    assert not asset.destination.exists(), "a file failing the hash was left in place"


def test_a_connection_that_yields_nothing_falls_through_to_the_other_mirror(
    asset, monkeypatch
) -> None:
    """An empty body hashes perfectly well — to the digest of the empty string.

    Read as a hash mismatch that is "the upstream file changed", which both
    misdiagnoses a dropped connection and, because a mismatch deliberately does
    not fall through, skips the mirror that would have worked. Found by running
    the real fetcher against a network where the first mirror timed out.
    """
    asked: list[str] = []

    def flaky(url, into, timeout):
        asked.append(url)
        into.write_bytes(b"" if url == asset.urls[0] else PAYLOAD)
        return into.stat().st_size

    monkeypatch.setattr(model_assets, "_download", flaky)
    ok, _reason = model_assets.fetch(asset)
    assert ok is True
    assert asked == list(asset.urls)
    assert asset.destination.read_bytes() == PAYLOAD


def test_a_short_transfer_is_reported_as_one(asset, monkeypatch) -> None:
    def truncated(url, into, timeout):
        into.write_bytes(PAYLOAD[:4])
        return 4

    monkeypatch.setattr(model_assets, "_download", truncated)
    ok, reason = model_assets.fetch(asset)
    assert ok is False
    # Named as a transfer problem, not as a changed upstream file.
    assert "4 bytes arrived" in reason
    assert str(asset.size_bytes) in reason


def test_a_wrong_file_is_not_retried_against_the_other_mirror(asset, monkeypatch) -> None:
    """Two mirrors disagreeing with the pin is not a transport problem.

    Falling through would turn a supply-chain signal into "try somewhere else
    until something passes", which is the opposite of what pinning is for.
    """
    asked = serves(
        monkeypatch,
        **{asset.urls[0]: b"x" * len(PAYLOAD), asset.urls[1]: PAYLOAD},
    )
    ok, _reason = model_assets.fetch(asset)
    assert ok is False
    assert asked == [asset.urls[0]]


def test_nothing_half_written_is_left_behind(asset, monkeypatch) -> None:
    def fails_midway(url, into, timeout):
        into.write_bytes(b"half of a fi")
        raise OSError("the connection dropped")

    monkeypatch.setattr(model_assets, "_download", fails_midway)
    ok, _reason = model_assets.fetch(asset)
    assert ok is False
    # Neither the destination nor a stray temporary next to it.
    assert not asset.destination.exists()
    assert list(asset.destination.parent.iterdir()) == []


def test_a_truncated_earlier_download_is_replaced(asset, monkeypatch) -> None:
    """The likely way this goes wrong is a dropped connection, and a partial
    ONNX file loads as an error a long way from here."""
    asset.destination.parent.mkdir(parents=True)
    asset.destination.write_bytes(PAYLOAD[:5])
    serves(monkeypatch, **{asset.urls[0]: PAYLOAD})
    assert model_assets.is_installed(asset) is False
    ok, reason = model_assets.fetch(asset)
    assert (ok, reason) == (True, "installed")
    assert asset.destination.read_bytes() == PAYLOAD


def test_a_file_of_the_right_length_but_the_wrong_bytes_is_replaced(asset, monkeypatch) -> None:
    # Size alone is not identity, and this is the case a size check would pass.
    asset.destination.parent.mkdir(parents=True)
    asset.destination.write_bytes(b"x" * len(PAYLOAD))
    serves(monkeypatch, **{asset.urls[0]: PAYLOAD})
    assert model_assets.is_installed(asset) is False
    assert model_assets.fetch(asset)[0] is True
    assert asset.destination.read_bytes() == PAYLOAD


def test_a_response_larger_than_the_ceiling_is_cut_off(asset, monkeypatch, tmp_path) -> None:
    """A redirect to something unexpected must not fill the disk first."""

    class _Endless:
        headers: dict[str, str] = {}

        def read(self, _size):
            return b"\0" * 65536

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(model_assets, "MAX_BYTES", 256 * 1024)
    monkeypatch.setattr(model_assets.urllib.request, "urlopen", lambda *_a, **_k: _Endless())
    into = tmp_path / "big.part"
    with pytest.raises(ValueError, match="more than"):
        model_assets._download("https://example.invalid/x", into, timeout=1)
    assert into.stat().st_size <= model_assets.MAX_BYTES + 65536


# --- setup must survive it ---------------------------------------------------------


def test_no_network_is_not_a_failed_install(asset, monkeypatch) -> None:
    serves(monkeypatch)  # nothing is reachable
    said: list[str] = []
    complete = model_assets.ensure_all((asset,), announce=said.append)
    assert complete is False
    spoken = "\n".join(said)
    # Says what was lost and how to fix it by hand, because that is the whole
    # decision an operator reading this has to make.
    assert asset.without_it in spoken
    assert asset.path in spoken


def test_an_operator_can_turn_the_download_off(asset, monkeypatch) -> None:
    monkeypatch.setenv(model_assets.SKIP, "1")
    asked = serves(monkeypatch, **{asset.urls[0]: PAYLOAD})
    said: list[str] = []
    assert model_assets.ensure_all((asset,), announce=said.append) is False
    assert asked == []
    assert model_assets.SKIP in "\n".join(said)


def test_a_quiet_run_when_everything_is_already_here(asset, monkeypatch) -> None:
    asset.destination.parent.mkdir(parents=True)
    asset.destination.write_bytes(PAYLOAD)
    serves(monkeypatch)
    said: list[str] = []
    assert model_assets.ensure_all((asset,), announce=said.append) is True
    # Setup output is read by people watching for problems; "nothing happened"
    # should not be one of the lines.
    assert said == []


def test_setup_does_not_stop_when_fetching_throws(monkeypatch, capsys) -> None:
    from scripts import bootstrap

    monkeypatch.setattr(
        model_assets, "ensure_all",
        lambda **_k: (_ for _ in ()).throw(RuntimeError("something unforeseen")),
    )
    bootstrap.ensure_models()
    assert "something unforeseen" in capsys.readouterr().out


# --- the pin itself ----------------------------------------------------------------


def test_every_shipped_asset_is_pinned_and_attributed() -> None:
    assert model_assets.ASSETS
    for item in model_assets.ASSETS:
        assert re.fullmatch(r"[0-9a-f]{64}", item.sha256), item.name
        assert item.size_bytes > 0
        assert item.urls and all(url.startswith("https://") for url in item.urls)
        # More than one official source, so one being down is not a broken setup.
        assert len(item.urls) >= 2, item.name
        assert item.licence, item.name
        assert item.without_it, item.name


def test_the_face_detector_lands_where_the_blur_looks_for_it() -> None:
    """The pin and the loader have to agree on the path, and nothing else
    connects them — the loader reads a constant, this writes a file."""
    from trendrelay_api.integrations.face_blur import YUNET_MODEL

    assert YUNET_MODEL == model_assets.YUNET.destination


def test_the_detector_pinned_matches_the_opencv_this_project_installs() -> None:
    """OpenCV 4 infers on the exact input shape it is given; the 2026 revision
    of YuNet has dynamic dimensions for OpenCV 5's engine. Pinning that one
    would be pinning a file for a runtime this project does not install."""
    manifest = (model_assets.ROOT / "services" / "api" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "opencv-python-headless>=4.10,<5" in manifest
    assert all("2023mar" in url for url in model_assets.YUNET.urls)

"""Small contract tests for MCP file-reference diagnostics."""

from __future__ import annotations

import base64

import pytest

from trendrelay_api.integrations.mcp import intake

#: Just enough of each container to be recognised by its signature. Real files
#: are not needed here: what is under test is that the bytes decide the type.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x42\x82\x84webm" + b"\x00" * 32
MKV = b"\x1a\x45\xdf\xa3" + b"\x42\x82\x88matroska" + b"\x00" * 32


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def oversized_inline(prefix: bytes, limit: int) -> str:
    """Valid base64 with a recognisable prefix and a decoded size over limit.

    The implementation must refuse on encoded length, so this string is never
    decoded into the much larger bytes object during the test.
    """
    padded_prefix = prefix + b"\x00" * ((-len(prefix)) % 3)
    encoded_prefix = encoded(padded_prefix)
    target = 4 * ((limit + 2) // 3) + 4
    return encoded_prefix + "A" * (target - len(encoded_prefix))


def refuse_fetch(*_args, **_kwargs):
    raise AssertionError("inline bytes must not trigger a network fetch")


@pytest.mark.parametrize(
    "reference",
    ["file-generated-123", r"/mnt/data/generated.png", r"C:\\runtime\\generated.png"],
)
def test_private_file_references_name_the_transfer_boundary(reference: str) -> None:
    with pytest.raises(ValueError, match="private file registry|mounted runtime"):
        intake.upload_image("ws", image=reference)


def test_attachment_url_aliases_from_clients_are_accepted() -> None:
    fetched: list[str] = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            intake,
            "_ingest_fetched",
            lambda *_args, **_kwargs: {"job_id": "queued"},
        )
        result = intake.upload_media(
            "ws",
            media={"file_id": "private", "file_url": "https://files.example/one.png"},
            fetch=lambda url: fetched.append(url) or (b"image", "image/png"),
        )

    assert fetched == ["https://files.example/one.png"]
    assert result == {"job_id": "queued"}


# --- bytes a caller sends directly -------------------------------------------
#
# The case these cover is the one that has no URL to fetch: an assistant that
# has just generated an image holds it in its own private file registry, and
# if the client cannot mint a temporary public link from that there is nothing
# to point the fetch at. The bytes are the one thing the caller always has.


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (WEBP, "image/webp"),
        (MP4, "video/mp4"),
        (MOV, "video/quicktime"),
        (WEBM, "video/webm"),
        (MKV, "video/x-matroska"),
    ],
)
def test_inline_bytes_are_typed_by_their_signature(payload: bytes, expected: str) -> None:
    """The file says what it is. Nothing else gets a vote.

    A URL fetch can lean on the serving host's Content-Type header; inline
    bytes have no such witness, so the signature is the whole of the check.
    """
    seen: dict = {}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            intake,
            "_ingest_fetched",
            lambda _ws, data, content_type, *_a, **_k: seen.update(
                data=data, content_type=content_type
            )
            or {"job_id": "queued"},
        )
        intake.upload_media("ws", media_base64=encoded(payload), fetch=refuse_fetch)

    assert seen["content_type"] == expected
    assert seen["data"] == payload


def test_a_data_url_is_accepted_where_a_url_is_expected() -> None:
    """Because that is where a client holding one will naturally put it."""
    seen: dict = {}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            intake,
            "_ingest_fetched",
            lambda _ws, data, content_type, *_a, **_k: seen.update(
                content_type=content_type
            )
            or {"job_id": "queued"},
        )
        intake.upload_media(
            "ws",
            media_url=f"data:image/png;base64,{encoded(PNG)}",
            fetch=refuse_fetch,
        )

    assert seen["content_type"] == "image/png"


def test_a_lying_data_url_is_typed_by_the_bytes_not_the_label() -> None:
    """The declared type is a claim; the signature is the evidence."""
    seen: dict = {}

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            intake,
            "_ingest_fetched",
            lambda _ws, data, content_type, *_a, **_k: seen.update(
                content_type=content_type
            )
            or {"job_id": "queued"},
        )
        intake.upload_media(
            "ws",
            media_base64=f"data:video/mp4;base64,{encoded(PNG)}",
            fetch=refuse_fetch,
        )

    assert seen["content_type"] == "image/png"


def test_bytes_that_are_not_media_are_refused() -> None:
    """Text, a wrapper, or base64 encoded twice - none of them are a file."""
    with pytest.raises(ValueError, match="signature matches no allowed"):
        intake.upload_media("ws", media_base64=encoded(b"just some words"), fetch=refuse_fetch)


def test_a_payload_that_is_not_base64_says_so() -> None:
    with pytest.raises(ValueError, match="could not be decoded"):
        intake.upload_media("ws", media_base64="not base64 at all!!", fetch=refuse_fetch)


def test_an_oversized_payload_is_refused_before_it_is_decoded() -> None:
    """Refused on the encoded length, so the oversized bytes are never built."""
    oversized = oversized_inline(PNG, intake.MAX_IMAGE_BYTES)

    with pytest.raises(ValueError, match="larger than 25 MB"):
        intake.upload_image("ws", image_base64=oversized, fetch=refuse_fetch)


def test_an_image_upload_keeps_its_tighter_cap_on_the_inline_route() -> None:
    """The image door promised 25 MB. Sending bytes must not widen it."""
    with pytest.raises(ValueError, match="larger than 25 MB"):
        intake.upload_image(
            "ws",
            image_base64=oversized_inline(PNG, intake.MAX_IMAGE_BYTES),
            fetch=refuse_fetch,
        )


def test_upload_media_applies_the_image_cap_after_sniffing() -> None:
    """The generic door must not give a PNG the much larger video allowance."""
    with pytest.raises(ValueError, match="larger than 25 MB"):
        intake.upload_media(
            "ws",
            media_base64=oversized_inline(PNG, intake.MAX_IMAGE_BYTES),
            fetch=refuse_fetch,
        )


def test_heic_is_not_misfiled_as_mp4() -> None:
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32

    with pytest.raises(ValueError, match="signature matches no allowed"):
        intake.upload_media("ws", media_base64=encoded(heic), fetch=refuse_fetch)


def test_the_refusal_for_a_private_reference_now_names_the_way_out() -> None:
    """The dead end this closes.

    A generated file has no address, so "attach it or give a public URL" was
    advice a caller in that position could not take. The refusal has to name
    the route that does not need one.
    """
    with pytest.raises(ValueError, match="base64"):
        intake.upload_media("ws", media="file-generated-123")

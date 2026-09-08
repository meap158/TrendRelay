"""The render, which is the expensive half and so the durable one.

The encode belongs to FFmpeg and is stubbed here. What is worth pinning down is
everything the job decides: which transcript it uses, what it writes, that the
same request does not start a second encode of the same video, and that a
captioned cut is filed as its own kind rather than as an effects render that
"Remove effects" would delete.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import caption_jobs
from trendrelay_api.jobs import claim_job, fail_job
from trendrelay_api.main import app  # noqa: F401  registers every model for create_all
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import Base, Workspace

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Factory = sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    with Factory.begin() as session:
        session.add(Workspace(id="ws1", name="Lab", slug="lab", created_by="owner"))
    yield


def add_asset(*, transcript: bool = True, video: Path | None = None) -> str:
    with Factory.begin() as session:
        asset = MediaAsset(
            id="asset1",
            workspace_id="ws1",
            title="A clip",
            media_kind="video",
            source_type="upload",
            original_path=str(video or "clips/a.mp4"),
            original_sha256="a" * 64,
            mime_type="video/mp4",
            size_bytes=10,
            has_audio=True,
            created_by="owner",
        )
        session.add(asset)
        if transcript:
            words = ["hello", "there", "friend"]
            session.add(
                MediaTranscript(
                    workspace_id="ws1",
                    asset_id="asset1",
                    kind="speech",
                    language="en",
                    provider="faster-whisper",
                    status="machine",
                    text=" ".join(words),
                    segments=[
                        {
                            "text": " ".join(words),
                            "start_ms": 0,
                            "end_ms": 3000,
                            "words": [
                                {
                                    "text": word,
                                    "start_ms": i * 1000,
                                    "end_ms": (i + 1) * 1000,
                                    "probability": 0.9,
                                }
                                for i, word in enumerate(words)
                            ],
                        }
                    ],
                    created_by="owner",
                )
            )
    return "asset1"


def fake_burn(source, cues, output, *, style=None, prefilters=()) -> Path:
    """Stand in for FFmpeg: produce the file it would have written."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(b"a captioned clip")
    return Path(output)


def request(**changes) -> dict:
    return {
        "style_id": "broadcast",
        "style_overrides": {},
        "layout_overrides": {},
        "translate_to": None,
        "transcript_id": None,
        "delivery": "sidecar",
        **changes,
    }


def test_a_burn_lands_on_the_newest_rendered_cut(tmp_path) -> None:
    """The same rule as the interface's handoffPath, for the same reason.

    Burning onto the original would discard every rendered decision: captions
    over the unblurred faces somebody blurred on purpose, and a translated
    line over on-screen text whose cover was rendered as an effect.
    """
    add_asset()
    covered = tmp_path / "covered.mp4"
    covered.write_bytes(b"the covered cut")
    with Factory.begin() as session:
        session.add(MediaAssetVersion(
            workspace_id="ws1", asset_id="asset1", version_kind="edited",
            path=str(covered), sha256="b" * 64, mime_type="video/mp4",
            size_bytes=15,
        ))
        asset = session.get(MediaAsset, "asset1")
        assert caption_jobs._burn_source(session, asset) == covered


def test_a_rendered_cut_gone_from_disk_falls_back_to_the_original() -> None:
    """A stale version row must not fail the render."""
    add_asset()
    with Factory.begin() as session:
        session.add(MediaAssetVersion(
            workspace_id="ws1", asset_id="asset1", version_kind="blurred",
            path="clips/vanished.mp4", sha256="c" * 64, mime_type="video/mp4",
            size_bytes=15,
        ))
        asset = session.get(MediaAsset, "asset1")
        assert caption_jobs._burn_source(session, asset) == Path("clips/a.mp4")


# --- queueing -----------------------------------------------------------------


def test_the_same_request_twice_is_the_same_job() -> None:
    """Otherwise a double click starts a second encode of the same video."""
    add_asset()

    first = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    second = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert first["id"] == second["id"]


def test_the_same_request_resumes_after_its_failure_is_fixed() -> None:
    add_asset()
    failed = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    claim_job(failed["id"], "worker", factory=Factory)
    fail_job(
        failed["id"],
        "worker",
        "Translation was switched off.",
        retry_allowed=False,
        factory=Factory,
    )

    resumed = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert resumed["id"] == failed["id"]
    assert resumed["status"] == "queued"
    assert resumed["attempt_count"] == 0
    assert resumed["error"] is None


def test_a_different_style_is_a_different_job() -> None:
    add_asset()

    plain = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    popped = caption_jobs.queue(
        "ws1",
        "asset1",
        actor_user_id="owner",
        request=request(style_id="word-pop"),
        factory=Factory,
    )

    assert plain["id"] != popped["id"]


def test_an_asset_outside_the_workspace_is_refused() -> None:
    add_asset()

    with pytest.raises(LookupError):
        caption_jobs.queue(
            "other", "asset1", actor_user_id="owner", request=request(), factory=Factory
        )


# --- rendering ----------------------------------------------------------------


def test_sidecars_are_written_and_reported(tmp_path) -> None:
    add_asset()
    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    done = caption_jobs.run_caption_job(job["id"], factory=Factory)

    files = done["result"]["files"]
    assert Path(files["srt"]).read_text(encoding="utf-8").startswith("1\n")
    assert Path(files["vtt"]).exists()
    assert done["result"]["cue_count"] >= 1


def test_a_burn_files_a_captioned_version_not_an_edited_one(tmp_path) -> None:
    """`edited` is what "Remove effects" deletes. A caption is not that."""
    add_asset()
    job = caption_jobs.queue(
        "ws1",
        "asset1",
        actor_user_id="owner",
        request=request(delivery="burned"),
        factory=Factory,
    )

    caption_jobs.run_caption_job(job["id"], factory=Factory, burn=fake_burn)

    with Factory() as session:
        kinds = session.scalars(
            select(MediaAssetVersion.version_kind).where(MediaAssetVersion.asset_id == "asset1")
        ).all()
    assert kinds == ["captioned"]


def test_sidecars_are_kept_even_when_burning(tmp_path) -> None:
    """They cost a kilobyte. Discovering afterwards that they were not is worse."""
    add_asset()
    job = caption_jobs.queue(
        "ws1",
        "asset1",
        actor_user_id="owner",
        request=request(delivery="both"),
        factory=Factory,
    )

    done = caption_jobs.run_caption_job(job["id"], factory=Factory, burn=fake_burn)

    assert {"srt", "vtt", "burned"} <= set(done["result"]["files"])


def test_an_asset_with_no_transcript_fails_the_job_clearly() -> None:
    add_asset(transcript=False)
    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    with pytest.raises(RuntimeError, match="no speech transcript"):
        caption_jobs.run_caption_job(job["id"], factory=Factory)


def test_the_track_is_named_for_its_language(tmp_path) -> None:
    """So two languages of one clip sit beside each other and read as what they are."""
    add_asset()
    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    done = caption_jobs.run_caption_job(job["id"], factory=Factory)

    assert Path(done["result"]["files"]["srt"]).name == "asset1.en.srt"


def test_a_queued_burn_carries_the_clips_length() -> None:
    """The notification drawer estimates from it; the row is already open here.

    A burn re-encodes the whole clip, so how long it takes tracks how long the
    clip is. Carrying the length on the job is what lets a batch of them say
    how much longer it has without a database read per notification row.
    """
    add_asset()
    with Factory.begin() as session:
        session.get(MediaAsset, "asset1").duration_ms = 31_500

    queued = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert queued["payload"]["media_ms"] == 31_500


def test_the_length_is_not_part_of_what_makes_a_job_the_same() -> None:
    """Measuring a clip afterwards must not split one request into two jobs.

    The id is content-addressed on what the render depends on. A duration is
    metadata about the source, not an input to the encode, so a clip that gets
    measured between two identical requests still returns the job already
    doing it rather than starting a second one.
    """
    add_asset()
    before = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    with Factory.begin() as session:
        session.get(MediaAsset, "asset1").duration_ms = 44_000

    after = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert before["id"] == after["id"]


def test_a_reviewed_transcript_still_captions_on_the_machines_timings() -> None:
    """Reviewing one used to silence the captions completely.

    A reviewed transcript is stored as text and no segments - typing produces
    no timings - and it is the transcript captions prefer over the machine
    draft. So correcting a word left the asset with no cues at all, for every
    style, and nothing said so.
    """
    add_asset()
    with Factory.begin() as session:
        session.add(
            MediaTranscript(
                workspace_id="ws1",
                asset_id="asset1",
                kind="speech",
                language="en",
                provider="operator-reviewed",
                status="reviewed",
                text="hello there old friend",
                segments=[],
                created_by="owner",
            )
        )

    with Factory() as session:
        chosen = caption_jobs._transcript(session, "ws1", "asset1", None)
        segments = caption_jobs.caption_segments(session, "ws1", "asset1", chosen)

    assert chosen.status == "reviewed", "the reviewed one is still preferred"
    assert chosen.segments == [], "and it still stores no timings of its own"
    # But the words are now on the draft's clock rather than on nothing.
    assert segments, "a reviewed transcript produced no timed words"
    words = [word["text"] for word in segments[0]["words"]]
    assert words == ["hello", "there", "old", "friend"]
    # The two words the draft also had keep the timings it measured.
    assert segments[0]["words"][0]["start_ms"] == 0


def test_a_machine_transcript_is_used_as_it_stands() -> None:
    """Nothing is re-timed when the chosen transcript already has timings."""
    add_asset()

    with Factory() as session:
        chosen = caption_jobs._transcript(session, "ws1", "asset1", None)
        segments = caption_jobs.caption_segments(session, "ws1", "asset1", chosen)

    assert segments == list(chosen.segments)


# --- captioning the words already on the picture ------------------------------


def add_on_screen_reading(*, language: str = "zh") -> None:
    """An OCR reading with boxes, which is what makes a line placeable."""
    with Factory.begin() as session:
        session.get(MediaAsset, "asset1").width = 720
        session.get(MediaAsset, "asset1").height = 1280
        session.add(
            MediaTranscript(
                workspace_id="ws1",
                asset_id="asset1",
                kind="ocr",
                language=language,
                provider="rapidocr",
                status="machine",
                text="限时优惠",
                segments=[
                    {
                        "timestamp_ms": 0,
                        "lines": [
                            {
                                "text": "限时优惠",
                                "confidence": 0.94,
                                # A band across the middle of a 720x1280 frame.
                                "box": [[100, 500], [620, 500], [620, 620], [100, 620]],
                            }
                        ],
                    }
                ],
                created_by="owner",
            )
        )


def test_on_screen_text_is_captioned_over_the_words_it_replaces() -> None:
    """A translation of on-screen text has exactly one place it can go.

    At the bottom of the frame it is a second thing to read beside the thing it
    translates - and over a covered original it is a blank rectangle and an
    unexplained caption.
    """
    add_asset(transcript=False)
    add_on_screen_reading()

    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen"), factory=Factory,
    )
    done = caption_jobs.run_caption_job(job["id"], factory=Factory)

    assert done["result"]["cue_count"] == 1
    written = Path(done["result"]["files"]["srt"]).read_text(encoding="utf-8")
    assert "限时优惠" in written
    # Named apart from the spoken track, because one clip can have both and a
    # shared name would have the second render overwrite the first.
    assert "on-screen" in done["result"]["files"]["srt"]


def test_captioning_the_speech_and_the_picture_are_different_jobs() -> None:
    """Without the source in the id they content-address to the same job.

    Asking for the second would hand back the first, which reports success and
    renders nothing new.
    """
    add_asset()
    add_on_screen_reading()

    spoken = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory,
    )
    printed = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen"), factory=Factory,
    )

    assert spoken["id"] != printed["id"]


def test_a_clip_whose_text_was_never_read_says_so() -> None:
    add_asset(transcript=False)

    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen"), factory=Factory,
    )
    try:
        caption_jobs.run_caption_job(job["id"], factory=Factory)
    except RuntimeError as error:
        assert "on-screen text has not been read" in str(error)
    else:
        raise AssertionError("a clip with no reading rendered anyway")


def test_the_cover_is_part_of_the_jobs_identity() -> None:
    """The same lettering over a blur and over a box are different pictures."""
    add_asset(transcript=False)
    add_on_screen_reading()

    boxed = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen", delivery="burned"), factory=Factory,
    )
    blurred = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen", delivery="burned", cover="blur"),
        factory=Factory,
    )

    assert boxed["id"] != blurred["id"]


def test_a_picture_cover_is_burned_under_the_lettering(tmp_path) -> None:
    """One encode: the patch goes in as a prefilter of the same burn.

    And the lettering's own backdrop steps aside - left opaque, the box would
    sit on top of the blur somebody chose precisely because a solid box was
    not what they wanted.
    """
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    add_asset(transcript=False, video=video)
    add_on_screen_reading()
    seen: dict = {}

    def burn(source, cues, output, *, style=None, prefilters=()) -> Path:
        seen["prefilters"] = list(prefilters)
        seen["back_alpha"] = style.back_alpha
        return fake_burn(source, cues, output)

    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen", delivery="burned", cover="blur"),
        factory=Factory,
    )
    caption_jobs.run_caption_job(job["id"], factory=Factory, burn=burn)

    assert seen["prefilters"] and "boxblur" in seen["prefilters"][0]
    assert seen["back_alpha"] == 255, "the backdrop hides the patch it was traded for"


def test_the_backdrop_cover_needs_no_prefilters(tmp_path) -> None:
    """The default is the lettering's own box, exactly as before."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    add_asset(transcript=False, video=video)
    add_on_screen_reading()
    seen: dict = {}

    def burn(source, cues, output, *, style=None, prefilters=()) -> Path:
        seen["prefilters"] = list(prefilters)
        seen["back_alpha"] = style.back_alpha
        return fake_burn(source, cues, output)

    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen", delivery="burned"), factory=Factory,
    )
    caption_jobs.run_caption_job(job["id"], factory=Factory, burn=burn)

    assert seen["prefilters"] == []
    assert seen["back_alpha"] == 0, "opaque, so the original stays hidden"


def test_an_explicit_backdrop_override_survives_a_picture_cover(tmp_path) -> None:
    """Somebody asking for both gets both."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    add_asset(transcript=False, video=video)
    add_on_screen_reading()
    seen: dict = {}

    def burn(source, cues, output, *, style=None, prefilters=()) -> Path:
        seen["back_alpha"] = style.back_alpha
        return fake_burn(source, cues, output)

    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(
            source="on_screen", delivery="burned", cover="solid",
            style_overrides={"back_alpha": 96},
        ),
        factory=Factory,
    )
    caption_jobs.run_caption_job(job["id"], factory=Factory, burn=burn)

    assert seen["back_alpha"] == 96


def test_a_clip_that_was_never_measured_cannot_place_anything() -> None:
    """Shares of the frame need a frame. Refused rather than placed at zero."""
    add_asset(transcript=False)
    add_on_screen_reading()
    with Factory.begin() as session:
        session.get(MediaAsset, "asset1").width = None

    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner",
        request=request(source="on_screen"), factory=Factory,
    )
    try:
        caption_jobs.run_caption_job(job["id"], factory=Factory)
    except RuntimeError as error:
        assert "never measured" in str(error)
    else:
        raise AssertionError("an unmeasured clip placed a line anyway")


def _source_transcript() -> str:
    """A reading to translate, and the asset it belongs to."""
    add_asset()
    with Factory() as session:
        return session.scalar(select(MediaTranscript.id))


def _translate(language: str, *lines: str) -> None:
    from types import SimpleNamespace

    caption_jobs._record_translation(
        Factory,
        workspace_id="ws1",
        asset_id="asset1",
        source_transcript_id=_SOURCE[0],
        kind="speech",
        language=language,
        cues=[
            SimpleNamespace(lines=[line], start_ms=index * 900, end_ms=(index + 1) * 900)
            for index, line in enumerate(lines)
        ],
        job_id=None,
        actor_user_id="owner",
    )


_SOURCE: list[str] = [""]


def test_a_translation_is_filed_as_a_reading_in_that_language() -> None:
    """The gap this closes: a translated clip with nothing to voice.

    Translations lived only as subtitle files named after their language, so
    everything reading transcripts - voiceover most of all - was blind to them.
    A clip captioned into Vietnamese had a Vietnamese track on disk and, as far
    as the model was concerned, nothing said in Vietnamese at all.
    """
    _SOURCE[0] = _source_transcript()

    _translate("vi", "Xin chào", "thế giới")

    with Factory() as session:
        row = session.scalar(
            select(MediaTranscript).where(MediaTranscript.language == "vi")
        )
    assert row is not None
    assert row.text.splitlines() == ["Xin chào", "thế giới"]
    assert row.source_transcript_id == _SOURCE[0]
    # Machine, because it is: a machine read the clip and a machine translated
    # it. The rule keeping unreviewed words out of billed speech still holds -
    # what changes is that there is now something to review.
    assert row.status == "machine"
    assert [cue["start_ms"] for cue in row.segments] == [0, 900]


def test_translating_the_same_reading_twice_replaces_the_track() -> None:
    """Cues are the whole of it, so an older row is a stale copy, not history."""
    _SOURCE[0] = _source_transcript()

    _translate("vi", "first pass")
    _translate("vi", "second pass")

    with Factory() as session:
        rows = session.scalars(
            select(MediaTranscript).where(MediaTranscript.language == "vi")
        ).all()
    assert len(rows) == 1
    assert rows[0].text == "second pass"


def test_one_reading_can_be_translated_into_several_languages() -> None:
    """What the old unique-on-source constraint quietly forbade.

    `source_transcript_id` was unique on its own, which said "a draft is
    reviewed once" and, unintentionally, "a transcript is translated once".
    """
    _SOURCE[0] = _source_transcript()

    for language, word in (("vi", "chào"), ("es", "hola"), ("fr", "salut")):
        _translate(language, word)

    with Factory() as session:
        rows = session.scalars(
            select(MediaTranscript).where(
                MediaTranscript.source_transcript_id == _SOURCE[0]
            )
        ).all()
    assert sorted(row.language for row in rows) == ["es", "fr", "vi"]

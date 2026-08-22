"""Voicing a clip from its own words, as a durable job.

Stage two. Stage one established whether the key works and what is left of the
month's characters; this spends them.

Why it is a job rather than a request
-------------------------------------
A few thousand characters of speech takes longer than a page will wait for, and
it costs money whether or not the browser is still there. So it is queued, run
by the worker, and reported like every other long piece of work here - which
also means a failure lands somewhere the operator can read it rather than in a
console nobody has open.

Where the cost is checked
-------------------------
At the queue, not in the worker. A refusal at the moment somebody clicks can say
"this needs 4,200 characters and 900 are left"; the same refusal twenty minutes
later, from a worker, is a job that failed for reasons the operator has to go
and reconstruct. The worker checks nothing about allowance and simply generates:
by then the decision has been made.

What comes out
--------------
An audio file filed as a `voiceover` version of the asset, and - when asked
for - the clip with that speech on it, filed as `voiced`. Neither is `edited`
or `audio`: the first is what the effects renderer writes and "Remove effects"
deletes, and the second is the clip's own extracted track, which this is
emphatically not - overwriting that would destroy the original's audio while
claiming to add to it.

The two are separate kinds because they are separate artefacts. One is a sound
file to check; one is a cut somebody can post.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations import elevenlabs
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
    report_progress,
)
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "voice_render"
JOB_SESSION_FACTORY = SessionFactory
VOICE_ROOT = PROJECT_ROOT / ".data" / "media" / "voice"
#: The kind a generated voiceover is filed under. Its own, for the reasons in
#: the module docstring.
VERSION_KIND = "voiceover"
#: The clip with that speech on it. A separate kind from the audio, because
#: they are separate artefacts: one is a sound file, one is a cut somebody can
#: post, and an interface offering "the voiceover" cannot mean both.
VOICED_KIND = "voiced"
#: Long enough for a stream copy of a long clip. The video is not re-encoded,
#: so this is disk speed rather than CPU.
MUX_TIMEOUT_SECONDS = 900
#: One attempt. Every retry of a generation is billed again, so a job that
#: failed is something to look at rather than something to run twice.
MAX_ATTEMPTS = 1


def script_for(
    session: Any, workspace_id: str, asset_id: str, *, text: str | None, transcript_id: str | None
) -> tuple[str, str | None]:
    """What to say, and the language it is in.

    Typed text wins when it is given. Otherwise the asset's own reviewed
    transcript - reviewed, not machine: a draft nobody has read is not something
    to spend money voicing, and the Library keeps the two apart precisely so
    this distinction can be made.
    """
    if text and text.strip():
        return text.strip(), None
    query = select(MediaTranscript).where(
        MediaTranscript.asset_id == asset_id,
        MediaTranscript.workspace_id == workspace_id,
        MediaTranscript.kind == "speech",
    )
    if transcript_id:
        found = session.scalar(query.where(MediaTranscript.id == transcript_id))
    else:
        found = session.scalar(
            query.where(MediaTranscript.status == "reviewed").order_by(
                MediaTranscript.created_at.desc()
            )
        )
    if not found or not (found.text or "").strip():
        raise ValueError(
            "This asset has no reviewed transcript to voice. Review one, or type "
            "the script here."
        )
    return found.text.strip(), found.language


def queue(
    workspace_id: str,
    asset_id: str,
    *,
    actor_user_id: str,
    request: dict[str, Any],
    factory: Any = JOB_SESSION_FACTORY,
) -> dict[str, Any]:
    """Record the work, after refusing it if the plan cannot pay for it.

    The same script, voice and model twice is the same job: the id is derived
    from what the audio depends on, so asking again returns the job already
    doing it rather than paying for a second identical generation.
    """
    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id, MediaAsset.workspace_id == workspace_id
            )
        )
        if not asset:
            raise ValueError("Media asset was not found.")
        # Refused here for the same reason the allowance is: there is no
        # picture to put sound on, and finding that out in the worker means
        # finding it out after the speech has been generated and billed. The
        # mux would fail on a missing video stream, and ffmpeg's version of
        # that sentence is not one anybody can act on.
        if request.get("deliver") in {"video", "both"} and asset.media_kind != "video":
            raise ValueError(
                "This asset has no picture to put a voiceover on. Generate the "
                "audio on its own, or choose a video."
            )
        script, language = script_for(
            session,
            workspace_id,
            asset_id,
            text=request.get("text"),
            transcript_id=request.get("transcript_id"),
        )

    voice_id = str(request.get("voice_id") or "").strip()
    if not voice_id:
        raise ValueError("Choose a voice before generating.")
    model_id = str(request.get("model_id") or elevenlabs.DEFAULT_MODEL)

    # Before the job exists, so a refusal is a sentence with a number in it
    # rather than a failed row somebody finds later.
    cost = elevenlabs.check_allowance(script)

    signature = ":".join([workspace_id, asset_id, voice_id, model_id, script])
    job_id = "voice_" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
    with factory() as session:
        if session.get(DurableJob, job_id):
            return get_job_record(job_id, factory=factory)
    return create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "id": job_id,
            "workspace_id": workspace_id,
            "asset_id": asset_id,
            "actor_user_id": actor_user_id,
            "voice_id": voice_id,
            "model_id": model_id,
            "language_code": request.get("language_code") or language,
            "text": script,
            # The same word the caption job uses for the same choice, so the
            # two read alike: the sound on its own, the clip with it on, or
            # both. Audio alone by default - it is the cheap half, and it is
            # what somebody checks before committing to a render.
            "deliver": request.get("deliver") or "audio",
            # Recorded so what was spent is attributable afterwards, and so the
            # figure the operator was shown is the figure on the job.
            "characters": cost,
        },
        max_attempts=MAX_ATTEMPTS,
        factory=factory,
    )


def run_voice_job(
    job_id: str,
    worker_id: str = "voice-worker",
    *,
    factory: Any = None,
    generate: Any = None,
) -> dict[str, Any]:
    factory = factory or JOB_SESSION_FACTORY
    claimed = claim_job(job_id, worker_id, lease_seconds=900, factory=factory)
    payload = dict(claimed["payload"])
    speak = generate or elevenlabs.synthesise
    try:
        report_progress(job_id, 0.1, "Generating speech", factory=factory)
        audio = speak(
            payload["text"],
            voice_id=payload["voice_id"],
            model_id=payload["model_id"],
            language_code=payload.get("language_code"),
        )
        if not audio:
            raise RuntimeError("ElevenLabs returned no audio.")

        report_progress(job_id, 0.8, "Filing the voiceover", factory=factory)
        destination = VOICE_ROOT / payload["workspace_id"]
        destination.mkdir(parents=True, exist_ok=True)
        # Named for the voice as well as the asset, so two takes of the same
        # clip sit beside each other and read as what they are.
        path = destination / f"{payload['asset_id']}.{payload['voice_id']}.mp3"
        path.write_bytes(audio)
        version_id = _record_version(
            payload["workspace_id"], payload["asset_id"], path, factory=factory
        )

        voiced_path: str | None = None
        voiced_id: str | None = None
        if payload.get("deliver") in {"video", "both"}:
            report_progress(job_id, 0.9, "Putting it on the clip", factory=factory)
            with factory() as session:
                asset = session.get(MediaAsset, payload["asset_id"])
                source = Path(asset.original_path) if asset else None
            if source is None or not source.is_file():
                # The audio is already filed and paid for, so this is a partial
                # success rather than a failure: saying "the generation failed"
                # about a generation that worked would send somebody to spend
                # the characters again.
                raise RuntimeError(
                    "The speech was generated and filed, but the original clip "
                    "could not be found to put it on."
                )
            output = destination / f"{payload['asset_id']}.{payload['voice_id']}.mp4"
            mux(source, path, output)
            voiced_path = str(output)
            voiced_id = _record_version(
                payload["workspace_id"], payload["asset_id"], output,
                factory=factory, kind=VOICED_KIND, mime_type="video/mp4",
            )

        return complete_job(
            job_id,
            worker_id,
            {
                "asset_id": payload["asset_id"],
                "version_id": version_id,
                "path": str(path),
                "voiced_version_id": voiced_id,
                "voiced_path": voiced_path,
                "characters": payload.get("characters"),
                "voice_id": payload["voice_id"],
            },
            factory=factory,
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=factory)
        raise


def mux(source: Path, voice: Path, output: Path) -> None:
    """Put the generated speech on the clip, keeping the picture untouched.

    The video is stream-copied, never re-encoded: this is replacing a sound
    track, and re-compressing the picture to do it would cost quality for
    nothing.

    `apad` with `shortest` together are what make the length right. The picture
    is the thing being kept, so the output runs exactly as long as it does:
    `apad` extends the speech with silence if it ends early, and `shortest` cuts
    it if it runs long. Either alone gets it wrong - `shortest` on its own
    truncates the creator's video to the length of the voiceover, which is the
    one outcome nobody wants.
    """
    from trendrelay_api.integrations.openmontage_runtime import FFMPEG

    if not FFMPEG.is_file():
        raise RuntimeError(
            "FFmpeg is not available, so the voiceover cannot be put on the clip. "
            "The audio itself was generated and is filed against the asset."
        )
    with tempfile.TemporaryDirectory(prefix="trendrelay-voice-") as scratch:
        rendered = Path(scratch) / f"voiced{output.suffix or '.mp4'}"
        done = subprocess.run(
            [
                str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                "-i", str(voice),
                # The picture from the clip, the sound from the generation, and
                # nothing of the clip's own audio: this replaces, not mixes.
                # Mixing under music is a different feature with its own
                # decisions about levels, and pretending this is that would give
                # somebody a muddle they did not ask for.
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-af", "apad",
                "-shortest",
                "-movflags", "+faststart",
                str(rendered),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=MUX_TIMEOUT_SECONDS, check=False, stdin=subprocess.DEVNULL,
        )
        if done.returncode != 0 or not rendered.is_file():
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            raise RuntimeError(
                "FFmpeg could not put the voiceover on the clip. "
                + (detail[-1][:300] if detail else "It gave no reason.")
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        rendered.replace(output)


def _record_version(
    workspace_id: str,
    asset_id: str,
    path: Path,
    *,
    factory: Any,
    kind: str = VERSION_KIND,
    mime_type: str = "audio/mpeg",
) -> str:
    """File an artefact as its own kind, and do not file it twice."""
    digest_of = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest_of.update(block)
    digest = digest_of.hexdigest()
    size_bytes = path.stat().st_size
    with factory.begin() as session:
        existing = session.scalar(
            select(MediaAssetVersion).where(
                MediaAssetVersion.asset_id == asset_id,
                MediaAssetVersion.version_kind == kind,
                MediaAssetVersion.sha256 == digest,
            )
        )
        if existing:
            return existing.id
        version = MediaAssetVersion(
            workspace_id=workspace_id,
            asset_id=asset_id,
            version_kind=kind,
            path=str(path),
            sha256=digest,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )
        session.add(version)
        session.flush()
        return version.id


def list_voice_jobs(
    workspace_id: str, limit: int = 30, *, factory: Any = None
) -> list[dict[str, Any]]:
    factory = factory or JOB_SESSION_FACTORY
    return list_job_records(workspace_id, JOB_KIND, limit, factory=factory)

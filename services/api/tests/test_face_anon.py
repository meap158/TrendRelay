"""What the face anonymiser says when it cannot run.

Written from a real run. The tool installs, its runtime reports CUDA, and the
anonymiser still fails - the model it builds on was withdrawn from Hugging
Face. The module already knew how to say that; it was reading the wrong line.
"""

from __future__ import annotations

from trendrelay_api.integrations import face_anon

#: Exactly what the worker printed, trimmed to the shape that matters: the
#: cause, and then the generic suggestion that follows every one of these.
WITHDRAWN_BASE_STDERR = """\
Traceback (most recent call last):
  File "scripts/face_anon_worker.py", line 61, in build_pipeline
    vae=AutoencoderKL.from_pretrained(BASE_REPO, subfolder="vae", **load),
OSError: stabilityai/stable-diffusion-2-1 is not a local folder and is not a \
valid model identifier listed on 'https://huggingface.co/models'
If this is a private repository, make sure to pass a token having permission \
to this repo with `token` or log in with `huggingface-cli login`.\
"""


def test_a_withdrawn_base_model_is_not_reported_as_a_missing_token() -> None:
    """The cause is one line above the last one.

    Matching only the final line answered "log in with huggingface-cli", which
    sends somebody to find a token for a repository that no token can reach.
    """
    last_line = WITHDRAWN_BASE_STDERR.splitlines()[-1]

    explained = face_anon._explain(WITHDRAWN_BASE_STDERR, fallback=last_line)

    assert "withdrawn from Hugging Face" in explained
    assert "huggingface-cli login" not in explained


def test_the_last_line_is_still_what_shows_when_nothing_is_recognised() -> None:
    # An unrecognised failure should report the useful line of the traceback,
    # not the whole of it.
    stderr = "Traceback (most recent call last):\n  ...\nRuntimeError: out of memory"

    explained = face_anon._explain(stderr, fallback="RuntimeError: out of memory")

    assert explained == "RuntimeError: out of memory"


def test_explaining_one_line_still_works_on_its_own() -> None:
    # The single-argument form is what the older callers use.
    assert face_anon._explain("something unrecognised") == "something unrecognised"


def test_a_video_is_declined_rather_than_flickered() -> None:
    """Each face is generated independently, so a clip would strobe.

    Worth a test because producing that and calling it anonymised would be
    worse than refusing: the faces would be gone, and differently gone in
    every frame.
    """
    from pathlib import Path

    import pytest

    with pytest.raises(face_anon.FaceAnonUnavailable, match="not a still image"):
        face_anon.anonymise_image(Path("clip.mp4"), Path("out.mp4"))

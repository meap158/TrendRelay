"""Turning a local analysis provider on from the app rather than a terminal.

The download itself is not exercised here — it fetches hundreds of megabytes
from PyPI and a model from Hugging Face, which is not something a test suite
should do. What is exercised is everything around it: that the app can queue it,
that asking twice does not start two, that the sequence ends with the provider
actually switched on, and that a failure ends up somewhere the operator can read
it. Those are the parts that used to be a sentence of documentation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import sys
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trendrelay_api import media_ai
from trendrelay_api.main import app
from trendrelay_api.models import Base


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.fixture()
def jobs(tmp_path):
    """A database of this test's own, so queued work cannot leak between tests."""
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_a_provider_can_be_queued_without_naming_a_command(jobs) -> None:
    job = media_ai.create_setup_job("speech", actor_user_id="tester", factory=jobs)

    assert job["status"] == "queued"
    assert job["kind"] == media_ai.SETUP_JOB_KIND
    assert job["payload"]["provider"] == "speech"
    assert job["payload"]["tool_id"] == "faster-whisper"
    # Pinned to the versions the status page reports, so what a card says is
    # running is what was asked for.
    assert job["payload"]["packages"] == [f"faster-whisper=={media_ai.SPEECH_VERSION}"]
    assert job["max_attempts"] == media_ai.SETUP_MAX_ATTEMPTS


def test_a_long_setup_keeps_its_lease_alive(jobs, monkeypatch) -> None:
    job = media_ai.create_setup_job("translate", actor_user_id="tester", factory=jobs)
    pulses: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        media_ai,
        "heartbeat_job",
        lambda job_id, worker_id, *, lease_seconds, factory: pulses.append(
            (job_id, worker_id, lease_seconds)
        ),
    )

    with media_ai._keep_setup_lease(
        job["id"], "worker", factory=jobs, interval_seconds=0.01
    ):
        time.sleep(0.03)

    assert pulses
    assert all(pulse == (job["id"], "worker", media_ai.SETUP_LEASE_SECONDS) for pulse in pulses)


def test_asking_twice_joins_the_download_already_running(jobs) -> None:
    """Two pip processes writing one directory is how this actually breaks.

    Clicking again is what an operator does when a long download looks stuck, so
    the second request has to be harmless rather than merely discouraged.
    """
    first = media_ai.create_setup_job("speech", actor_user_id="tester", factory=jobs)
    second = media_ai.create_setup_job("speech", actor_user_id="tester", factory=jobs)

    assert second["id"] == first["id"]
    # A different provider is different work and is not folded into it.
    other = media_ai.create_setup_job("translate", actor_user_id="tester", factory=jobs)
    assert other["id"] != first["id"]


def test_a_finished_attempt_does_not_block_the_next_one(jobs, monkeypatch) -> None:
    """A download that failed must be retryable, or the app is a dead end."""
    monkeypatch.setattr(
        media_ai, "prepare_provider", lambda provider, on_stage=None: (_ for _ in ()).throw(
            RuntimeError("no network")
        )
    )
    first = media_ai.create_setup_job("speech", actor_user_id="tester", factory=jobs)
    with pytest.raises(RuntimeError):
        media_ai.run_setup_job(first["id"], factory=jobs)

    again = media_ai.create_setup_job("speech", actor_user_id="tester", factory=jobs)
    assert again["id"] != first["id"]


def test_a_failure_is_recorded_where_the_operator_can_read_it(jobs, monkeypatch) -> None:
    """pip's reason, not "exit code 1" — they never saw the console it ran in."""
    monkeypatch.setattr(
        media_ai, "prepare_provider", lambda provider, on_stage=None: (_ for _ in ()).throw(
            RuntimeError("No matching distribution found for faster-whisper==1.2.1")
        )
    )
    job = media_ai.create_setup_job("speech", actor_user_id="tester", factory=jobs)
    with pytest.raises(RuntimeError):
        media_ai.run_setup_job(job["id"], factory=jobs)

    recorded = media_ai.latest_setup_jobs(factory=jobs)["speech"]
    assert recorded["status"] == "failed"
    assert "No matching distribution" in recorded["error"]


def test_preparing_ends_with_the_provider_switched_on(monkeypatch) -> None:
    """One decision, one action.

    Downloaded but not activated is the state that produced the original
    complaint: a runtime present on disk and an interface still saying the
    feature is unavailable, with nothing on screen joining the two.
    """
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        media_ai, "list_job_records", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        "trendrelay_api.tool_registry.list_tools",
        lambda: [{"id": "faster-whisper", "installed": False, "active": False}],
    )
    monkeypatch.setattr(
        "trendrelay_api.tool_registry.install_tool",
        lambda tool_id: calls.__setitem__("installed", tool_id),
    )
    monkeypatch.setattr(
        "trendrelay_api.tool_registry.set_active",
        lambda tool_id, active: calls.__setitem__("active", (tool_id, active)),
    )
    monkeypatch.setattr(media_ai, "runtime_ready", lambda provider: False)
    monkeypatch.setattr(
        media_ai, "pip_install", lambda packages: calls.__setitem__("packages", list(packages))
    )
    monkeypatch.setitem(media_ai.PROVIDER_PREPARE, "speech", lambda stage: [])
    stages: list[str] = []

    skipped = media_ai.prepare_provider("speech", on_stage=lambda _, label: stages.append(label))

    assert skipped == []
    assert calls["installed"] == "faster-whisper"
    assert calls["packages"] == [f"faster-whisper=={media_ai.SPEECH_VERSION}"]
    assert calls["active"] == ("faster-whisper", True)
    # Every stage named, because a progress bar with no label is a bar that
    # could be doing anything for twenty minutes.
    assert stages == [
        "Fetching the pinned source",
        "Downloading the runtime",
        "Preparing the model",
        "Switching the provider on",
    ]


def test_a_language_pack_that_will_not_download_is_reported_not_fatal(monkeypatch) -> None:
    """Eleven working directions and one missing beats none of either."""
    monkeypatch.setattr(media_ai, "list_job_records", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "trendrelay_api.tool_registry.list_tools",
        lambda: [{"id": "argos-translate", "installed": True, "active": False}],
    )
    monkeypatch.setattr("trendrelay_api.tool_registry.set_active", lambda tool_id, active: None)
    monkeypatch.setattr(media_ai, "runtime_ready", lambda provider: True)
    monkeypatch.setitem(
        media_ai.PROVIDER_PREPARE, "translate", lambda stage: ["en->ar: no package published"]
    )

    assert media_ai.prepare_provider("translate") == ["en->ar: no package published"]


def test_an_unknown_provider_is_refused_rather_than_queued() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/media-ai/providers/telepathy/prepare",
            json={"confirm_external_action": True},
        )
    )
    assert response.status_code == 404


def test_downloading_a_runtime_needs_explicit_confirmation() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/media-ai/providers/speech/prepare",
            json={"confirm_external_action": False},
        )
    )
    assert response.status_code == 400


def test_a_remote_browser_cannot_start_a_download() -> None:
    """Same rule as installing any other tool: this machine only."""

    async def remote() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/media-ai/providers/speech/prepare",
                json={"confirm_external_action": True},
            )

    assert asyncio.run(remote()).status_code == 403


def test_the_status_endpoint_says_which_of_three_states_a_provider_is_in() -> None:
    """Downloaded-but-off and never-downloaded need different answers from the
    operator, and one "not configured" could not tell them apart."""
    payload = asyncio.run(request("GET", "/api/media-ai/providers")).json()

    speech = payload["providers"]["speech"]
    assert {"prepared", "source_active", "ready", "tool_id"} <= set(speech)
    # Prepared is about the download; ready also requires the switch.
    assert speech["ready"] is (speech["prepared"] and speech["source_active"])
    assert "setup_jobs" in payload


# --- the two names each provider goes by --------------------------------------
#
# `tool_setup` has to look up a running setup job and a readiness report for one
# tool, and those two are keyed differently: a job records `translate` while the
# status report calls the same thing `translation`. It therefore keeps a small
# map of its own, and a map written out by hand beside two it must agree with is
# the kind of thing that drifts silently - the page simply stops offering a
# tool, which reads as the tool being unavailable rather than as a typo.


def test_media_ai_tools_map_covers_every_provider_that_has_a_tool() -> None:
    from trendrelay_api.tool_setup import MEDIA_AI_TOOLS

    assert set(MEDIA_AI_TOOLS) == set(media_ai.PROVIDER_TOOL.values())


def test_each_entry_names_a_provider_setup_jobs_actually_use() -> None:
    # The first half of the pair looks up a running job, which records this key.
    from trendrelay_api.tool_setup import MEDIA_AI_TOOLS

    for tool, (provider, _status_key) in MEDIA_AI_TOOLS.items():
        assert provider in media_ai.PROVIDER_TOOL, tool
        assert media_ai.PROVIDER_TOOL[provider] == tool, (
            f"{tool} says its provider is {provider}, which belongs to "
            f"{media_ai.PROVIDER_TOOL[provider]}"
        )


def test_each_entry_names_a_status_the_report_actually_returns() -> None:
    """The second half indexes `provider_status()`, whose keys differ.

    This is the one that would have caught `translate` being used where
    `translation` was meant.
    """
    from trendrelay_api.tool_setup import MEDIA_AI_TOOLS

    status = media_ai.provider_status()
    for tool, (_provider, status_key) in MEDIA_AI_TOOLS.items():
        assert status_key in status, f"{tool} reads {status_key}, which is not reported"


def test_every_media_ai_tool_can_describe_its_own_setup() -> None:
    # The failure this replaces was an undefined name, so the report raised
    # rather than rendering - for all three tools at once.
    from trendrelay_api.tool_setup import MEDIA_AI_TOOLS, setup_report

    for tool in MEDIA_AI_TOOLS:
        report = setup_report(tool)
        assert report["requirements"], tool
        assert report["actions"], tool


# --- fetching the models -------------------------------------------------------
#
# All offline. What broke in practice was never the download itself: it was a
# credential nobody asked for, and a retry loop with no exit.


class _FakeWhisper:
    """Records how faster-whisper was asked for the model."""

    calls: list[dict] = []

    def __init__(self, model, **kwargs):
        type(self).calls.append({"model": model, **kwargs})


def test_the_model_is_fetched_with_no_credential(monkeypatch, tmp_path) -> None:
    """The Systran models are public, and a token turns that into a 401.

    `huggingface_hub` resolves one from the environment or from whatever
    `huggingface-cli login` last wrote to the home directory, and the Hub
    answers a credentialled request for a public repo with 401 - which arrives
    as RepositoryNotFoundError and reads as "no such model". A token from two
    years ago on this machine was doing exactly that, against a repo that
    resolves fine anonymously.
    """
    _FakeWhisper.calls = []
    monkeypatch.setattr(media_ai, "_runtime_path", lambda: None)
    monkeypatch.setattr(media_ai, "MODEL_ROOT", tmp_path)
    module = type(sys)("faster_whisper")
    module.WhisperModel = _FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    assert media_ai._prepare_speech() == []

    asked = _FakeWhisper.calls[0]
    # False, not None: None means "go and find me a token".
    assert asked["use_auth_token"] is False


class _FakeArgos:
    """argostranslate's package module, with its retry trap intact."""

    def __init__(self, index_path, entries=None, refresh_writes=None):
        self.index_path = index_path
        self.entries = entries or []
        self.refresh_writes = refresh_writes
        self.refresh_calls = 0
        self.reads = 0

    def update_package_index(self):
        # The real one catches every exception and returns without writing.
        self.refresh_calls += 1
        if self.refresh_writes is not None:
            self.index_path.write_text(json.dumps(self.refresh_writes), encoding="utf-8")

    def get_available_packages(self):
        # The trap: absent index -> refresh -> recurse. Faithful on purpose, so
        # a caller that stops guarding blows the stack here instead of in
        # production.
        self.reads += 1
        if not self.index_path.exists():
            self.update_package_index()
            return self.get_available_packages()
        return self.entries


@pytest.fixture()
def argos_index(monkeypatch, tmp_path):
    """Point argostranslate's settings at a directory this test owns."""
    index = tmp_path / "index.json"
    settings = type(sys)("argostranslate.settings")
    settings.local_package_index = index
    settings.remote_package_index = (
        "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
    )
    monkeypatch.setitem(sys.modules, "argostranslate.settings", settings)
    argostranslate = type(sys)("argostranslate")
    argostranslate.settings = settings
    monkeypatch.setitem(sys.modules, "argostranslate", argostranslate)
    return index


def test_an_unreachable_catalogue_fails_instead_of_looping(monkeypatch, argos_index) -> None:
    """The bug this guard exists for.

    argostranslate recurses from get_available_packages into a refresh that
    swallows its own failure, so an unreachable index means about a thousand
    requests and then RecursionError. Against raw.githubusercontent.com the
    loop earns the 429 that keeps it going, and the interface sits on
    "Preparing the model" until something gives out.
    """
    monkeypatch.setattr(media_ai, "_fetch_argos_index", lambda *_: False)
    package = _FakeArgos(argos_index)

    with pytest.raises(RuntimeError, match="could not be downloaded"):
        media_ai._argos_available_packages(package)

    # Refreshed once and never read: reading is what recurses.
    assert package.refresh_calls == 1
    assert package.reads == 0


def test_a_mirror_rescues_a_rate_limited_catalogue(monkeypatch, argos_index) -> None:
    def mirror(_configured, destination):
        destination.write_text(json.dumps([{"from_code": "en"}]), encoding="utf-8")
        return True

    monkeypatch.setattr(media_ai, "_fetch_argos_index", mirror)
    package = _FakeArgos(argos_index, entries=["en->vi"])

    assert media_ai._argos_available_packages(package) == ["en->vi"]


def test_a_catalogue_from_an_earlier_run_is_good_enough(monkeypatch, argos_index) -> None:
    # A stale catalogue still installs packages. Refusing to translate because
    # GitHub is busy would be the worse answer.
    argos_index.write_text(json.dumps([{"from_code": "en"}]), encoding="utf-8")
    monkeypatch.setattr(media_ai, "_fetch_argos_index", lambda *_: False)
    package = _FakeArgos(argos_index, entries=["en->vi"])

    assert media_ai._argos_available_packages(package) == ["en->vi"]


def test_an_unreadable_catalogue_says_so_rather_than_looping(monkeypatch, argos_index) -> None:
    argos_index.write_text("half a fi", encoding="utf-8")
    monkeypatch.setattr(media_ai, "_fetch_argos_index", lambda *_: False)
    package = _FakeArgos(argos_index)

    with pytest.raises(RuntimeError, match="could not be downloaded"):
        media_ai._argos_available_packages(package)


# --- unwrapping what a mirror returns ------------------------------------------


def test_the_api_wraps_the_catalogue_in_base64() -> None:
    # The two mirrors do not answer with the same shape: GitHub's contents
    # endpoint returns the file encoded inside a JSON envelope.
    catalogue = json.dumps([{"from_code": "en", "to_code": "vi"}]).encode()
    envelope = json.dumps({
        "encoding": "base64",
        "content": base64.b64encode(catalogue).decode(),
    }).encode()

    assert json.loads(media_ai._argos_index_payload(envelope)) == [
        {"from_code": "en", "to_code": "vi"}
    ]


def test_a_plain_catalogue_passes_straight_through() -> None:
    body = json.dumps([{"from_code": "en"}]).encode()
    assert media_ai._argos_index_payload(body) == body


@pytest.mark.parametrize(
    "body",
    [
        b"<html>429 Too Many Requests</html>",
        b"",
        b"{}",
        b"[]",
        b'{"encoding": "base64", "content": "not base64 at all!!"}',
    ],
)
def test_what_is_not_a_catalogue_is_refused(body) -> None:
    # A 429 body and an error page are both just bytes. Writing one to the path
    # argostranslate trusts would trade a clear failure for a puzzling one.
    assert media_ai._argos_index_payload(body) is None


def test_an_operators_own_index_is_never_swapped_for_a_mirror(tmp_path) -> None:
    # Mirroring the default catalogue is help; mirroring a source somebody
    # chose deliberately is overriding them.
    assert media_ai._fetch_argos_index(
        "https://mirror.example.internal/argos/index.json", tmp_path / "index.json"
    ) is False


# --- what a failure says to the operator ---------------------------------------

#: The text that actually reached the card, kept verbatim as the case to beat.
HF_401 = (
    "401 Client Error. (Request ID: Root=1-6a830ff0-7393e50640ae27333b61c4a1;"
    "2f04f7e3-09d3-4106-972a-6349ac9a96ad)\n\n"
    "Repository Not Found for url: https://huggingface.co/api/models/"
    "Systran/faster-whisper-base/revision/main.\n"
    "Please make sure you specified the correct `repo_id` and `repo_type`.\n"
    "If you are trying to access a private or gated repo, make sure you are "
    "authenticated and your token has the required permissions.\n"
    'For more details, see https://huggingface.co/docs/huggingface_hub/'
    'authentication\nUser Access Token "First" is expired'
)


class RepositoryNotFoundError(Exception):
    """Stands in for huggingface_hub's, which the test environment need not have."""


def test_the_401_becomes_something_an_operator_can_act_on() -> None:
    """Every word of the raw error is true and only the last clause matters.

    It arrived as a class name, a request id, two lines of advice about
    arguments nobody passed, a docs link, and then the cause.
    """
    message = media_ai.setup_failure(RepositoryNotFoundError(HF_401))

    assert "Request ID" not in message
    assert "repo_type" not in message
    assert "RepositoryNotFoundError" not in message
    # Names the cause and where to look for it.
    assert "HF_TOKEN" in message
    assert "expired token" in message


def test_our_own_wording_is_not_dressed_up_as_a_stack_trace() -> None:
    # These are raised by this module for this reader; prefixing "RuntimeError:"
    # would make a sentence written for an operator look like a crash.
    plain = "The language catalogue could not be downloaded. Wait and try again."
    assert media_ai.setup_failure(RuntimeError(plain)) == plain


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (Exception("HTTP Error 429: Too Many Requests"), "rate-limiting"),
        (OSError("[Errno 11001] getaddrinfo failed"), "reach the internet"),
        (OSError(28, "No space left on device"), "disk is full"),
        (ModuleNotFoundError("No module named 'faster_whisper'"), "will not import"),
        (Exception("Cannot access gated repo for url https://..."), "gated"),
    ],
)
def test_each_recognised_cause_says_what_to_do(error, expected) -> None:
    assert expected in media_ai.setup_failure(error)


def test_an_unrecognised_failure_keeps_the_libraried_own_first_line() -> None:
    # Worth more than a vaguer sentence of ours: it is what makes the next
    # unknown cause diagnosable at all.
    error = ValueError(
        "Invalid model size 'huge', expected one of: tiny, base. "
        "Then a second sentence nobody needs."
    )
    message = media_ai.setup_failure(error)

    assert message == "ValueError: Invalid model size 'huge', expected one of: tiny, base."


def test_a_failure_with_nothing_to_say_still_names_itself() -> None:
    assert media_ai.setup_failure(TimeoutError()) == "TimeoutError."


def test_the_message_is_one_line() -> None:
    # It renders into a single span beside a switch; embedded newlines turned
    # that into a paragraph pushing the rest of the card down.
    message = media_ai.setup_failure(ValueError("first line\n\nsecond line"))
    assert "\n" not in message


# --- the download that never gave up -------------------------------------------


def test_argos_downloads_get_a_deadline_it_does_not_set_itself() -> None:
    """argostranslate calls urlopen with no timeout, and the default is forever.

    A connection that is accepted and then stalls hangs the whole setup. It did:
    eighteen minutes into fetching ru->en, nothing downloaded and nothing
    logged, which from the interface looks exactly like the retry loop above.
    Setting the default is the only way in, because urlopen consults it when the
    caller passes nothing.
    """
    before = socket.getdefaulttimeout()
    with media_ai._socket_deadline(media_ai.ARGOS_SOCKET_TIMEOUT):
        assert socket.getdefaulttimeout() == media_ai.ARGOS_SOCKET_TIMEOUT
    assert socket.getdefaulttimeout() == before


def test_the_deadline_is_lifted_even_when_the_download_fails() -> None:
    # It is a process-wide setting, so leaving it behind would put a timeout on
    # sockets that never asked for one.
    before = socket.getdefaulttimeout()
    with pytest.raises(ValueError), media_ai._socket_deadline(5.0):
        raise ValueError("the download failed")
    assert socket.getdefaulttimeout() == before


def test_preparing_translations_runs_under_the_deadline(monkeypatch) -> None:
    # The guard is worth nothing if the install does not actually sit inside it.
    seen: list[float | None] = []
    monkeypatch.setattr(media_ai, "_runtime_path", lambda: None)
    # The submodule too, not just the package: `from argostranslate import
    # package` reaches for it, and an empty stand-in only passed while some
    # other test had left the real runtime on sys.path.
    argostranslate = type(sys)("argostranslate")
    argostranslate.package = type(sys)("argostranslate.package")
    monkeypatch.setitem(sys.modules, "argostranslate", argostranslate)
    monkeypatch.setitem(sys.modules, "argostranslate.package", argostranslate.package)
    monkeypatch.setattr(
        media_ai,
        "_install_translation_packages",
        lambda package, stage=None: seen.append(socket.getdefaulttimeout()) or [],
    )
    monkeypatch.setattr(media_ai, "_fetch_sentence_splitters", lambda stage=None: [])

    media_ai._prepare_translate()

    assert seen == [media_ai.ARGOS_SOCKET_TIMEOUT]


# --- saying which of twelve downloads is running --------------------------------


class _FakePackages:
    def __init__(self, available):
        self.available = available

    def get_installed_packages(self):
        return []

    def get_available_packages(self):
        return self.available


class _Available:
    def __init__(self, from_code, to_code):
        self.from_code, self.to_code = from_code, to_code

    def download(self):
        return f"{self.from_code}_{self.to_code}.argosmodel"


def test_each_language_download_says_which_one_it_is(monkeypatch) -> None:
    """The complaint was a bar that sat still, not a download that failed.

    These are not uniform: most are around sixty megabytes and ru->en is a
    hundred and fifty-six, which on this host arrived at a fiftieth of the speed
    of the others and took an hour and fifty minutes. Behind one unchanging
    "Preparing the model" that is indistinguishable from a hang - and it is why
    somebody kills a download that was going to finish.
    """
    pairs = media_ai.DEFAULT_TRANSLATION_PAIRS
    packages = _FakePackages([_Available(s, t) for s, t in pairs])
    packages.install_from_path = lambda path: None
    monkeypatch.setattr(media_ai, "_argos_available_packages", lambda p: p.available)
    labels: list[str] = []

    media_ai._install_translation_packages(
        packages, lambda fraction, label: labels.append(label)
    )

    assert len(labels) == len(pairs)
    assert labels[0] == f"Downloading {pairs[0][0]}->{pairs[0][1]} (1 of {len(pairs)})"
    assert labels[-1].endswith(f"({len(pairs)} of {len(pairs)})")
    # The slow one is named rather than hidden behind a count.
    assert any("ru->en" in label for label in labels)


def test_progress_climbs_across_the_downloads(monkeypatch) -> None:
    # A fraction that never moves is the same as no fraction at all.
    packages = _FakePackages([])
    monkeypatch.setattr(media_ai, "_argos_available_packages", lambda p: [])
    seen: list[float] = []

    media_ai._install_translation_packages(
        packages, lambda fraction, label: seen.append(fraction)
    )

    assert seen == sorted(seen)
    assert seen[0] == 0.0
    assert seen[-1] < 1.0


def test_the_prepare_step_reports_across_the_rest_of_the_bar(monkeypatch) -> None:
    """It owns the longest stretch of the job, so it gets the room to say so."""
    monkeypatch.setattr(media_ai, "list_job_records", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "trendrelay_api.tool_registry.list_tools",
        lambda: [{"id": "argos-translate", "installed": True, "active": False}],
    )
    monkeypatch.setattr("trendrelay_api.tool_registry.set_active", lambda tool_id, active: None)
    monkeypatch.setattr(media_ai, "runtime_ready", lambda provider: True)
    def report_both_ends(stage):
        stage(0.0, "first")
        stage(1.0, "last")
        return []

    monkeypatch.setitem(media_ai.PROVIDER_PREPARE, "translate", report_both_ends)
    seen: list[tuple[float, str]] = []

    media_ai.prepare_provider("translate", on_stage=lambda f, label: seen.append((f, label)))

    reported = dict((label, fraction) for fraction, label in seen)
    # Mapped into the room between "Preparing the model" and switching on,
    # rather than overwriting either end.
    assert reported["first"] == pytest.approx(0.6)
    assert reported["last"] == pytest.approx(0.95)
    assert reported["Switching the provider on"] == pytest.approx(0.95)

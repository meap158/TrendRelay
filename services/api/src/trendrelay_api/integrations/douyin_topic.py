"""Turning a trending topic into videos that can actually be downloaded.

The hot board ranks *topics*. Its `group_id` names the topic, not a clip, and
asking the video endpoint for one fails every time — which is why a board card
could only ever open Douyin in a browser and leave the operator copying links
back by hand.

There are two ways to close that gap, and which one is available depends on
something outside this codebase: whether the operator has a Douyin account.

**The hot-topic page needs no account, so it is the default.** Every board entry
carries a `sentence_id`, and `douyin.com/hot/<sentence_id>` serves that topic's
videos to a signed-out visitor. Measured rather than assumed: the same probe
that returned zero videos for a search returned nine here, headless, with no
session at all.

**Search needs one, so it is the fallback.** It ranks better and reaches topics
the board has dropped, but an anonymous session gets `2483 please log in first`
and the search page renders nothing. Anyone without an account is locked out of
this route entirely, which is exactly why it is not the default.

Both end in the same place: real `aweme_id`s handed to the existing download
job. Same worker, same library reconciliation, same audit trail. Nothing about
acquisition changes, only how the URLs are found.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from pydantic import BaseModel, Field, field_validator

from trendrelay_api.integrations.douyin import (
    DownloadRequest,
    create_download_job,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

DOWNLOAD_SCRIPT = PROJECT_ROOT / "scripts" / "douyin.py"
#: Searching is a network round trip per page; the board's own terms are short
#: and an operator wanting hundreds of clips wants a profile, not a topic.
MAX_RESULTS = 30
TIMEOUT_SECONDS = 240
#: `scripts/douyin.py topic` exits with this when the saved session is anonymous.
LOGIN_REQUIRED_EXIT = 5
#: `scripts/douyin.py hot` exit codes.
BROWSER_MISSING_EXIT = 3
EMPTY_TOPIC_EXIT = 4


class TopicUnavailable(RuntimeError):
    """The search could not run, with the reason to show."""

    def __init__(self, message: str, *, login_required: bool = False) -> None:
        super().__init__(message)
        #: Lets the UI offer the sign-in action instead of a bare failure. The
        #: fix is one command, and an operator should not have to guess it.
        self.login_required = login_required


class TopicDownloadRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=80)
    term: str = Field(min_length=1, max_length=120)
    #: The board's hot-topic id. When present the topic page is read instead of
    #: the term being searched, which is what makes this work without a Douyin
    #: account. The term is still carried, for the audit trail and the message.
    sentence_id: str | None = Field(default=None, max_length=40)
    #: How many of the term's videos to take. Deliberately small by default: a
    #: trending topic has thousands of posts and almost none of them are worth
    #: the disk.
    limit: int = Field(default=5, ge=1, le=MAX_RESULTS)
    confirm_external_action: bool = False

    @field_validator("term")
    @classmethod
    def a_real_term(cls, value: str) -> str:
        term = value.strip()
        if not term:
            raise ValueError("Give a term to search for.")
        return term

    @field_validator("sentence_id")
    @classmethod
    def a_real_topic_id(cls, value: str | None) -> str | None:
        # It reaches a URL, so it is checked rather than trusted. Numeric is
        # also what the board always hands out.
        if value is None or not value.strip():
            return None
        topic_id = value.strip()
        if not topic_id.isdigit():
            raise ValueError("A hot-topic id is numeric.")
        return topic_id


def _last_line(text: str | None) -> str:
    """The final non-empty line of the child's stderr.

    stdout is the results channel, so the script sends its progress to stderr
    too: by the time a search fails, stderr already holds `Douyin provider
    ready` and `Douyin cookies ready`. Showing all of it tells an operator the
    provider is fine directly above a message saying it is not. The actionable
    sentence is written last and on one line, so that is what is taken.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DOWNLOAD_SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        # The term and every title are Chinese, and the default Windows codepage
        # cannot decode them; without this the output is mojibake that will not
        # even parse as JSON.
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=TIMEOUT_SECONDS,
    )


def topic_videos(sentence_id: str, limit: int = 10) -> dict[str, Any]:
    """The videos on a hot-topic page. Works with no Douyin account."""
    bounded = max(1, min(limit, MAX_RESULTS))
    try:
        completed = _run(["hot", sentence_id, "--limit", str(bounded)])
    except subprocess.TimeoutExpired as error:
        raise TopicUnavailable(
            f"The topic page did not load within {TIMEOUT_SECONDS} seconds."
        ) from error

    if completed.returncode == BROWSER_MISSING_EXIT:
        raise TopicUnavailable(
            "The browser used to read topic pages is not installed. Connect "
            "Douyin once from Tools and it will be installed automatically."
        )
    if completed.returncode == EMPTY_TOPIC_EXIT:
        raise TopicUnavailable(
            "That topic's page showed no videos. It may have dropped off the board."
        )
    if completed.returncode != 0:
        raise TopicUnavailable(
            _last_line(completed.stderr) or "The topic page could not be read."
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise TopicUnavailable("The topic page came back unreadable.") from error

    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    return {"sentence_id": sentence_id, "count": len(items), "items": items}


def search(term: str, limit: int = 10) -> dict[str, Any]:
    """The videos posted under a term, newest-ranked as Douyin returns them."""
    bounded = max(1, min(limit, MAX_RESULTS))
    try:
        completed = _run(["topic", term, "--limit", str(bounded)])
    except subprocess.TimeoutExpired as error:
        raise TopicUnavailable(
            f"Douyin did not answer within {TIMEOUT_SECONDS} seconds."
        ) from error

    if completed.returncode == LOGIN_REQUIRED_EXIT:
        raise TopicUnavailable(
            _last_line(completed.stderr)
            or "Douyin requires a signed-in account to search.",
            login_required=True,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        raise TopicUnavailable(detail[-400:] or "The term could not be searched.")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise TopicUnavailable("The search results came back unreadable.") from error

    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    return {"term": payload.get("term") or term, "count": len(items), "items": items}


def download_topic(request: TopicDownloadRequest, actor_user_id: str) -> dict[str, Any]:
    """Find a topic's videos and queue them as one ordinary download job.

    The hot-topic page when the board gave a `sentence_id`, because that route
    needs no Douyin account and most operators do not have one. Search only when
    there is no id to use — it is the better ranking and the narrower door.
    """
    if not request.confirm_external_action:
        raise PermissionError(
            "Downloading a topic fetches media from Douyin and needs confirmation."
        )

    if request.sentence_id:
        found = topic_videos(request.sentence_id, limit=request.limit)
        route = "hot-topic-page"
    else:
        found = search(request.term, limit=request.limit)
        route = "search"

    urls = [
        str(item["video_url"])
        for item in found["items"]
        if isinstance(item.get("video_url"), str)
    ][: request.limit]
    if not urls:
        # Said as its own case: a topic that legitimately holds nothing is not a
        # failure of the downloader, and queuing an empty job would leave an
        # operator watching work that could never produce a file.
        raise TopicUnavailable(f"Douyin returned no videos for “{request.term}”.")

    job = create_download_job(
        DownloadRequest(
            workspace_id=request.workspace_id,
            urls=urls,
            # One post per link, so the per-source limit is beside the point;
            # the count was already chosen by how many links were taken.
            mode="post",
            limit=1,
            incremental=True,
            media_kinds=["video"],
            confirm_external_action=True,
        ),
        actor_user_id=actor_user_id,
    )
    return {
        "job": job,
        "term": request.term,
        # Which door this went through. Worth returning: the two routes find
        # different videos, and an operator comparing two runs of the same term
        # should not have to guess why.
        "route": route,
        # Returned so the operator can see which clips were picked rather than
        # trusting a count. The job itself only ever knew the URLs.
        "queued": [item for item in found["items"] if item.get("video_url") in set(urls)],
    }

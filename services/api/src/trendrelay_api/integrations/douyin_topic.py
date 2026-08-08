"""Turning a trending term into videos that can actually be downloaded.

The hot board ranks *topics*. Its `group_id` names the topic, not a clip, and
asking the video endpoint for one fails every time — which is why a board card
could only ever open the search page in a browser and leave the operator to
copy links back by hand.

Searching the term is the missing step. It returns real `aweme_id`s, and an
aweme id is a downloadable link. So this searches, takes the top few, and hands
them to the existing download job: same worker, same library reconciliation,
same audit trail. Nothing about acquisition changes; only how the URLs are found.

Search is also the one Douyin call here that needs a signed-in account. The
anonymous session `connect` captures is enough to download a known link and to
read the board, and Douyin answers `2483 please log in first` to a search made
with it. That is a specific, fixable situation, so it is carried as its own
error rather than flattened into "the provider failed".
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


def search(term: str, limit: int = 10) -> dict[str, Any]:
    """The videos posted under a term, newest-ranked as Douyin returns them."""
    bounded = max(1, min(limit, MAX_RESULTS))
    try:
        completed = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT), "topic", term,
             "--limit", str(bounded)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            # The term and the titles are Chinese, and the default Windows
            # codepage cannot decode them; without this the output is mojibake.
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
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
    """Search a term and queue what it found as one ordinary download job."""
    if not request.confirm_external_action:
        raise PermissionError(
            "Downloading a topic fetches media from Douyin and needs confirmation."
        )

    found = search(request.term, limit=request.limit)
    urls = [
        str(item["video_url"])
        for item in found["items"]
        if isinstance(item.get("video_url"), str)
    ][: request.limit]
    if not urls:
        # Said as its own case: a search that legitimately returns nothing is
        # not a failure of the downloader, and queuing an empty job would leave
        # an operator watching a job that can never produce a file.
        raise TopicUnavailable(f"Douyin returned no videos for “{found['term']}”.")

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
        "term": found["term"],
        # Returned so the operator can see which clips were picked rather than
        # trusting a count. The job itself only ever knew the URLs.
        "queued": [item for item in found["items"] if item.get("video_url") in set(urls)],
    }

"""A job must not be queued on a second connection mid-transaction.

SQLite lets one connection hold the write lock. A request that has written
something - a profile row for a first-time user, a stored recipe - and then
creates a durable job on a connection of its own queues behind its own
uncommitted write, waits out the 15-second busy timeout, and fails with
"database is locked". Applying an effect stack to seventy-one clips did
exactly that: it queued two and appeared to hang.

The test suite cannot catch this. Its database is in-memory with a single
shared connection, where the contention does not exist, so the guard is a read
of the source instead - the same shape as the stylesheet palette check.

`create_job_record` takes a `session` for this reason; the fix is always to
pass the one the request already holds.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "trendrelay_api"

#: Writes on the request's session, or flushes one.
WRITES = re.compile(r"\b(ensure_profile|session\.add|session\.flush|session\.delete)\b")

#: Creates a durable job. Named explicitly, so a new queue function is a
#: deliberate addition to this list rather than something the guard quietly
#: stops covering.
QUEUES = re.compile(
    r"\b(create_render_job|create_blur_job|create_job_record|queue_caption_job"
    r"|queue_media_ai_setup|create_download_job|create_publish_job"
    r"|enqueue_ingest|queue_enrichment|create_montage_job)\s*\("
)

#: The same names, as the parser sees them rather than as text.
QUEUE_NAMES = frozenset({
    "create_render_job", "create_blur_job", "create_job_record", "queue_caption_job",
    "queue_media_ai_setup", "create_download_job", "create_publish_job",
    "enqueue_ingest", "queue_enrichment", "create_montage_job",
})


def _called_name(call: ast.Call) -> str:
    """The bare function name, whether it is called plainly or off a module."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def offenders() -> list[str]:
    """Queue calls that follow a write without joining the request's transaction.

    The handover is read off the call itself. It used to be looked for in the
    fourteen lines after the call started, which is a guess at how long a call
    can be: `submit_batch_render` grew a per-item object and a comment saying
    why it passes the session, which put `session=session` on the nineteenth
    line and made the guard report the one place most careful about this.

    A window is wrong in the other direction too. Fourteen lines is far enough
    to reach the *next* call, so a neighbour's handover could vouch for a call
    that had none. Asking the parser for the keyword removes both.
    """
    found: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            wrote_at: int | None = None
            for offset, line in enumerate(lines[node.lineno - 1 : node.end_lineno]):
                if line.lstrip().startswith("#"):
                    continue
                if WRITES.search(line):
                    wrote_at = node.lineno + offset
                    break
            if wrote_at is None:
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or call.lineno <= wrote_at:
                    continue
                if _called_name(call) not in QUEUE_NAMES:
                    continue
                if any(keyword.arg == "session" for keyword in call.keywords):
                    continue
                found.append(
                    f"{path.name}:{call.lineno} {node.name} queues after writing: "
                    f"{lines[call.lineno - 1].strip()}"
                )
    return found


def test_nothing_queues_a_job_on_a_second_connection_mid_transaction() -> None:
    found = offenders()

    assert found == [], (
        "pass the request's session to these so they join its transaction: "
        + "; ".join(found)
    )

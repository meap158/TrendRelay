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

#: How far after the call to look for the session being handed over, which
#: covers a multi-line call with a comment inside it.
LOOKAHEAD = 14


def offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = lines[node.lineno - 1 : node.end_lineno]
            written = False
            for offset, line in enumerate(body):
                if line.lstrip().startswith("#"):
                    continue
                if not written and WRITES.search(line):
                    written = True
                    continue
                if written and QUEUES.search(line):
                    window = " ".join(body[offset : offset + LOOKAHEAD])
                    if "session=session" in window:
                        break
                    found.append(
                        f"{path.name}:{node.lineno + offset} {node.name} "
                        f"queues after writing: {line.strip()}"
                    )
                    break
    return found


def test_nothing_queues_a_job_on_a_second_connection_mid_transaction() -> None:
    found = offenders()

    assert found == [], (
        "pass the request's session to these so they join its transaction: "
        + "; ".join(found)
    )

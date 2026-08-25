"""Re-open measurement windows that were filled with somebody else's figures.

The Zernio reader used to fall back to the first row of the analytics report
when it could not find the post it was asked about, so a failed lookup was
recorded as a positive observation - another post's numbers, filed against this
one. In practice that row was almost always empty, and 69 executions ended up
holding snapshots that are entirely zero.

Those are not measurements. Left in place they are worse than nothing: a
captured window is never captured again, so the post reads zero on every screen
until its next window comes due, and a post whose windows are all used up reads
zero for ever.

Clearing them costs one re-read each. A post that genuinely got nothing simply
records nothing again, and loses no information by being asked twice - which is
what the snapshot headroom in `campaign_measurement` is there for.

Only executions where *every* snapshot is all-zero are touched. One real figure
anywhere in the series means the reader found the right post, and the series is
left alone.

    python scripts/reset_corrupt_metrics.py            # report, change nothing
    python scripts/reset_corrupt_metrics.py --apply    # do it, after a backup
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = (
    ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
)

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

DB = ROOT / ".data" / "trendrelay.db"


def all_zero(raw: str | None) -> bool:
    """True when every window recorded on this post reads nothing at all."""
    try:
        snapshots = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return False
    if not snapshots:
        return False
    for snapshot in snapshots:
        metrics = snapshot.get("metrics") or {}
        # An empty snapshot is not evidence of a bad read; a zero-valued one is.
        if not metrics or any(metrics.values()):
            return False
    return True


def main(apply: bool) -> int:
    if not DB.is_file():
        print("No database here.")
        return 1

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, provider, platform, published_at, performance_snapshots "
        "FROM publication_executions WHERE state = 'measured'"
    ).fetchall()
    doomed = [row for row in rows if all_zero(row["performance_snapshots"])]
    if not doomed:
        print(f"Nothing to re-open; all {len(rows)} measured posts hold real figures.")
        return 0

    by_provider: dict[str, int] = {}
    stuck = 0
    for row in doomed:
        by_provider[row["provider"]] = by_provider.get(row["provider"], 0) + 1
        # Three windows used up means no pass will ever look at this post again.
        if len(json.loads(row["performance_snapshots"] or "[]")) >= 3:
            stuck += 1

    print(f"{len(doomed)} of {len(rows)} measured posts hold only zeros:")
    for provider, count in sorted(by_provider.items()):
        print(f"  {provider}: {count}")
    print(f"{stuck} of them have used every window and will never re-read on their own.")

    if not apply:
        print("\nNothing was changed. Pass --apply to re-open them.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, DB.with_name(f"{DB.name}.backup-metrics-{stamp}"))
    print(f"\nbacked up the database ({stamp})")
    with db:
        db.executemany(
            "UPDATE publication_executions "
            "SET state = 'published', performance_snapshots = '[]' WHERE id = ?",
            [(row["id"],) for row in doomed],
        )
    left = db.execute(
        "SELECT count(*) FROM publication_executions WHERE state = 'measured'"
    ).fetchone()[0]
    print(f"re-opened {len(doomed)}; {left} measured posts keep their real figures")
    print("The next worker pass reads them again.")
    db.close()
    return 0


if __name__ == "__main__":
    flags = set(sys.argv[1:])
    if flags - {"--apply"}:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(main("--apply" in flags))

"""Totals and percentages in stored coverage lines.

Coverage sentences now lead a multi-profile batch with its whole-batch share
and give every profile its percentage, but job results are frozen records, so
summaries written before that keep the bare counts. Each such row still holds
the ``source_stats`` the sentence was built from, which makes the old sentence
exactly reconstructible: rebuild it, rebuild its replacement, and swap the one
for the other inside the summary. Rows whose stats are missing, or whose
sentence no longer matches the retired format, are left untouched.

Revision ID: 20260901_0061
Revises: 20260901_0060
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op

revision: str = "20260901_0061"
down_revision: str | None = "20260901_0060"
branch_labels: str | None = None
depends_on: str | None = None


def _pct(held: int, total: int) -> str:
    if total <= 0 or held <= 0:
        return "0%"
    if held >= total:
        return "100%"
    return f"{min(99, max(1, round(held * 100 / total)))}%"


def _profiles(stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry for entry in stats
        if entry.get("kind") == "profile" and entry.get("declared_total")
    ]


def _retired_line(profiles: list[dict[str, Any]]) -> str:
    parts = [
        f"{entry.get('nickname') or 'profile'} "
        f"{int(entry.get('held') or 0)}/{int(entry['declared_total'])}"
        for entry in profiles[:3]
    ]
    rest = len(profiles) - 3
    return (
        "Profile coverage: " + ", ".join(parts)
        + (f", and {rest} more" if rest > 0 else "") + "."
    )


def _current_line(profiles: list[dict[str, Any]]) -> str:
    parts = [
        f"{entry.get('nickname') or 'profile'} "
        f"{int(entry.get('held') or 0)}/{int(entry['declared_total'])} "
        f"({_pct(int(entry.get('held') or 0), int(entry['declared_total']))})"
        for entry in profiles[:3]
    ]
    rest = len(profiles) - 3
    listing = ", ".join(parts) + (f", and {rest} more" if rest > 0 else "")
    if len(profiles) == 1:
        return f"Profile coverage: {listing}."
    held_sum = sum(int(entry.get("held") or 0) for entry in profiles)
    total_sum = sum(int(entry["declared_total"]) for entry in profiles)
    return (
        f"Profile coverage: {held_sum}/{total_sum} posts held "
        f"({_pct(held_sum, total_sum)}) - {listing}."
    )


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        "SELECT id, result FROM durable_jobs WHERE kind = 'douyin_download' "
        "AND result LIKE '%Profile coverage: %'"
    ).fetchall()
    for job_id, raw in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        profiles = _profiles(data.get("source_stats") or [])
        if not profiles:
            continue
        summary = data.get("summary") or ""
        retired = _retired_line(profiles)
        if retired not in summary:
            continue
        data["summary"] = summary.replace(retired, _current_line(profiles))
        connection.exec_driver_sql(
            "UPDATE durable_jobs SET result = ? WHERE id = ?",
            (json.dumps(data), job_id),
        )


def downgrade() -> None:
    # The bare-count sentences are not restored: they were copy, not data.
    pass

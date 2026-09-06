"""Early listing reads join one batch.

The first listing runs were queued before jobs carried a batch marker or
their product's name, so the notification bell drew hundreds of rows titled
by job id instead of one card counting what is left. Every unmarked
shopee_enrich job - waiting or already settled - is stamped into a single
backfill batch and given its product's name, so the bell reads the run the
way new runs read.

Revision ID: 20260906_0065
Revises: 20260906_0064
"""

from __future__ import annotations

import json

from alembic import op

revision: str = "20260906_0065"
down_revision: str | None = "20260906_0064"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    names = dict(connection.exec_driver_sql("SELECT id, name FROM products").fetchall())
    rows = connection.exec_driver_sql(
        "SELECT id, payload FROM durable_jobs WHERE kind = 'shopee_enrich'"
    ).fetchall()
    unmarked = []
    for job_id, raw in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            continue
        if not payload.get("batch"):
            unmarked.append((job_id, payload))
    if not unmarked:
        return
    batch = {"id": "shopee-listing-backfill-0065", "total": len(unmarked)}
    for job_id, payload in unmarked:
        payload["batch"] = batch
        if not payload.get("product_name"):
            name = names.get(payload.get("product_id"))
            if name:
                payload["product_name"] = name
        connection.exec_driver_sql(
            "UPDATE durable_jobs SET payload = ? WHERE id = ?",
            (json.dumps(payload), job_id),
        )


def downgrade() -> None:
    # The markers describe the jobs truthfully; nothing to unwind.
    pass

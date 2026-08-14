"""Filling in what a Shopee export does not carry, one product at a time.

The bulk export knows a product's name, price and commission. It does not know
what the product looks like, and an image is the one field a post actually
needs - so the picture has to come from the product page, read as the account
that can see it.

Why this is a job rather than part of the import
------------------------------------------------
Reading one page means rendering it in a browser and waiting for Shopee to fill
it in, which is the better part of a minute. An export of two hundred products
would hold an HTTP request open for hours and lose everything if it dropped. So
the import files the rows immediately, which is the part somebody is waiting
for, and queues the pictures behind it.

One job per product, deliberately. A product whose page has changed, or been
taken down, should cost that product its image and nothing else; a single job
over two hundred products would lose the run to the first bad one.

Nothing here overwrites. A product already carrying an image, or a name
somebody chose, is left exactly as it is - this fills gaps, and a later import
should not undo a correction.
"""

from __future__ import annotations

from secrets import token_urlsafe
from typing import Any

from sqlalchemy import select

from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations import shopee_session
from trendrelay_api.jobs import claim_job, complete_job, create_job_record, fail_job
from trendrelay_api.models import utc_now
from trendrelay_api.opportunity_models import Product

JOB_KIND = "shopee_enrich"

#: Long, because the work is one browser page load and Shopee is not quick.
#: Short of this the lease expires under a job that is still working and the
#: sweep declares it abandoned.
LEASE_SECONDS = 300

#: How many pages one import is willing to open. An export can hold hundreds,
#: and a queue that takes six hours to drain is one nobody trusts; the rest
#: keep their export data and can be enriched by importing again.
MAX_PER_IMPORT = 40


def needs_enrichment(product: Product) -> bool:
    """Whether there is anything to go and look for.

    Only an image and only from a product URL. Everything else the page could
    say, the export already said better.
    """
    return bool(product.product_url) and not product.image_url


def enqueue(
    workspace_id: str,
    products: list[Product],
    *,
    factory: Any = SessionFactory,
    limit: int = MAX_PER_IMPORT,
) -> list[str]:
    """Queue a page read for each product still missing its picture."""
    queued: list[str] = []
    for product in products:
        if len(queued) >= limit:
            break
        if not needs_enrichment(product):
            continue
        job_id = f"shopee-enrich-{token_urlsafe(8)}"
        create_job_record(
            job_id,
            workspace_id,
            JOB_KIND,
            {
                "workspace_id": workspace_id,
                "product_id": product.id,
                "url": product.product_url,
            },
            # Once more, not three times. A page that did not answer is usually
            # a page that will not answer, and a session that has expired will
            # fail identically on every retry across every queued product.
            max_attempts=2,
            factory=factory,
        )
        queued.append(job_id)
    return queued


def progress(workspace_id: str, *, limit: int = 200, factory: Any = SessionFactory) -> dict:
    """How the queued page reads are going, in one answer.

    Counts rather than rows. The question somebody has after an import is "are
    the images coming, and if not why", and a list of forty job records answers
    it worse than four numbers and the reason the failures gave.

    The failure reason is carried once rather than per job because when these
    fail they nearly always fail together and for one cause - the session
    expired partway through the batch - and forty copies of that sentence would
    bury it.
    """
    from trendrelay_api.jobs import list_job_records

    jobs = list_job_records(workspace_id, JOB_KIND, limit, factory=factory)
    counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
    filled = 0
    #: Taken from a job that has given up in preference to one still retrying,
    #: because that one is settled. But taken from a retrying job too: when
    #: these fail it is usually the session, every retry will fail the same way,
    #: and waiting for the attempts to run out before saying so helps nobody.
    problem: str | None = None
    problem_is_final = False
    for job in jobs:
        status = job.get("status") or "queued"
        counts[status] = counts.get(status, 0) + 1
        if status == "succeeded":
            filled += len((job.get("result") or {}).get("filled") or [])
            continue
        error = job.get("error")
        if error and (not problem or (status == "failed" and not problem_is_final)):
            problem, problem_is_final = error, status == "failed"
    pending = counts["queued"] + counts["running"]
    return {
        "pending": pending,
        "succeeded": counts["succeeded"],
        "failed": counts["failed"],
        # Told apart from a settled failure: one is "it may still work", the
        # other is "it will not". They deserve different words on a screen.
        "retrying": sum(
            1 for job in jobs
            if job.get("error") and (job.get("status") or "queued") in {"queued", "running"}
        ),
        # What was actually gained, which is not the same as how many jobs
        # finished: a page that loaded but had nothing new to add succeeds and
        # fills nothing.
        "fields_filled": filled,
        "problem": problem,
        # Whether the fix is to reconnect rather than to retry. Worked out here
        # so the interface does not have to pattern-match an error message.
        "reconnect": bool(problem and shopee_session.looks_like_auth_failure(problem)),
    }


def run_enrich_job(
    job_id: str,
    worker_id: str = "shopee-enrich-worker",
    *,
    factory: Any = SessionFactory,
    fetch: Any = None,
) -> None:
    """Read one product page and fill in the gaps it can close."""
    try:
        record = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=factory)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    reader = fetch or shopee_session.fetch_product
    try:
        found = reader(payload["url"])
        applied = apply_details(payload["workspace_id"], payload["product_id"], found, factory)
        complete_job(job_id, worker_id, applied, factory=factory)
    except Exception as error:
        # Redacted: this message is stored on the job and read back on a screen,
        # and the failure came from a process holding a live session.
        fail_job(job_id, worker_id, shopee_session.redact(str(error)), factory=factory)


def apply_details(
    workspace_id: str,
    product_id: str,
    found: dict[str, Any],
    factory: Any = SessionFactory,
) -> dict[str, Any]:
    """Write what the page said, without disturbing what was already known."""
    filled: list[str] = []
    with factory.begin() as session:
        product = session.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.workspace_id == workspace_id,
            )
        )
        if not product:
            # Deleted while queued. Not a failure: there is simply nothing to
            # fill in any more.
            return {"filled": [], "product_id": product_id, "missing": True}

        image = (found.get("image_url") or "").strip()
        # Only over https, and only what the product page itself pointed at.
        # This URL is handed to a publishing engine to fetch.
        if image.startswith("https://") and not product.image_url:
            product.image_url = image[:2000]
            filled.append("image_url")

        name = (found.get("name") or "").strip()
        # The placeholder a pasted link leaves behind, and nothing else. A name
        # from an export, or one somebody typed, stands.
        if name and product.name.startswith("Shopee ") and not name.startswith("Shopee "):
            product.name = name[:240]
            filled.append("name")

        if filled:
            product.updated_at = utc_now()
    return {"filled": filled, "product_id": product_id}

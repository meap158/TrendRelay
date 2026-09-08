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

import time
from secrets import token_urlsafe
from typing import Any

from sqlalchemy import select

from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations import shopee_listing, shopee_session
from trendrelay_api.jobs import claim_job, complete_job, create_job_record, fail_job
from trendrelay_api.models import DurableJob, utc_now
from trendrelay_api.opportunity_models import Product

JOB_KIND = "shopee_enrich"

#: Breathing room between anonymous page reads. The worker drains these jobs
#: back to back, and five hundred products read politely over half an hour is
#: the same answer as five hundred read rudely in five minutes.
LISTING_DELAY_SECONDS = 2.5

#: Long, because the work is one browser page load and Shopee is not quick.
#: Short of this the lease expires under a job that is still working and the
#: sweep declares it abandoned.
LEASE_SECONDS = 300

def needs_enrichment(product: Product) -> bool:
    """Whether there is anything to go and look for.

    A missing picture, or a listing never read: the product page knows the
    description, pictures, variations, categories, attributes, discount and
    vouchers the export cannot carry, and a product that has never been asked
    about is worth one polite page read.
    """
    return bool(product.product_url) and (
        not product.image_url or not shopee_listing.is_fetched_listing(product.listing)
    )


def enqueue(
    workspace_id: str,
    products: list[Product],
    *,
    factory: Any = SessionFactory,
    limit: int | None = None,
    force: bool = False,
) -> list[str]:
    """Queue a page read for each product still missing something.

    `force` re-reads products whose listing is already stored: a listing is a
    snapshot, and the deliberate refresh is the one caller allowed to say the
    old one no longer serves.
    """
    wanted: list[Product] = []
    for product in products:
        if limit is not None and len(wanted) >= limit:
            break
        if not force and not needs_enrichment(product):
            continue
        if force and not product.product_url:
            continue
        wanted.append(product)
    # One batch marker across the run, sized before anything is queued: the
    # notification bell folds jobs sharing it into one card with a live
    # "n of total done", and a total counted as jobs land would move under
    # the card it is drawn on. A run of one carries it too - the bell shows
    # a single job as its own row, full product name and all.
    batch = {"id": f"shopee-listing-{token_urlsafe(6)}", "total": len(wanted)}
    queued: list[str] = []
    # Commit the complete batch together, never a half-created queue.
    with factory.begin() as session:
        for product in wanted:
            job_id = f"shopee-enrich-{token_urlsafe(8)}"
            create_job_record(
                job_id, workspace_id, JOB_KIND,
                {
                    "workspace_id": workspace_id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "url": product.product_url,
                    "batch": batch,
                },
                max_attempts=2,
                factory=factory,
                session=session,
            )
            queued.append(job_id)
    return queued


def recent_jobs(
    workspace_id: str, *, limit: int = 250, factory: Any = SessionFactory
) -> list[dict[str, Any]]:
    """The rows the notification bell draws, compact on purpose.

    Each job carries the product's name and its batch marker, which is all a
    notification needs: a run of one shows the product in full, a run of many
    folds into one card counting how many are left. Results and full payloads
    stay behind - the bell is polled every few seconds from every tab.
    """
    from trendrelay_api.jobs import list_job_records_including_active

    rows = []
    jobs = list_job_records_including_active(workspace_id, JOB_KIND, limit, factory=factory)
    batch_ids = {((job.get("payload") or {}).get("batch") or {}).get("id") for job in jobs}
    summaries: dict[str, dict[str, int]] = {}
    fetched_jobs: set[str] = set()
    # Narrow columns from *all* members of visible batches. The history cap
    # limits notification rows, never the arithmetic of a batch.
    with factory() as session:
        members = session.execute(select(
            DurableJob.id,
            DurableJob.payload["batch"]["id"].as_string(),
            DurableJob.status, DurableJob.result,
            Product.listing["title"].as_string(),
        ).outerjoin(Product, (
            (Product.id == DurableJob.payload["product_id"].as_string())
            & (Product.workspace_id == DurableJob.workspace_key)
        )).where(
            DurableJob.workspace_key == workspace_id, DurableJob.kind == JOB_KIND,
            (DurableJob.payload["batch"]["id"].as_string().in_([b for b in batch_ids if b])
             | DurableJob.id.in_([job["id"] for job in jobs if not (job.get("payload") or {}).get("batch")])),
        ))
        for job_id, batch_id, status, result, title in members:
            summary = summaries.setdefault(batch_id, {
                "queued": 0, "running": 0, "succeeded": 0, "failed": 0,
                "cancelled": 0, "fetched": 0, "total": 0,
            })
            summary[status] = summary.get(status, 0) + 1
            summary["total"] += 1
            if status == "succeeded" and title and title.strip() and "listing" in (result or {}).get("filled", []):
                summary["fetched"] += 1
                fetched_jobs.add(job_id)
    for job in jobs:
        payload = job.get("payload") or {}
        rows.append({
            "id": job.get("id"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "error": job.get("error"),
            "batch_summary": summaries.get((payload.get("batch") or {}).get("id")),
            "listing_fetched": job.get("id") in fetched_jobs,
            "result": {"filled": (job.get("result") or {}).get("filled", [])},
            "payload": {
                "batch": payload.get("batch"),
                "product_id": payload.get("product_id"),
                "product_name": payload.get("product_name"),
            },
        })
    return rows


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
    # Operational counts cannot be truncated to a page of recent history.
    with factory() as session:
        jobs = [dict(row) for row in session.execute(select(
            DurableJob.status.label("status"), DurableJob.result.label("result"),
            DurableJob.last_error.label("error"),
        ).where(DurableJob.workspace_key == workspace_id, DurableJob.kind == JOB_KIND)).mappings()]
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


def _read_product_page(url: str) -> dict[str, Any]:
    """The public listing first, the connected browser only as a fallback.

    The anonymous page read costs a second and answers with the whole
    listing; the browser bridge costs the better part of a minute and a live
    session, and answers with an image and a name. So the bridge is kept for
    exactly the page that comes back challenged - and when the cheap read
    works, a short pause keeps a batch of five hundred polite.
    """
    try:
        listing = shopee_listing.fetch_listing(url)
    except shopee_listing.ListingUnavailable:
        return shopee_session.fetch_product(url)
    time.sleep(LISTING_DELAY_SECONDS)
    return {
        "listing": listing,
        "image_url": next(iter(listing.get("images") or []), None),
        "name": listing.get("title"),
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
    reader = fetch or _read_product_page
    try:
        found = reader(payload["url"])
        if not shopee_listing.is_fetched_listing(found.get("listing")):
            raise shopee_listing.ListingUnavailable(
                "Shopee returned no usable listing details. The listing has not been fetched."
            )
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

        listing = found.get("listing")
        if shopee_listing.is_fetched_listing(listing):
            # The one field here that replaces rather than fills: the listing
            # is the page's own account of the product, ours to refresh whole,
            # and half of last month's snapshot is not a correction to keep.
            product.listing = listing
            product.listing_fetched_at = utc_now()
            filled.append("listing")

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

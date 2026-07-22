"""Recoverable TrendRelay durable-job worker."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = ROOT / "services" / "api" / "src"
sys.path.insert(0, str(API_SOURCE))

from trendrelay_api.integrations.last30days import run_job  # noqa: E402
from trendrelay_api.integrations.postiz import run_publish_job  # noqa: E402
from trendrelay_api.jobs import recoverable_job_ids  # noqa: E402


def process_available() -> int:
    research_ids = recoverable_job_ids("trend_research")
    publishing_ids = recoverable_job_ids("social_publish")
    for job_id in research_ids:
        run_job(job_id)
    for job_id in publishing_ids:
        run_publish_job(job_id)
    return len(research_ids) + len(publishing_ids)


def worker_main() -> None:
    print("Durable worker ready: trend_research, social_publish", flush=True)
    try:
        while True:
            if process_available() == 0:
                time.sleep(1)
    except KeyboardInterrupt:
        print("Durable worker stopped.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="process currently available work"
    )
    parser.add_argument(
        "--watch", action="store_true", help="reload the worker after code changes"
    )
    args = parser.parse_args()
    if args.once:
        print(f"Processed {process_available()} durable job(s).")
        return 0
    if args.watch:
        from watchfiles import PythonFilter, run_process

        return run_process(
            ROOT / "scripts",
            API_SOURCE,
            target=worker_main,
            watch_filter=PythonFilter(),
            ignore_permission_denied=True,
        )
    worker_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

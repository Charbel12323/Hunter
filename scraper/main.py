"""Pipeline orchestration: FETCH -> NORMALIZE -> DEDUP -> FILTER -> NOTIFY.

Each stage is a separate function so later stages can fill them in without
rewiring, and so dry runs and tests can exercise the seams. Runs are
stateless: state is loaded from the SeenStore at the start and saved at the
end.
"""

import argparse
import logging
import sys

import yaml

from scraper import filters
from scraper import notify as telegram
from scraper.adapters import get_adapter
from scraper.models import Job
from scraper.store import SeenStore

log = logging.getLogger("scraper")


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("Config file %s not found; running with no sources.", path)
        return {}
    if not isinstance(config, dict):
        raise ValueError(f"{path} must contain a YAML mapping, got {type(config).__name__}")
    return config


def fetch_all(sources: list[dict]) -> list[Job]:
    """Fetch every source, each inside its own try/except (bulkhead):
    one broken source must never sink the run."""
    jobs: list[Job] = []
    for source in sources:
        name = source.get("company") or source.get("repo") or source.get("name") or "?"
        label = f"{source.get('type', '?')}/{name}"
        try:
            fetch = get_adapter(source["type"])
            fetched = fetch(source)
            log.info("%s: fetched %d jobs", label, len(fetched))
            jobs.extend(fetched)
        except Exception:
            log.exception("%s: fetch failed; continuing with remaining sources", label)
    return jobs


def normalize(jobs: list[Job]) -> list[Job]:
    # Adapters already return normalized Job objects; this seam exists for
    # any cross-source cleanup that turns out to be needed later.
    return jobs


def dedup(jobs: list[Job], store: SeenStore) -> list[Job]:
    return [job for job in jobs if not store.has(job.id)]


def apply_filters(jobs: list[Job], filters_config: dict) -> list[Job]:
    predicates = filters.build_predicates(filters_config)
    if not predicates:
        return jobs
    kept = [job for job in jobs if filters.keep(job, predicates)]
    if len(kept) != len(jobs):
        log.info("Filters dropped %d of %d new jobs.", len(jobs) - len(kept), len(jobs))
    return kept


def notify(jobs: list[Job], store: SeenStore, dry_run: bool, digest_threshold: int) -> int:
    if len(jobs) > digest_threshold:
        # Digest mode: one summary message instead of flooding the chat.
        if dry_run:
            print(f"DIGEST of {len(jobs)} new jobs:")
            for job in jobs:
                print(f"  - {job.title} @ {job.company} ({job.location})")
        else:
            telegram.send_digest(jobs)
        for job in jobs:
            store.add(job)
        return len(jobs)

    for job in jobs:
        if dry_run:
            print(f"NEW: {job.title} @ {job.company} ({job.location}) -> {job.url}")
        else:
            telegram.send(job)
        # Record only after the message is out: a crash in between re-sends a
        # harmless duplicate, while the reverse order would miss a job.
        store.add(job)
    return len(jobs)


def seed(jobs: list[Job], store: SeenStore) -> None:
    """Silent first-run seeding: an empty store means this is the first run
    ever, so record everything currently posted without notifying. Without
    this, run one would fire a message for every existing posting."""
    for job in jobs:
        store.add(job)
    store.save()
    log.info("First run: seeded %d current postings without notifying.", len(jobs))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scraper.main",
        description="Poll job sources and notify about never-seen-before postings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print would-be notifications to stdout instead of sending to Telegram",
    )
    parser.add_argument("--config", default="sources.yaml", help="path to the sources YAML file")
    parser.add_argument(
        "--store", default="seen_jobs.json", help="path to the seen-jobs state file"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    config = load_config(args.config)
    sources = config.get("sources") or []
    store = SeenStore(args.store)

    fetched = fetch_all(sources)
    normalized = normalize(fetched)

    if normalized and len(store) == 0:
        seed(normalized, store)
        return 0

    filters_config = config.get("filters") or {}
    fresh = dedup(normalized, store)
    matched = apply_filters(fresh, filters_config)
    notified = notify(
        matched, store, args.dry_run, digest_threshold=filters_config.get("digest_threshold", 10)
    )
    # Record filtered-out jobs as seen too (after notify, so a crash can't
    # mark a matched job seen before its message went out). Otherwise every
    # filtered job re-enters the diff as "new" on every run forever.
    for job in fresh:
        if not store.has(job.id):
            store.add(job)
    store.save()

    log.info(
        "Run complete: %d sources, %d fetched, %d new jobs, %d notified.",
        len(sources),
        len(fetched),
        len(fresh),
        notified,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

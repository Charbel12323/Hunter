"""Pipeline orchestration: FETCH -> NORMALIZE -> DEDUP -> FILTER -> NOTIFY.

Each stage is a separate function so later stages can fill them in without
rewiring, and so dry runs and tests can exercise the seams. Runs are
stateless: state is loaded from the SeenStore at the start and saved at the
end.
"""

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import requests
import yaml

from scraper import filters, health
from scraper import notify as telegram
from scraper.adapters import get_adapter
from scraper.models import Job
from scraper.store import SeenStore

log = logging.getLogger("scraper")

# Waits between retry attempts on timeouts and 5xx. Per-source and small so
# the whole run still finishes quickly even with a flaky source.
RETRY_WAITS = (1, 4, 16)
PRUNE_MAX_AGE_DAYS = 60


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


def fetch_all(sources: list[dict]) -> tuple[list[Job], dict[str, dict]]:
    """Fetch every source, each inside its own try/except (bulkhead):
    one broken source must never sink the run. Returns the jobs plus
    per-source stats for the run summary and health tracking."""
    jobs: list[Job] = []
    stats: dict[str, dict] = {}
    for source in sources:
        name = source.get("company") or source.get("repo") or source.get("name") or "?"
        label = f"{source.get('type', '?')}/{name}"
        stat = stats.setdefault(label, {"fetched": 0, "errors": 0})
        try:
            fetch = get_adapter(source["type"])
            fetched = fetch_with_retry(fetch, source, label)
            stat["fetched"] += len(fetched)
            log.info("%s: fetched %d jobs", label, len(fetched))
            jobs.extend(fetched)
        except Exception:
            stat["errors"] += 1
            log.exception("%s: fetch failed; continuing with remaining sources", label)
    return jobs, stats


def fetch_with_retry(fetch: Callable, config: dict, label: str) -> list[Job]:
    """Retry timeouts and 5xx with exponential backoff. 4xx fails fast:
    it means the source config is wrong, and retrying won't fix that."""
    attempts = len(RETRY_WAITS) + 1
    for attempt in range(attempts):
        try:
            return fetch(config)
        except Exception as exc:
            if attempt == attempts - 1 or not _retryable(exc):
                raise
            wait = RETRY_WAITS[attempt]
            log.warning(
                "%s: attempt %d/%d failed (%s); retrying in %ds",
                label, attempt + 1, attempts, exc, wait,
            )
            time.sleep(wait)
    raise AssertionError("unreachable")


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        return response is not None and response.status_code >= 500
    return isinstance(exc, requests.exceptions.ConnectionError | requests.exceptions.Timeout)


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


def notify(
    jobs: list[Job],
    store: SeenStore,
    dry_run: bool,
    digest_threshold: int,
    people_location: str | None = None,
) -> list[Job]:
    """Send each job (or one digest), returning the jobs actually sent.
    A job whose send failed is NOT recorded as seen, so it retries next
    run - never-miss beats never-duplicate."""
    if not jobs:
        return []

    if len(jobs) > digest_threshold:
        # Digest mode: one summary message instead of flooding the chat.
        if dry_run:
            print(f"DIGEST of {len(jobs)} new jobs:")
            for job in jobs:
                print(f"  - {job.title} @ {job.company} ({job.location})")
        else:
            try:
                telegram.send_digest(jobs, people_location)
            except Exception:
                log.exception("Digest send failed; jobs stay unseen and retry next run.")
                return []
        for job in jobs:
            store.add(job)
        return jobs

    sent = []
    for job in jobs:
        try:
            if dry_run:
                print(f"NEW: {job.title} @ {job.company} ({job.location}) -> {job.url}")
            else:
                telegram.send(job, people_location)
        except Exception:
            log.exception("Send failed for %s; it stays unseen and retries next run.", job.id)
            continue
        # Record only after the message is out: a crash in between re-sends a
        # harmless duplicate, while the reverse order would miss a job.
        store.add(job)
        sent.append(job)
    return sent


def seed_new_sources(
    fresh: list[Job], normalized: list[Job], store: SeenStore, max_age_days: int | None = None
) -> list[Job]:
    """Seed sources that are new to the watchlist, returning the jobs still
    eligible for notification.

    A source with no previously-seen jobs at all was just added (or fetched
    successfully for the first time); alerting would replay its entire
    backlog. But a backlog posting dated within the max_age_days window is
    exactly what the watchlist exists to surface - swallowing it means never
    hearing about a days-old opening that is still live - so those stay in
    the pipeline (digest mode caps a burst) and only older or undated backlog
    is seeded silently. Recognizing sources by "has at least one seen job"
    (rather than by health history) keeps the never-miss rule intact for
    existing sources recovering from an outage."""
    seen_sources = {job.source for job in normalized if store.has(job.id)}
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days) if max_age_days else None
    backlog_ids: set[str] = set()
    kept: Counter[str] = Counter()
    for job in fresh:
        if job.source in seen_sources:
            continue
        if _posted_since(job.posted_at, cutoff):
            kept[job.source] += 1
        else:
            backlog_ids.add(job.id)
            store.add(job)
    if not backlog_ids:
        return fresh
    seeded = Counter(job.source for job in fresh if job.id in backlog_ids)
    for label, count in sorted(seeded.items()):
        log.info(
            "%s: new source; seeded %d existing postings silently, kept %d recent.",
            label,
            count,
            kept[label],
        )
    return [job for job in fresh if job.id not in backlog_ids]


def _posted_since(posted_at: str | None, cutoff: datetime | None) -> bool:
    """True only for a parseable date inside the window. Unlike the age
    filter, an undated posting does NOT count as recent here: "keep when the
    date is missing" would replay entire boards whose adapters carry no
    dates, defeating the silent seed."""
    if cutoff is None or not posted_at:
        return False
    try:
        posted = datetime.fromisoformat(posted_at)
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return posted >= cutoff


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

    fetched, stats = fetch_all(sources)
    normalized = normalize(fetched)
    warnings = health.record_run(store.health, stats)

    if normalized and len(store) == 0:
        seed(normalized, store)
        return 0

    filters_config = config.get("filters") or {}
    fresh = dedup(normalized, store)
    fresh = seed_new_sources(fresh, normalized, store, filters_config.get("max_age_days"))
    matched = apply_filters(fresh, filters_config)
    sent = notify(
        matched,
        store,
        args.dry_run,
        digest_threshold=filters_config.get("digest_threshold", 10),
        people_location=filters_config.get("people_location"),
    )
    # Record filtered-out jobs as seen too (after notify, so a crash can't
    # mark a matched job seen before its message went out). Otherwise every
    # filtered job re-enters the diff as "new" on every run forever. Jobs
    # whose send failed are deliberately left unseen so they retry.
    matched_ids = {job.id for job in matched}
    for job in fresh:
        if job.id not in matched_ids and not store.has(job.id):
            store.add(job)

    # Prune at the very end, after notifications and state updates, so it
    # can never race the dedup.
    store.prune(PRUNE_MAX_AGE_DAYS)
    store.save()

    for message in warnings:
        try:
            if args.dry_run:
                print(f"WARNING: {message}")
            else:
                telegram.send_text(f"Health warning: {message}")
        except Exception:
            log.exception("Failed to send health warning.")

    summarize(stats, fresh, matched, sent)
    log.info(
        "Run complete: %d sources, %d fetched, %d new jobs, %d notified.",
        len(sources),
        len(fetched),
        len(fresh),
        len(sent),
    )
    return 0


def summarize(stats: dict, fresh: list[Job], matched: list[Job], sent: list[Job]) -> None:
    """One readable line per source - the primary observability surface in
    the Actions logs."""
    new_by = Counter(job.source for job in fresh)
    matched_by = Counter(job.source for job in matched)
    sent_by = Counter(job.source for job in sent)
    for label in sorted(stats):
        stat = stats[label]
        log.info(
            "%s: fetched=%d new=%d filtered_out=%d notified=%d errors=%d",
            label,
            stat["fetched"],
            new_by[label],
            new_by[label] - matched_by[label],
            sent_by[label],
            stat["errors"],
        )


if __name__ == "__main__":
    sys.exit(main())

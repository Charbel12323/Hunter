"""Composable predicate filters (Strategy pattern).

Each rule is a predicate ``Job -> bool`` (True = keep). ``build_predicates``
returns only the predicates the config enables; a job must pass all of them.
A new rule type (max posting age, salary floor, ...) is one new block here -
existing predicates are never edited.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from scraper.models import Job
from scraper.regions import region

Predicate = Callable[[Job], bool]


def build_predicates(config: dict) -> list[Predicate]:
    predicates: list[Predicate] = []

    # Bind each word list via a default argument: these lambdas capture by
    # reference, and a shared local reused across blocks would leak one
    # rule's words into another.
    if include := config.get("include_keywords"):
        words = [w.lower() for w in include]
        predicates.append(lambda job, words=words: any(w in job.title.lower() for w in words))

    if exclude := config.get("exclude_keywords"):
        words = [w.lower() for w in exclude]
        predicates.append(
            lambda job, words=words: not any(w in job.title.lower() for w in words)
        )

    # Regexes for what substrings can't express safely: level suffixes like
    # "II" (a bare "ii" substring would hit "IIoT") and "TS/SCI" spellings.
    if patterns := config.get("exclude_patterns"):
        pattern = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
        predicates.append(lambda job, pattern=pattern: not pattern.search(job.title))

    if locations := config.get("locations"):
        places = [place.lower() for place in locations]
        predicates.append(
            lambda job, places=places: any(place in job.location.lower() for place in places)
        )

    # Coarse country allowlist via the same classifier notify groups by.
    # "Remote" with no country and an empty location both pass: neither
    # rules the job out, and never-miss beats never-duplicate.
    if regions := config.get("regions"):
        allowed = {r.lower() for r in regions}
        predicates.append(lambda job, allowed=allowed: _in_regions(job.location, allowed))

    # Aggregator feeds backfill and reactivate old postings, which enter the
    # diff as "new" despite being posted long ago. This drops anything whose
    # posted date is older than the window; undated jobs pass (never-miss).
    if max_age_days := config.get("max_age_days"):
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        predicates.append(lambda job, cutoff=cutoff: _posted_within(job.posted_at, cutoff))

    return predicates


def _in_regions(location: str, allowed: set[str]) -> bool:
    if region(location) in allowed:
        return True
    return not location.strip() or location.strip().lower() == "remote"


def _posted_within(posted_at: str | None, cutoff: datetime) -> bool:
    if not posted_at:
        return True  # no date: keep, never-miss beats never-duplicate
    try:
        posted = datetime.fromisoformat(posted_at)
    except ValueError:
        return True
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return posted >= cutoff


def keep(job: Job, predicates: list[Predicate]) -> bool:
    return all(predicate(job) for predicate in predicates)

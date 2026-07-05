"""Composable predicate filters (Strategy pattern).

Each rule is a predicate ``Job -> bool`` (True = keep). ``build_predicates``
returns only the predicates the config enables; a job must pass all of them.
A new rule type (max posting age, salary floor, ...) is one new block here -
existing predicates are never edited.
"""

from collections.abc import Callable

from scraper.models import Job

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

    if locations := config.get("locations"):
        places = [place.lower() for place in locations]
        predicates.append(
            lambda job, places=places: any(place in job.location.lower() for place in places)
        )

    return predicates


def keep(job: Job, predicates: list[Predicate]) -> bool:
    return all(predicate(job) for predicate in predicates)

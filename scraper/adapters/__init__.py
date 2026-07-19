"""Adapter registry: maps a sources.yaml ``type:`` string to a fetch function.

Every adapter module exposes ``fetch(config: dict) -> list[Job]`` and is
registered here. Adding a source type = one new adapter file + one entry in
REGISTRY; the orchestrator dispatches purely by type string.
"""

from collections.abc import Callable

from scraper.adapters import (
    amazon,
    ashby,
    github_repo,
    google_careers,
    greenhouse,
    lever,
    microsoft,
    workday,
)
from scraper.models import Job

REGISTRY: dict[str, Callable[[dict], list[Job]]] = {
    "ashby": ashby.fetch,
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "github": github_repo.fetch,
    "workday": workday.fetch,
    "amazon": amazon.fetch,
    "google": google_careers.fetch,
    "microsoft": microsoft.fetch,
}


def get_adapter(type_str: str) -> Callable[[dict], list[Job]]:
    try:
        return REGISTRY[type_str]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none registered yet)"
        raise KeyError(
            f"Unknown source type {type_str!r} in sources.yaml. Known types: {known}"
        ) from None

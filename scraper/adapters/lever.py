"""Lever adapter.

Public, unauthenticated postings API:
https://api.lever.co/v0/postings/{company}?mode=json

Watch the migration trap: a company that left Lever keeps an empty board
that returns 200 with zero postings forever (e.g. kraken, plaid). Confirm
where a company's Apply links point before adding it to sources.yaml.
"""

from datetime import UTC, datetime

import requests

from scraper.models import Job

API_URL = "https://api.lever.co/v0/postings/{company}"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30


def fetch(config: dict) -> list[Job]:
    company = config["company"]
    response = requests.get(
        API_URL.format(company=company),
        params={"mode": "json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    postings = response.json()

    return [
        Job(
            id=f"lever:{company}:{posting['id']}",
            title=posting.get("text", ""),
            company=company,
            location=_location(posting.get("categories") or {}),
            url=posting.get("hostedUrl", ""),
            posted_at=_iso_date(posting.get("createdAt")),
            description=" ".join((posting.get("descriptionPlain") or "").split())[
                :DESCRIPTION_LIMIT
            ],
            source=f"lever/{company}",
        )
        for posting in postings
    ]


def _location(categories: dict) -> str:
    all_locations = categories.get("allLocations")
    if all_locations:
        return "; ".join(all_locations)
    return categories.get("location") or ""


def _iso_date(created_at_ms: int | None) -> str | None:
    if not created_at_ms:
        return None
    return datetime.fromtimestamp(created_at_ms / 1000, tz=UTC).isoformat(timespec="seconds")

"""GitHub-repo adapter for curated listing files (e.g. SimplifyJobs).

Fetches a raw JSON listings file from a repo and maps each active listing to
a Job. Sends a conditional request (If-None-Match) with an ETag cached in a
local, gitignored file: a 304 means the file is byte-identical to what a
previous run already processed, so every listing in it is in the seen store
and returning [] is safe. On a fresh CI VM the cache is absent and a full
fetch happens - erring toward over-fetching, never toward missing a job.

Config:
    type: github
    repo: SimplifyJobs/New-Grad-Positions
    path: .github/scripts/listings.json
    branch: dev            # optional, default "main"
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

import requests

from scraper.models import Job

log = logging.getLogger(__name__)

RAW_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"
ETAG_CACHE_PATH = ".etag_cache.json"
TIMEOUT_SECONDS = 60


def fetch(config: dict) -> list[Job]:
    repo = config["repo"]
    url = RAW_URL.format(repo=repo, branch=config.get("branch", "main"), path=config["path"])
    cache_path = config.get("etag_cache_path", ETAG_CACHE_PATH)

    cache = _load_cache(cache_path)
    headers = {"If-None-Match": cache[url]} if url in cache else {}

    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    if response.status_code == 304:
        log.info("github/%s: file unchanged since last run (ETag match); skipping parse.", repo)
        return []
    response.raise_for_status()
    if etag := response.headers.get("ETag"):
        _save_cache(cache_path, cache | {url: etag})

    jobs = []
    for listing in response.json():
        if listing.get("active") is False or listing.get("is_visible") is False:
            continue
        listing_url = listing.get("url", "")
        listing_id = listing.get("id") or hashlib.sha256(listing_url.encode()).hexdigest()[:16]
        jobs.append(
            Job(
                id=f"github:{repo}:{listing_id}",
                title=listing.get("title", ""),
                company=listing.get("company_name", ""),
                location="; ".join(listing.get("locations") or []),
                url=listing_url,
                posted_at=_iso_date(listing.get("date_posted")),
                description="",
                source=f"github/{repo}",
            )
        )
    return jobs


def _load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_cache(path: str, cache: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def _iso_date(epoch_seconds: int | None) -> str | None:
    if not epoch_seconds:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat(timespec="seconds")

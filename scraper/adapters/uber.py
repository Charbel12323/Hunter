"""Uber careers adapter.

Uber's career site (jobs.uber.com) is a Next.js app sitting behind
Cloudflare. The page itself issues managed-challenge pages to non-browser
clients on rapid/unusual traffic, but the JSON API its own search form
calls - ``/api/jobs/search`` - answers plain GETs fine as long as a normal
browser User-Agent is sent (no cookies, no JS challenge solving; the same
courtesy every other adapter here already extends). Results aren't sorted
newest-first (relevance ranking from the backing search index instead), but
per-location job volume is low enough that fetching every page each poll is
cheap and never-miss.

Config:
    type: uber
    name: canada               # optional; used in the source label, default "careers"
    country: Canada            # optional; server-side country filter
    pages: 3                   # optional; 10 jobs per page
"""

import html
import re

import requests

from scraper.models import Job

API_URL = "https://jobs.uber.com/api/jobs/search/"
BASE_URL = "https://jobs.uber.com"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30
DEFAULT_PAGES = 3
PAGE_SIZE = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch(config: dict) -> list[Job]:
    source = f"uber/{config.get('name') or 'careers'}"

    jobs: dict[str, Job] = {}  # keyed by id: defensive against shifting pages
    total_pages = None
    for page in range(1, config.get("pages", DEFAULT_PAGES) + 1):
        params = {"page": page}
        if country := config.get("country"):
            params["countries"] = country
        response = requests.get(
            API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        total_pages = payload.get("totalPages", total_pages)
        postings = payload.get("jobs") or []

        for posting in postings:
            job = _job(posting, source)
            jobs.setdefault(job.id, job)
        if len(postings) < PAGE_SIZE or (total_pages is not None and page >= total_pages):
            break
    return list(jobs.values())


def _job(posting: dict, source: str) -> Job:
    job_id = posting["Id"]
    urls = posting.get("Urls") or []
    path = next((u["Url"] for u in urls if u.get("IsDefault")), None) or (
        urls[0]["Url"] if urls else f"/en/jobs/{job_id}/"
    )
    return Job(
        id=f"uber:uber:{job_id}",
        title=posting.get("Title", ""),
        company="uber",
        location="; ".join(loc.get("Address", "") for loc in posting.get("Locations") or []),
        url=BASE_URL + path,
        posted_at=posting.get("DisplayDate"),  # already real ISO 8601, e.g. "...Z"
        description=_text(posting.get("Description") or ""),
        source=source,
    )


def _text(markup: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return " ".join(text.split())[:DESCRIPTION_LIMIT]

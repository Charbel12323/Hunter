"""Rivian careers adapter.

Rivian's career site (careers.rivian.com) is a client-rendered app backed
by an iCIMS/Jibe job-search API at ``/api/jobs`` - unauthenticated GET,
JSON response, no bot mitigation encountered. ``sortBy=relevance`` is the
site's own default; job volume in any one location is low enough that
fetching every page each poll (rather than relying on sort order to cut
pagination short) is cheap and never-miss.

Config:
    type: rivian
    name: canada               # optional; used in the source label, default "careers"
    location: Canada           # optional; server-side location filter
    pages: 3                   # optional; 10 jobs per page
"""

import html
import re
from datetime import datetime

import requests

from scraper.models import Job

API_URL = "https://careers.rivian.com/api/jobs"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30
DEFAULT_PAGES = 3
PAGE_SIZE = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch(config: dict) -> list[Job]:
    source = f"rivian/{config.get('name') or 'careers'}"

    jobs: dict[str, Job] = {}  # keyed by id: defensive against shifting pages
    total = None
    for page in range(1, config.get("pages", DEFAULT_PAGES) + 1):
        params = {
            "page": page,
            "sortBy": "relevance",
            "descending": "false",
            "internal": "false",
            "tags2": "Rivian Automotive",
        }
        if location := config.get("location"):
            params["location"] = location
        response = requests.get(
            API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        total = payload.get("totalCount", total)
        postings = payload.get("jobs") or []

        for posting in postings:
            job = _job(posting["data"], source)
            jobs.setdefault(job.id, job)
        if len(postings) < PAGE_SIZE or (total is not None and len(jobs) >= total):
            break
    return list(jobs.values())


def _job(data: dict, source: str) -> Job:
    job_id = data.get("req_id") or data["slug"]
    return Job(
        id=f"rivian:rivian:{job_id}",
        title=data.get("title", ""),
        company="rivian",
        location=data.get("full_location") or data.get("short_location") or "",
        url=(data.get("meta_data") or {}).get("canonical_url") or data.get("apply_url", ""),
        posted_at=_iso_date(data.get("posted_date")),
        description=_text(data.get("description") or ""),
        source=source,
    )


def _iso_date(posted_date: str | None) -> str | None:
    # "2026-05-27T21:45:00+0000" -> ISO with a real timezone offset.
    if not posted_date:
        return None
    try:
        return datetime.strptime(posted_date, "%Y-%m-%dT%H:%M:%S%z").isoformat(
            timespec="seconds"
        )
    except ValueError:
        return None


def _text(markup: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return " ".join(text.split())[:DESCRIPTION_LIMIT]

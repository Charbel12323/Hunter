"""Google careers adapter.

Google runs a custom career site with no public board API, and the old
careers.google.com/api/v3 endpoint is gone. The search results page IS
server-rendered for SEO though, with the result set embedded as JSON in an
``AF_initDataCallback({key: 'ds:1', data: ...})`` script blob; this adapter
fetches the page sorted by newest first and parses that blob.

Job entries in the blob are positional arrays with no keys. The indexes
below were mapped by inspecting live pages and can move if Google reshapes
the payload, so parsing is defensive: a malformed entry is skipped, but a
page with no blob at all raises, which surfaces in health tracking instead
of silently reporting zero jobs forever.

Config:
    type: google
    name: canada               # optional; used in the source label, default "careers"
    location: Canada           # optional; server-side location filter
    query: software engineer   # optional; narrows the search at the source
    pages: 2                   # optional; 20 jobs per page, newest first
"""

import html
import json
import logging
import re
from datetime import UTC, datetime

import requests

from scraper.models import Job

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results"
JOB_URL = "https://www.google.com/about/careers/applications/jobs/results/{job_id}"
BLOB_RE = re.compile(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", re.S)
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30
DEFAULT_PAGES = 2
PAGE_SIZE = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Positional indexes of one job entry inside the ds:1 blob.
ID, TITLE, COMPANY, LOCATIONS, SUMMARY, CREATED_AT = 0, 1, 7, 9, 10, 12


def fetch(config: dict) -> list[Job]:
    source = f"google/{config.get('name') or 'careers'}"
    jobs: dict[str, Job] = {}  # keyed by id: entries can repeat across pages
    for page in range(1, config.get("pages", DEFAULT_PAGES) + 1):
        params = {"sort_by": "date", "page": page}
        if query := config.get("query"):
            params["q"] = query
        if location := config.get("location"):
            params["location"] = location
        response = requests.get(
            SEARCH_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        match = BLOB_RE.search(response.text)
        if not match:
            raise ValueError("google careers page layout changed: 'ds:1' data blob not found")
        entries = json.loads(match.group(1))[0] or []

        for entry in entries:
            if job := _job(entry, source):
                jobs.setdefault(job.id, job)
        if len(entries) < PAGE_SIZE:
            break
    return list(jobs.values())


def _job(entry: list, source: str) -> Job | None:
    try:
        job_id = str(entry[ID])
        title = entry[TITLE] or ""
        company = entry[COMPANY] or "Google"
        location = "; ".join(loc[0] for loc in (entry[LOCATIONS] or []) if loc and loc[0])
        summary = (entry[SUMMARY] or [None, ""])[1] or ""
        created = entry[CREATED_AT]
    except (IndexError, TypeError):
        log.warning("%s: skipping malformed job entry", source)
        return None
    return Job(
        id=f"google:google:{job_id}",
        title=title,
        company=company,
        location=location,
        url=JOB_URL.format(job_id=job_id),
        posted_at=_iso_date(created),
        description=_text(summary),
        source=source,
    )


def _iso_date(timestamp: list | None) -> str | None:
    # Timestamps arrive as [epoch_seconds, nanos].
    if not timestamp or not timestamp[0]:
        return None
    return datetime.fromtimestamp(timestamp[0], tz=UTC).isoformat(timespec="seconds")


def _text(markup: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return " ".join(text.split())[:DESCRIPTION_LIMIT]

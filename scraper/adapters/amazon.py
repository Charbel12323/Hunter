"""Amazon careers adapter.

Amazon runs a custom career site (amazon.jobs) with no official board API,
but the site's own search box is backed by an unauthenticated JSON endpoint:
https://www.amazon.jobs/en/search.json?sort=recent&base_query=...

``sort=recent`` puts the newest postings first, so one page of 100 results
is plenty between polls. The endpoint rejects non-browser User-Agents, so a
browser-like UA is sent.

Config:
    type: amazon
    name: canada               # optional; used in the source label, default "jobs"
    country: CAN               # optional ISO-3 code; server-side country filter
    query: software engineer   # optional; narrows the search at the source
"""

import html
import re
from datetime import datetime

import requests

from scraper.models import Job

API_URL = "https://www.amazon.jobs/en/search.json"
BASE_URL = "https://www.amazon.jobs"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30
RESULT_LIMIT = 100
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch(config: dict) -> list[Job]:
    params = {"sort": "recent", "result_limit": RESULT_LIMIT, "offset": 0}
    if query := config.get("query"):
        params["base_query"] = query
    if country := config.get("country"):
        params["country"] = country

    response = requests.get(
        API_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    postings = response.json().get("jobs") or []

    return [
        Job(
            id=f"amazon:amazon:{posting.get('id_icims') or posting['id']}",
            title=posting.get("title", ""),
            company="amazon",
            location=posting.get("normalized_location") or posting.get("location") or "",
            url=BASE_URL + posting.get("job_path", ""),
            posted_at=_iso_date(posting.get("posted_date")),
            description=_text(posting.get("description_short") or ""),
            source=f"amazon/{config.get('name') or 'jobs'}",
        )
        for posting in postings
    ]


def _iso_date(posted_date: str | None) -> str | None:
    # The API returns US-style dates like "July 15, 2026".
    if not posted_date:
        return None
    try:
        return datetime.strptime(posted_date, "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _text(markup: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return " ".join(text.split())[:DESCRIPTION_LIMIT]

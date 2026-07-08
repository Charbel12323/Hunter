"""Workday adapter.

Talks to the CxS endpoint that serves every Workday-hosted career site:

    POST https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs

The API is unofficial but has been stable for years and powers the boards'
own frontends. Three probed facts shape this adapter:

- Page size is hard-capped at 20; limit=50 or 100 returns HTTP 400.
- With an empty searchText the board is newest-first (a tenant may pin a
  couple of old "featured" postings at the very top), so the first few
  pages always contain every posting added since the last poll. Boards
  hold up to several thousand postings - never paginate them fully.
- postedOn is a relative string ("Posted 3 Days Ago"), so posted_at is
  reconstructed approximately. "Posted 30+ Days Ago" maps to exactly 30
  days, which is already far past the max_age_days window.

A ``search`` config key is supported but switches Workday to relevance
ranking, where new postings are no longer guaranteed to be on the first
pages; leave it empty unless the fetch window must be narrowed.
"""

import re
from datetime import UTC, datetime, timedelta

import requests

from scraper.models import Job

API_URL = "https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
JOB_URL = "https://{company}.{instance}.myworkdayjobs.com/en-US/{site}{path}"
PAGE_SIZE = 20  # the server's hard cap
DEFAULT_PAGES = 3  # 60 newest postings; even the biggest boards post fewer per day
TIMEOUT_SECONDS = 30

_POSTED_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def fetch(config: dict) -> list[Job]:
    company = config["company"]
    instance = config["instance"]
    site = config["site"]
    pages = int(config.get("pages", DEFAULT_PAGES))
    api_url = API_URL.format(company=company, instance=instance, site=site)

    jobs: list[Job] = []
    seen_ids: set[str] = set()  # the board can shift between page requests
    total: int | None = None
    for page in range(pages):
        response = requests.post(
            api_url,
            json={
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": page * PAGE_SIZE,
                "searchText": config.get("search", ""),
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if total is None:
            # Only the first page's total is trustworthy: some tenants (e.g.
            # visa) report total=0 on every offset>0 page that still carries
            # postings, which would end pagination one page early.
            total = data.get("total") or 0
        postings = data.get("jobPostings") or []
        for posting in postings:
            job = _to_job(posting, company, instance, site)
            if job.id in seen_ids:
                continue
            seen_ids.add(job.id)
            jobs.append(job)
        if (page + 1) * PAGE_SIZE >= total or not postings:
            break
    return jobs


def _to_job(posting: dict, company: str, instance: str, site: str) -> Job:
    path = posting.get("externalPath") or ""
    # bulletFields[0] is the requisition id (e.g. "JR2020259"); the path is a
    # stable fallback for tenants that leave it empty.
    req_id = next(iter(posting.get("bulletFields") or []), None) or path
    return Job(
        id=f"workday:{company}:{req_id}",
        title=posting.get("title", ""),
        company=company,
        location=posting.get("locationsText") or "",
        url=JOB_URL.format(company=company, instance=instance, site=site, path=path)
        if path
        else "",
        posted_at=_posted_at(posting.get("postedOn")),
        description="",  # the list endpoint carries none; a per-job fetch is not worth it
        source=f"workday/{company}",
    )


def _posted_at(posted_on: str | None) -> str | None:
    if not posted_on:
        return None
    match = _POSTED_RE.search(posted_on)
    if not match:
        return None  # unparseable: undated, so the age filter keeps it (never-miss)
    if match.group(2) is not None:
        days = int(match.group(2))
    else:
        days = 0 if match.group(1).lower() == "today" else 1
    return (datetime.now(UTC) - timedelta(days=days)).date().isoformat()

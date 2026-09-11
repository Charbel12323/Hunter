"""Netflix careers adapter.

Netflix's career site (explore.jobs.netflix.net) runs on Eightfold, same as
Microsoft's - but unlike Microsoft's white-labeled ``apply.careers.
microsoft.com/api/pcsx/search`` (which needs a browser-fingerprinted TLS
handshake to get past its WAF), Netflix's own Eightfold path,
``/api/apply/v2/jobs``, answers plain unauthenticated GETs with no special
handling required. ``sort_by=timestamp`` puts the newest postings first; the
server hard-caps each page at 10 positions regardless of ``num``, so the
adapter pages through ``start`` offsets. The position payload carries no
description text (same as Microsoft's), so descriptions are left empty;
title/location/date filtering is unaffected.

Config:
    type: netflix
    name: canada               # optional; used in the source label, default "careers"
    location: Canada           # optional; server-side location filter
    query: software engineer   # optional; narrows the search at the source
    pages: 3                   # optional; 10 jobs per page, newest first
"""

from datetime import UTC, datetime

import requests

from scraper.models import Job

API_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
BASE_URL = "https://explore.jobs.netflix.net"
TIMEOUT_SECONDS = 30
DEFAULT_PAGES = 3
PAGE_SIZE = 10  # server-side hard cap; a larger num is silently clamped
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch(config: dict) -> list[Job]:
    source = f"netflix/{config.get('name') or 'careers'}"

    jobs: dict[str, Job] = {}  # keyed by id: entries can slide across pages
    for page in range(config.get("pages", DEFAULT_PAGES)):
        params = {
            "domain": "netflix.com",
            "start": page * PAGE_SIZE,
            "num": PAGE_SIZE,
            "sort_by": "timestamp",
        }
        if query := config.get("query"):
            params["query"] = query
        if location := config.get("location"):
            params["location"] = location
        response = requests.get(
            API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        positions = response.json().get("positions") or []

        for position in positions:
            job = _job(position, source)
            jobs.setdefault(job.id, job)
        if len(positions) < PAGE_SIZE:
            break
    return list(jobs.values())


def _job(position: dict, source: str) -> Job:
    position_id = position["id"]
    return Job(
        # display_job_id is the stable ATS requisition number (e.g. JR42454);
        # the Eightfold-internal id is the fallback.
        id=f"netflix:netflix:{position.get('display_job_id') or position_id}",
        title=position.get("name", ""),
        company="netflix",
        location="; ".join(position.get("locations") or []),
        url=position.get("canonicalPositionUrl") or f"{BASE_URL}/careers/job/{position_id}",
        posted_at=_iso_date(position.get("t_create")),
        description="",
        source=source,
    )


def _iso_date(timestamp: int | None) -> str | None:
    # t_create is epoch seconds.
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat(timespec="seconds")

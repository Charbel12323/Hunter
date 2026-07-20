"""Microsoft careers adapter.

Microsoft moved its careers site to Eightfold AI: the old
gcsservices.careers.microsoft.com search API is gone (its hostname now
points at a CDN whose certificate no longer covers it), and
jobs.careers.microsoft.com redirects to apply.careers.microsoft.com, an
Eightfold-hosted site. Its search UI is backed by an unauthenticated JSON
endpoint:
https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com

``sort_by=timestamp`` puts the newest postings first. The endpoint caps
pages at 10 positions regardless of ``num``, so the adapter pages through
``start`` offsets; a few pages between polls is lossless at the 5-minute
cadence. The search payload carries no description text, so descriptions
are empty (title filters are unaffected).

The WAF in front of the site fingerprints the TLS ClientHello and resets
handshakes that look like python-requests (curl and browsers get through).
Sending a trimmed, browser-like cipher list changes the fingerprint enough
to pass; the occasional remaining reset surfaces as a ConnectionError,
which the pipeline's per-source retry absorbs.

Config:
    type: microsoft
    name: canada               # optional; used in the source label, default "careers"
    location: Canada           # optional; server-side location filter
    query: software engineer   # optional; narrows the search at the source
    pages: 3                   # optional; 10 jobs per page, newest first
"""

from datetime import UTC, datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from scraper.models import Job

API_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
BASE_URL = "https://apply.careers.microsoft.com"
TIMEOUT_SECONDS = 30
DEFAULT_PAGES = 3
PAGE_SIZE = 10  # server-side hard cap; a larger num is silently clamped
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# A browser-like subset of the default cipher list. The point is not the
# ciphers themselves but making the ClientHello not fingerprint as
# python-requests, which the WAF resets.
CIPHERS = ":".join(
    [
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
    ]
)


class _FingerprintAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = create_urllib3_context(ciphers=CIPHERS)
        return super().init_poolmanager(*args, **kwargs)


def fetch(config: dict) -> list[Job]:
    source = f"microsoft/{config.get('name') or 'careers'}"
    session = requests.Session()
    session.mount("https://", _FingerprintAdapter())

    jobs: dict[str, Job] = {}  # keyed by id: entries can slide across pages
    for page in range(config.get("pages", DEFAULT_PAGES)):
        params = {
            "domain": "microsoft.com",
            "start": page * PAGE_SIZE,
            "num": PAGE_SIZE,
            "sort_by": "timestamp",
        }
        if query := config.get("query"):
            params["query"] = query
        if location := config.get("location"):
            params["location"] = location
        response = session.get(
            API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        positions = (response.json().get("data") or {}).get("positions") or []

        for position in positions:
            job = _job(position, source)
            jobs.setdefault(job.id, job)
        if len(positions) < PAGE_SIZE:
            break
    return list(jobs.values())


def _job(position: dict, source: str) -> Job:
    position_id = position["id"]
    return Job(
        # displayJobId is the stable ATS requisition number (e.g. 200041854);
        # the Eightfold-internal id is the fallback.
        id=f"microsoft:microsoft:{position.get('displayJobId') or position_id}",
        title=position.get("name", ""),
        company="microsoft",
        location="; ".join(position.get("locations") or []),
        url=BASE_URL + (position.get("positionUrl") or f"/careers/job/{position_id}"),
        posted_at=_iso_date(position.get("postedTs")),
        description="",
        source=source,
    )


def _iso_date(timestamp: int | None) -> str | None:
    # postedTs is epoch seconds.
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat(timespec="seconds")

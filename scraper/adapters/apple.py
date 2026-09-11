"""Apple careers adapter.

Apple runs a custom career site (jobs.apple.com) with no public JSON board
API - the ``/api/v1/search`` endpoint the frontend calls after hydration
requires a session that a plain HTTP client can't obtain (it 401s as "User
Unauthorized" even with cookies from a prior page load). The search results
page itself is server-rendered for SEO, though: a plain GET of
``/en-us/search?location=...&page=N`` returns full HTML with every job
already in the markup, so this adapter parses that instead of the API.

Job entries are extracted with a regex over the accordion list markup
(title anchor, team name, posted date, location) rather than a JSON blob -
Apple's SSR output is real DOM, not a data island. Descriptions aren't
extracted (they're keyed by a separate accordion-body id per job that isn't
worth the extra brittleness); title/location/date filtering is unaffected.

Config:
    type: apple
    name: canada                # optional; used in the source label, default "jobs"
    location: canada-CANC       # optional; Apple's internal geo code, default canada-CANC
    pages: 3                    # optional; 20 jobs per page, newest-first by default
"""

import html
import re
from datetime import datetime

import requests

from scraper.models import Job

SEARCH_URL = "https://jobs.apple.com/en-us/search"
BASE_URL = "https://jobs.apple.com"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30
DEFAULT_PAGES = 3
PAGE_SIZE = 20
DEFAULT_LOCATION = "canada-CANC"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

JOB_RE = re.compile(
    r'href="/en-us/details/(?P<id>[^/"]+)/(?P<slug>[^"?]+)\?team=[^"]*"[^>]*>'
    r"(?P<title>[^<]+)</a></h3>"
    r'<span[^>]*class="team-name[^"]*">(?P<team>[^<]*)</span>'
    r'<span class="job-posted-date"[^>]*>(?P<posted>[^<]*)</span>'
    r'.*?id="search-store-name(?:-container)?-\d+">(?P<location>[^<]*)</span>',
    re.S,
)
TOTAL_RE = re.compile(r"(\d+)\s+Result\(s\)")


def fetch(config: dict) -> list[Job]:
    source = f"apple/{config.get('name') or 'jobs'}"
    location = config.get("location", DEFAULT_LOCATION)

    jobs: dict[str, Job] = {}  # keyed by id: defensive against duplicate matches
    total = None
    for page in range(1, config.get("pages", DEFAULT_PAGES) + 1):
        response = requests.get(
            SEARCH_URL,
            params={"location": location, "sort": "newest", "page": page},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.text

        if total is None and (match := TOTAL_RE.search(body)):
            total = int(match.group(1))

        matches = list(JOB_RE.finditer(body))
        for match in matches:
            job = _job(match, source)
            jobs.setdefault(job.id, job)
        if len(matches) < PAGE_SIZE or (total is not None and len(jobs) >= total):
            break
    return list(jobs.values())


def _job(match: re.Match, source: str) -> Job:
    job_id = match.group("id")
    return Job(
        id=f"apple:apple:{job_id}",
        title=html.unescape(match.group("title")).strip(),
        company="apple",
        location=html.unescape(match.group("location")).strip(),
        url=f"{BASE_URL}/en-us/details/{job_id}/{match.group('slug')}",
        posted_at=_iso_date(match.group("posted").strip()),
        description="",
        source=source,
    )


def _iso_date(posted: str) -> str | None:
    # Apple renders dates like "Sep 11, 2026".
    try:
        return datetime.strptime(posted, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None

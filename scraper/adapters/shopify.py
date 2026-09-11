"""Shopify careers adapter.

Shopify's career site (shopify.com/careers) is a React Router v7 app that
looks unscrapable at first: the rendered page has no visible job listings
in its initial HTML for the high-volume disciplines (Engineering, Design,
Product, Commercial - they're shown as summary cards, with the real list
populated client-side), and the underlying data isn't JSON either. It's
`.data` route-loader payload (``GET /careers.data``, what React Router's
client fetches on navigation) which *is* plain unauthenticated JSON, but
every object in it is flattened into one big array and referenced by
index - e.g. ``{"_9": 10, "_11": 12}`` at array position 8 means "the
object at 8 has a key equal to array[9]'s value, pointing at array[10]".
This is NOT the open-source ``turbo-stream`` library's wire format (it
looks similar but doesn't share a byte of protocol - decoding it with the
real library throws), it's bespoke to Shopify's frontend. ``_resolve``
below is a from-scratch decoder for it: recursively resolve every "_N": M
object into {resolve(N): resolve(M)}, plain lists position-wise, and
negative integers as an unknown-sentinel -> None (they showed up constantly
in unrelated UI/component-tree fields we don't care about; none of the job
fields this adapter reads ever legitimately resolve to a negative index).

The resolved payload's ``jobPostingsWithJobs`` list is the whole company's
open-role board (Ashby-backed under the hood - each job's apply link is
``.../careers?ashby_jid=<uuid>``) with title/location/date/link already
flat; there's no per-page/location query filtering server-side, so this
fetches the whole board every poll and leaves location filtering to
sources.yaml same as everywhere else. No description text is included in
this listing payload (only on individual job pages, which would mean one
request per job); title/location/date filtering is unaffected.

Config:
    type: shopify
    name: canada   # optional; used in the source label, default "careers"
"""

import json
import re

import requests

from scraper.models import Job

DATA_URL = "https://www.shopify.com/careers.data"
BASE_URL = "https://www.shopify.com"
TIMEOUT_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
REF_KEY_RE = re.compile(r"^_(\d+)$")


def fetch(config: dict) -> list[Job]:
    source = f"shopify/{config.get('name') or 'careers'}"
    response = requests.get(
        DATA_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/x-script, application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    # The response has no charset in its Content-Type (text/x-script), so
    # requests falls back to Latin-1 and mangles non-ASCII text; the body is
    # actually UTF-8.
    arr = json.loads(response.content.decode("utf-8"))

    routes = _resolve(arr, 0)
    route_key = next((k for k in routes if isinstance(k, str) and "careers" in k), None)
    if route_key is None:
        raise ValueError("shopify careers.data layout changed: no 'careers' route found")
    careers_data = routes[route_key]["data"]
    postings = careers_data.get("jobPostingsWithJobs") or []

    return [
        _job(entry["jobPosting"], source)
        for entry in postings
        if entry.get("jobPosting", {}).get("isListed")
    ]


def _job(posting: dict, source: str) -> Job:
    return Job(
        id=f"shopify:shopify:{posting['id']}",
        title=posting.get("title", ""),
        company="shopify",
        location=posting.get("locationName") or "",
        url=posting.get("applyLink") or posting.get("externalLink") or BASE_URL,
        posted_at=posting.get("publishedDate"),
        description="",
        source=source,
    )


def _resolve(arr: list, index: int, memo: dict | None = None, in_progress: set | None = None):
    if memo is None:
        memo = {}
    if in_progress is None:
        in_progress = set()
    if index < 0:
        return None
    if index in memo:
        return memo[index]
    if index in in_progress:
        return None  # cyclic reference; not needed for the fields this adapter reads
    in_progress.add(index)

    value = arr[index]
    if isinstance(value, dict) and value and all(REF_KEY_RE.match(k) for k in value):
        result: dict = {}
        memo[index] = result
        for raw_key, raw_val in value.items():
            key = _resolve(arr, int(REF_KEY_RE.match(raw_key).group(1)), memo, in_progress)
            val = _resolve(arr, raw_val, memo, in_progress) if isinstance(raw_val, int) else raw_val
            if isinstance(key, str):
                result[key] = val
    elif isinstance(value, list):
        result = []
        memo[index] = result
        for item in value:
            result.append(_resolve(arr, item, memo, in_progress) if isinstance(item, int) else item)
    else:
        memo[index] = value
        result = value

    in_progress.discard(index)
    return result

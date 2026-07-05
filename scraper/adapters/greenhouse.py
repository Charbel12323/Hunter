"""Greenhouse adapter.

Public, unauthenticated board API:
https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true

``content=true`` is required to get descriptions - without it the API
returns titles only.
"""

import html
import re

import requests

from scraper.models import Job

API_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 30


def fetch(config: dict) -> list[Job]:
    company = config["company"]
    response = requests.get(
        API_URL.format(company=company),
        params={"content": "true"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    postings = response.json().get("jobs", [])

    return [
        Job(
            id=f"greenhouse:{company}:{posting['id']}",
            title=posting.get("title", ""),
            company=company,
            location=(posting.get("location") or {}).get("name", ""),
            url=posting.get("absolute_url", ""),
            posted_at=posting.get("first_published") or posting.get("updated_at"),
            description=_description(posting.get("content") or ""),
            source=f"greenhouse/{company}",
        )
        for posting in postings
    ]


def _description(content: str) -> str:
    # `content` is HTML-escaped HTML: unescape to get markup, strip the tags,
    # then unescape once more for entities that were inside the markup.
    text = re.sub(r"<[^>]+>", " ", html.unescape(content))
    return " ".join(html.unescape(text).split())[:DESCRIPTION_LIMIT]

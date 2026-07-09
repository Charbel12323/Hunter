"""Telegram sender.

Posts one message per job via the Bot API sendMessage endpoint (HTML parse
mode). Credentials come only from environment variables, injected by GitHub
Actions Secrets - never from config files.
"""

import html
import logging
import os
import time
from datetime import datetime
from urllib.parse import quote

import requests

from scraper.models import Job

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
SEND_PAUSE_SECONDS = 0.5  # stay well under Telegram's rate limits
TIMEOUT_SECONDS = 30
PAGE_BUDGET = 3800  # headroom under Telegram's hard 4096-char message cap
MAX_TITLE_CHARS = 200  # a title longer than this is noise; keep lines bounded

# Pre-built people-search URLs, not resolved names: they open in the reader's
# own logged-in LinkedIn session (surfacing mutual connections), and need no
# scraping or people-data API.
LINKEDIN_PEOPLE_URL = "https://www.linkedin.com/search/results/people/?keywords={query}"
PEOPLE_SEARCHES = (("Recruiters", "recruiter"), ("Managers", "manager"))


def send(job: Job) -> None:
    _post(format_message(job))
    log.info("Notified: %s", job.id)
    time.sleep(SEND_PAUSE_SECONDS)


def send_digest(jobs: list[Job]) -> None:
    pages = format_digest(jobs)
    for page in pages:
        _post(page)
        time.sleep(SEND_PAUSE_SECONDS)
    log.info("Notified: digest of %d jobs in %d messages", len(jobs), len(pages))


def send_text(text: str) -> None:
    """Send a plain (non-job) message, e.g. a health warning."""
    _post(html.escape(text))
    log.info("Notified: %s", text)


def _post(text: str) -> None:
    # Strip whitespace: a token pasted into GitHub Secrets with a trailing
    # newline becomes %0A in the URL and Telegram answers 404.
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
    response = requests.post(
        API_URL.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def format_message(job: Job) -> str:
    # Telegram HTML mode breaks on unescaped <, >, & - escape everything
    # that originates from the source.
    e = html.escape
    lines = [f"<b>{e(job.title)}</b>", f"{e(job.company)} - {e(job.location)}"]
    if job.posted_at:
        lines.append(f"Posted: {e(_date_only(job.posted_at))}")
    if job.description:
        lines.append("")
        lines.append(e(job.description))
    lines.append("")
    lines.append(f'<a href="{e(job.url, quote=True)}">Apply</a> ({e(job.source)})')
    lines.append(_people_links(job.company))
    return "\n".join(lines)


def format_digest(jobs: list[Job]) -> list[str]:
    # Telegram hard-caps a message at 4096 chars, so a big run must be split
    # across as many messages as it takes: every job gets sent, none are
    # silently dropped (a dropped job is marked seen and lost forever).
    # Jobs are grouped by company so each company's people links appear once.
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.company, []).append(job)

    pages_lines: list[list[str]] = [[]]
    for company, group in groups.items():
        for block in _company_blocks(company, group):
            _place_block(pages_lines, block)

    pages = []
    for i, lines in enumerate(pages_lines):
        head = f"{len(jobs)} new matching jobs this run"
        if len(pages_lines) > 1:
            head += f" ({i + 1}/{len(pages_lines)})"
        pages.append("\n".join([f"<b>{head}</b>", "", *lines]))
    return pages


def _company_blocks(company: str, jobs: list[Job]) -> list[list[str]]:
    """One header/jobs/people-links block per company, split into several
    blocks only when the company alone exceeds a page. Every split carries
    the header and people links so no page shows jobs orphaned from them."""
    header = f"<b>{html.escape(company)}</b>"
    people = _people_links(company)
    frame = len(header) + len(people) + 2  # the two newlines around the jobs
    blocks: list[list[str]] = []
    lines: list[str] = []
    used = frame
    for job in jobs:
        line = _digest_line(job)
        if lines and used + len(line) + 1 > PAGE_BUDGET:
            blocks.append([header, *lines, people])
            lines = []
            used = frame
        lines.append(line)
        used += len(line) + 1
    blocks.append([header, *lines, people])
    return blocks


def _place_block(pages_lines: list[list[str]], block: list[str]) -> None:
    page = pages_lines[-1]
    size = sum(len(line) + 1 for line in block)
    if page and sum(len(line) + 1 for line in page) + size + 1 > PAGE_BUDGET:
        pages_lines.append([])
        page = pages_lines[-1]
    if page:
        page.append("")  # blank line between company blocks
    page.extend(block)


def _people_links(company: str) -> str:
    e = html.escape
    links = []
    for label, role in PEOPLE_SEARCHES:
        url = LINKEDIN_PEOPLE_URL.format(query=quote(f"{company} {role}"))
        links.append(f'<a href="{e(url, quote=True)}">{label}</a>')
    return "People: " + " | ".join(links)


def _digest_line(job: Job) -> str:
    e = html.escape
    title = job.title
    if len(title) > MAX_TITLE_CHARS:
        # Truncate before escaping so we can't cut an entity like &amp; in half.
        title = title[: MAX_TITLE_CHARS - 3] + "..."
    return f'- <a href="{e(job.url, quote=True)}">{e(title)}</a>'


def _date_only(posted_at: str) -> str:
    try:
        return datetime.fromisoformat(posted_at).date().isoformat()
    except ValueError:
        return posted_at

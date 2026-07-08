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

import requests

from scraper.models import Job

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
SEND_PAUSE_SECONDS = 0.5  # stay well under Telegram's rate limits
TIMEOUT_SECONDS = 30
PAGE_BUDGET = 3800  # headroom under Telegram's hard 4096-char message cap
MAX_TITLE_CHARS = 200  # a title longer than this is noise; keep lines bounded


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
    return "\n".join(lines)


def format_digest(jobs: list[Job]) -> list[str]:
    # Telegram hard-caps a message at 4096 chars, so a big run must be split
    # across as many messages as it takes: every job gets sent, none are
    # silently dropped (a dropped job is marked seen and lost forever).
    chunks: list[list[str]] = [[]]
    used = 0
    for job in jobs:
        line = _digest_line(job)
        if chunks[-1] and used + len(line) + 1 > PAGE_BUDGET:
            chunks.append([])
            used = 0
        chunks[-1].append(line)
        used += len(line) + 1

    pages = []
    for i, chunk in enumerate(chunks):
        head = f"{len(jobs)} new matching jobs this run"
        if len(chunks) > 1:
            head += f" ({i + 1}/{len(chunks)})"
        pages.append("\n".join([f"<b>{head}</b>", "", *chunk]))
    return pages


def _digest_line(job: Job) -> str:
    e = html.escape
    title = job.title
    if len(title) > MAX_TITLE_CHARS:
        # Truncate before escaping so we can't cut an entity like &amp; in half.
        title = title[: MAX_TITLE_CHARS - 3] + "..."
    return f'- <a href="{e(job.url, quote=True)}">{e(title)}</a> - {e(job.company)}'


def _date_only(posted_at: str) -> str:
    try:
        return datetime.fromisoformat(posted_at).date().isoformat()
    except ValueError:
        return posted_at

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


def send(job: Job) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    response = requests.post(
        API_URL.format(token=token),
        json={
            "chat_id": chat_id,
            "text": format_message(job),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    log.info("Notified: %s", job.id)
    time.sleep(SEND_PAUSE_SECONDS)


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


def _date_only(posted_at: str) -> str:
    try:
        return datetime.fromisoformat(posted_at).date().isoformat()
    except ValueError:
        return posted_at

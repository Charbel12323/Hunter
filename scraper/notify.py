"""Telegram sender.

Posts one message per job - or per group of identical-looking postings -
via the Bot API sendMessage endpoint (HTML parse mode). Credentials come
only from environment variables, injected by GitHub Actions Secrets - never
from config files.
"""

import html
import logging
import os
import re
import time
from datetime import UTC, datetime

import requests

from scraper.models import Job
from scraper.regions import region

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
SEND_PAUSE_SECONDS = 0.5  # stay well under Telegram's rate limits
TIMEOUT_SECONDS = 30

# Seniority buckets, in the order their digest messages are sent.
CATEGORIES = (
    ("internship", "🌱", "INTERNSHIPS"),
    ("new_grad", "🎓", "NEW GRAD & JUNIOR"),
    ("full_time", "💼", "FULL-TIME"),
)
_EMOJI = {key: emoji for key, emoji, _ in CATEGORIES}

# Within each seniority digest, jobs are further split by region, in this order.
REGIONS = (
    ("canada", "🇨🇦", "Canada"),
    ("us", "🇺🇸", "United States"),
    ("other", "🌐", "Other"),
)

_INTERN_RE = re.compile(r"\bintern(ship)?\b|\bco[-\s]?op\b|\bstudent\b", re.IGNORECASE)
# Internship feeds list student roles whose titles say nothing about it
# ("Student Web Developer"), so the feed itself is the stronger signal.
_INTERN_FEED_RE = re.compile(r"intern", re.IGNORECASE)
# "Engineer I" / "Engineer 1" style level suffixes count as junior; II+ do not.
_NEW_GRAD_RE = re.compile(
    r"\bnew\s+grad(uate)?\b|\bgraduate\b|\bentry[-\s]level\b|\bearly\s+career\b"
    r"|\bjunior\b|\bjr\.?\b|\bassociate\b|\bcampus\b|\buniversity\s+grad"
    r"|\b(i|1)\s*$",
    re.IGNORECASE,
)


def categorize(job: Job) -> str:
    if _INTERN_RE.search(job.title):
        return "internship"
    repo = _github_repo(job.source)
    if repo and _INTERN_FEED_RE.search(repo):
        return "internship"
    if _NEW_GRAD_RE.search(job.title):
        return "new_grad"
    return "full_time"


# Company names reach notify in two shapes: bare ATS slugs from sources.yaml
# ("td", "northrop-grumman") and already-cased names from the aggregator feeds
# ("Domino Data Lab"). Slugs get title-cased; anything already carrying an
# uppercase letter is trusted as-is. Only names the generic rule gets wrong -
# acronyms and camel-cased brands - need an entry below.
_COMPANY_NAMES = {
    "cibc": "CIBC",
    "gitlab": "GitLab",
    "hp": "HP",
    "hpe": "HPE",
    "ibm": "IBM",
    "janestreet": "Jane Street",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "rbc": "RBC",
    "td": "TD",
}


def display_company(company: str) -> str:
    name = company.strip()
    override = _COMPANY_NAMES.get(name.lower())
    if override:
        return override
    if any(char.isupper() for char in name):
        return name
    return re.sub(r"[-_]+", " ", name).title()


_GITHUB_PREFIX = "github/"
GITHUB_REPO_URL = "https://github.com/{repo}"


def _github_repo(source: str) -> str | None:
    """The repo slug when a job came from a GitHub aggregator feed, else None."""
    if source.startswith(_GITHUB_PREFIX):
        return source[len(_GITHUB_PREFIX) :]
    return None


def group_duplicates(jobs: list[Job]) -> list[list[Job]]:
    """Collapse postings that look identical to a reader - same company,
    title and location under different ATS ids (one role opened as several
    reqs). Order is preserved. Display-only: every job keeps its own id and
    is still recorded as seen individually."""
    groups: dict[tuple[str, str, str], list[Job]] = {}
    for job in jobs:
        key = (
            display_company(job.company).casefold(),
            " ".join(job.title.split()).casefold(),
            " ".join(job.location.split()).casefold(),
        )
        groups.setdefault(key, []).append(job)
    return list(groups.values())


def send(job: Job, copies: int = 1) -> None:
    _post(format_message(job, copies))
    log.info("Notified: %s", job.id)
    time.sleep(SEND_PAUSE_SECONDS)


def send_digest(jobs: list[Job]) -> None:
    messages = format_digest(jobs)
    for message in messages:
        _post(message)
        time.sleep(SEND_PAUSE_SECONDS)
    log.info("Notified: digest of %d jobs in %d message(s)", len(jobs), len(messages))


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


def format_message(job: Job, copies: int = 1) -> str:
    # Telegram HTML mode breaks on unescaped <, >, & - escape everything
    # that originates from the source.
    e = html.escape
    lines = [f"<b>{_EMOJI[categorize(job)]} {e(job.title)}</b>{_copies(copies)}"]

    company_line = e(display_company(job.company))
    if job.location:
        company_line += f" — {e(job.location)}"
    lines.append(company_line)

    if job.posted_at:
        posted = _date_only(job.posted_at)
        age = _age(job.posted_at)
        lines.append(f"Posted: {e(posted)} ({age})" if age else f"Posted: {e(posted)}")

    # No dedicated pay/keyword fields exist, so mine them from the
    # description; both are omitted when nothing recognisable is found.
    pay = _extract_pay(job.description)
    if pay:
        lines.append(f"Pay: {e(pay)}")
    keywords = _extract_keywords(job.description)
    if keywords:
        lines.append(f"Keywords: {e(', '.join(keywords))}")

    lines.append("")
    apply = f'<a href="{e(job.url, quote=True)}">Apply</a>'
    repo = _github_repo(job.source)
    if repo:
        url = e(GITHUB_REPO_URL.format(repo=repo), quote=True)
        apply += f' · via <a href="{url}">{e(repo)}</a>'
    lines.append(apply)
    return "\n".join(lines)


def format_digest(jobs: list[Job]) -> list[str]:
    # One message (or more, if long) per seniority bucket, so internships,
    # new-grad roles, and full-time roles never share a message. Counts in
    # the headers are entries, i.e. duplicates collapsed.
    messages: list[str] = []
    entries = group_duplicates(jobs)
    for key, emoji, name in CATEGORIES:
        group = [dupes for dupes in entries if categorize(dupes[0]) == key]
        if group:
            messages.extend(_format_group(f"{emoji} {name}", group))
    return messages


CAP = 3800  # stay safely under Telegram's 4096-char message cap


def _format_entry(dupes: list[Job]) -> str:
    """One digest entry: company first and bold, then location and age, then
    feed. Duplicates share one entry, linked to the first posting.

    Returned as a single multi-line string so the message splitter treats the
    entry as one indivisible unit and never orphans a job's location line.
    """
    e = html.escape
    job = dupes[0]
    lines = [
        f'- <b>{e(display_company(job.company))}</b> — '
        f'<a href="{e(job.url, quote=True)}">{e(job.title)}</a>{_copies(len(dupes))}'
    ]
    details = [e(job.location)] if job.location else []
    if job.posted_at and (age := _age(job.posted_at)):
        details.append(age)
    if details:
        lines.append(f"  {' · '.join(details)}")
    # Aggregator feeds pull from hundreds of companies, so naming the repo is
    # the only way to tell where a listing actually came from. Per-company ATS
    # sources are already identified by the company name above.
    repo = _github_repo(job.source)
    if repo:
        url = e(GITHUB_REPO_URL.format(repo=repo), quote=True)
        lines.append(f'  via <a href="{url}">{e(repo)}</a>')
    return "\n".join(lines)


def _format_group(label: str, jobs: list[list[Job]]) -> list[str]:
    # Within the seniority group, split further by region (Canada / US /
    # Other), each under its own flagged subheader. Long groups spill across
    # as many messages as needed - each stays under the Telegram cap and the
    # main and region headers repeat on continuation so no job is orphaned.
    header = f"<b>{label} ({len(jobs)})</b>"

    sections: list[tuple[str, list[str]]] = []
    for key, flag, name in REGIONS:
        group = [dupes for dupes in jobs if region(dupes[0].location) == key]
        if not group:
            continue
        subheader = f"<b>{flag} {name} ({len(group)})</b>"
        sections.append((subheader, [_format_entry(dupes) for dupes in group]))

    messages: list[str] = []
    lines = [header, ""]

    def used() -> int:
        return sum(len(line) + 1 for line in lines)

    def flush() -> None:
        nonlocal lines
        messages.append("\n".join(lines))
        lines = [f"{header} (continued)", ""]

    for subheader, entries in sections:
        blank = any(line.startswith("- ") for line in lines)
        # Keep a subheader with its first job: start a fresh message if the
        # pair won't fit on the current one.
        if used() + blank + len(subheader) + 1 + len(entries[0]) + 1 > CAP:
            flush()
            blank = False
        if blank:
            lines.append("")
        lines.append(subheader)
        for entry in entries:
            has_job = any(line.startswith("- ") for line in lines)
            if used() + len(entry) + 1 > CAP and has_job:
                flush()
                lines.append(subheader)
            lines.append(entry)
    messages.append("\n".join(lines))
    return messages


def _copies(count: int) -> str:
    return f" ×{count}" if count > 1 else ""


def _age(posted_at: str, now: datetime | None = None) -> str | None:
    """How long ago a job was posted, in whole days - what actually matters
    when deciding whether to apply now. None if the date can't be parsed."""
    try:
        posted = datetime.fromisoformat(posted_at)
    except ValueError:
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    days = (now.date() - posted.astimezone(UTC).date()).days
    if days <= 0:
        return "🔥 today"
    return f"{days}d ago"


def _date_only(posted_at: str) -> str:
    try:
        return datetime.fromisoformat(posted_at).date().isoformat()
    except ValueError:
        return posted_at


# A salary-like dollar amount: comma-grouped ($120,000), K/M-suffixed
# ($120K), or a bare run of 4+ digits ($120000). The leading amount must be
# $-anchored to avoid matching stray numbers; the second half of a range may
# drop the $ ("$120,000 - 150,000").
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s?[KkMm]\b|\d{4,})"
_PAY_RE = re.compile(
    rf"\$\s?{_AMOUNT}(?:\s?(?:-|–|—|to)\s?\$?\s?{_AMOUNT})?"
)


def _extract_pay(description: str) -> str | None:
    match = _PAY_RE.search(description)
    if not match:
        return None
    return " ".join(match.group(0).split())


# Recognisable skills/technologies, mapped to a canonical display form. Values
# are case-insensitive regexes; multiple spellings collapse onto one label.
_SKILLS = {
    "Python": r"\bpython\b",
    "Java": r"\bjava\b",
    "JavaScript": r"\bjavascript\b",
    "TypeScript": r"\btypescript\b",
    "C++": r"c\+\+",
    "C#": r"c#",
    "Golang": r"\bgolang\b",
    "Rust": r"\brust\b",
    "Ruby": r"\bruby\b",
    "Kotlin": r"\bkotlin\b",
    "Swift": r"\bswift\b",
    "Scala": r"\bscala\b",
    "PHP": r"\bphp\b",
    "MATLAB": r"\bmatlab\b",
    "SQL": r"\bsql\b",
    "NoSQL": r"\bnosql\b",
    "React": r"\breact(?:\.js)?\b",
    "Angular": r"\bangular\b",
    "Vue": r"\bvue(?:\.js)?\b",
    "Node.js": r"\bnode\.js\b",
    "Django": r"\bdjango\b",
    "Flask": r"\bflask\b",
    "Spring": r"\bspring\b",
    ".NET": r"\.net\b",
    "TensorFlow": r"\btensorflow\b",
    "PyTorch": r"\bpytorch\b",
    "Kafka": r"\bkafka\b",
    "Spark": r"\bspark\b",
    "AWS": r"\baws\b",
    "Azure": r"\bazure\b",
    "GCP": r"\bgcp\b|\bgoogle cloud\b",
    "Docker": r"\bdocker\b",
    "Kubernetes": r"\bkubernetes\b|\bk8s\b",
    "Terraform": r"\bterraform\b",
    "Linux": r"\blinux\b",
    "PostgreSQL": r"\bpostgres(?:ql)?\b",
    "MongoDB": r"\bmongodb\b",
    "Redis": r"\bredis\b",
    "GraphQL": r"\bgraphql\b",
    "REST": r"\brest(?:ful)?\b",
    "gRPC": r"\bgrpc\b",
    "OOP": r"\boop\b|\bobject[-\s]oriented\b",
    "Machine Learning": r"\bmachine learning\b|\bml\b",
    "Deep Learning": r"\bdeep learning\b",
    "NLP": r"\bnlp\b|\bnatural language processing\b",
    "Computer Vision": r"\bcomputer vision\b",
    "Distributed Systems": r"\bdistributed systems?\b",
    "Microservices": r"\bmicroservices?\b",
    "CI/CD": r"\bci/cd\b",
    "Agile": r"\bagile\b",
    "Scrum": r"\bscrum\b",
    "Algorithms": r"\balgorithms?\b",
    "Data Structures": r"\bdata structures?\b",
}
_SKILL_LIMIT = 10


def _extract_keywords(description: str) -> list[str]:
    # Order by first appearance so the most prominent skills lead.
    found: list[tuple[int, str]] = []
    for canonical, pattern in _SKILLS.items():
        match = re.search(pattern, description, re.IGNORECASE)
        if match:
            found.append((match.start(), canonical))
    found.sort()
    return [name for _, name in found[:_SKILL_LIMIT]]

from scraper.models import Job
from scraper.notify import format_digest, format_message


def make_job(n: int = 1, title: str | None = None) -> Job:
    return Job(
        id=f"x:y:{n}",
        title=title or f"Engineer {n} <Platform & Tools>",
        company="Acme",
        location="Remote",
        url=f"https://example.com/jobs/{n}?a=1&b=2",
        posted_at="2026-06-17T20:31:02.329+00:00",
        description="Build <great> things & more.",
        source="x/y",
    )


def test_message_escapes_html_and_trims_date():
    text = format_message(make_job())
    assert "&lt;Platform &amp; Tools&gt;" in text
    assert "Build &lt;great&gt; things &amp; more." in text
    assert "Posted: 2026-06-17" in text
    assert "20:31" not in text  # date only, no timestamp
    assert 'href="https://example.com/jobs/1?a=1&amp;b=2"' in text


def test_digest_lists_jobs_and_counts():
    jobs = [make_job(n) for n in range(1, 4)]
    text = format_digest(jobs)
    assert text.startswith("<b>3 new matching jobs this run</b>")
    assert text.count("<a href=") == 3
    assert "more" not in text


def test_digest_truncates_to_stay_under_telegram_cap():
    jobs = [make_job(n, title="X" * 200) for n in range(100)]
    text = format_digest(jobs)
    assert len(text) <= 4000
    assert "more" in text.splitlines()[-1]

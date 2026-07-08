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
    pages = format_digest(jobs)
    assert len(pages) == 1
    assert pages[0].startswith("<b>3 new matching jobs this run</b>")
    assert pages[0].count("<a href=") == 3


def test_digest_splits_into_pages_without_dropping_jobs():
    jobs = [make_job(n, title="X" * 150) for n in range(100)]
    pages = format_digest(jobs)
    assert len(pages) > 1
    assert all(len(page) <= 4096 for page in pages)
    # Every single job must appear somewhere; none silently dropped.
    assert sum(page.count("<a href=") for page in pages) == 100
    assert pages[0].splitlines()[0].endswith(f"(1/{len(pages)})</b>")
    assert pages[-1].splitlines()[0].endswith(f"({len(pages)}/{len(pages)})</b>")


def test_digest_truncates_absurdly_long_titles():
    pages = format_digest([make_job(1, title="Y" * 1000)])
    assert len(pages) == 1
    assert "Y" * 1000 not in pages[0]
    assert "..." in pages[0]

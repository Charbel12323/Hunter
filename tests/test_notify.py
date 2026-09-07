from scraper.models import Job
from scraper.notify import format_digest, format_message


def make_job(n: int = 1, title: str | None = None, company: str = "Acme") -> Job:
    return Job(
        id=f"x:y:{n}",
        title=title or f"Engineer {n} <Platform & Tools>",
        company=company,
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


def test_message_links_people_searches():
    text = format_message(make_job())
    assert "People:" in text
    assert "linkedin.com/search/results/people/?keywords=Acme%20recruiter" in text
    assert "linkedin.com/search/results/people/?keywords=Acme%20manager" in text
    assert "geoUrn" not in text  # no people_location configured -> worldwide


def test_message_people_searches_pinned_to_region():
    text = format_message(make_job(), people_location="canada")
    # %5B%22...%22%5D is the URL-encoded ["101174742"] LinkedIn expects.
    assert text.count("&amp;geoUrn=%5B%22101174742%22%5D") == 2


def test_message_unknown_people_location_falls_back_to_worldwide(caplog):
    text = format_message(make_job(), people_location="narnia")
    assert "geoUrn" not in text
    assert "narnia" in caplog.text


def test_digest_people_searches_pinned_to_region():
    page = format_digest([make_job(1), make_job(2, company="Globex")], "canada")[0]
    assert page.count("&amp;geoUrn=%5B%22101174742%22%5D") == 4  # 2 companies x 2 roles


def test_digest_lists_jobs_and_counts():
    jobs = [make_job(n) for n in range(1, 4)]
    pages = format_digest(jobs)
    assert len(pages) == 1
    assert pages[0].startswith("<b>3 new matching jobs this run</b>")
    assert pages[0].count("- <a href=") == 3
    # Same company: one header and one people line for all three jobs.
    assert pages[0].count("<b>Acme</b>") == 1
    assert pages[0].count("People:") == 1


def test_digest_groups_by_company():
    jobs = [make_job(1), make_job(2, company="Globex & Co"), make_job(3)]
    page = format_digest(jobs)[0]
    assert page.count("People:") == 2
    # Company names are escaped in headers and URL-encoded in search links.
    assert "<b>Globex &amp; Co</b>" in page
    assert "keywords=Globex%20%26%20Co%20recruiter" in page
    # Acme's two jobs sit together under one header despite arriving split.
    acme_at = page.index("<b>Acme</b>")
    globex_at = page.index("<b>Globex &amp; Co</b>")
    acme_block = page[acme_at:globex_at] if acme_at < globex_at else page[acme_at:]
    assert acme_block.count("- <a href=") == 2


def test_digest_splits_into_pages_without_dropping_jobs():
    jobs = [make_job(n, title="X" * 150) for n in range(100)]
    pages = format_digest(jobs)
    assert len(pages) > 1
    assert all(len(page) <= 4096 for page in pages)
    # Every single job must appear somewhere; none silently dropped.
    assert sum(page.count("- <a href=") for page in pages) == 100
    # A split company keeps its header and people links on every page.
    assert all("<b>Acme</b>" in page and "People:" in page for page in pages)
    assert pages[0].splitlines()[0].endswith(f"(1/{len(pages)})</b>")
    assert pages[-1].splitlines()[0].endswith(f"({len(pages)}/{len(pages)})</b>")


def test_digest_truncates_absurdly_long_titles():
    pages = format_digest([make_job(1, title="Y" * 1000)])
    assert len(pages) == 1
    assert "Y" * 1000 not in pages[0]
    assert "..." in pages[0]

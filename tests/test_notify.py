from datetime import UTC, datetime

from scraper import main
from scraper.models import Job
from scraper.notify import (
    _age,
    categorize,
    display_company,
    format_digest,
    format_message,
    group_duplicates,
)
from scraper.regions import region as _region
from scraper.store import SeenStore


def make_job(
    n: int = 1,
    title: str | None = None,
    location: str = "Remote",
    company: str = "Acme",
    source: str = "x/y",
) -> Job:
    return Job(
        id=f"x:y:{n}",
        title=title or f"Engineer {n} <Platform & Tools>",
        company=company,
        location=location,
        url=f"https://example.com/jobs/{n}?a=1&b=2",
        posted_at="2026-06-17T20:31:02.329+00:00",
        description="Build <great> things & more.",
        source=source,
    )


def test_message_escapes_html_and_trims_date():
    text = format_message(make_job())
    assert "&lt;Platform &amp; Tools&gt;" in text
    assert "Posted: 2026-06-17" in text
    assert "20:31" not in text  # date only, no timestamp
    assert 'href="https://example.com/jobs/1?a=1&amp;b=2"' in text


def test_message_omits_description():
    text = format_message(make_job())
    assert "Build" not in text  # the raw description is not reported


def test_message_frames_title_for_skimming():
    text = format_message(make_job(title="Software Intern"))
    assert text.startswith("<b>🌱 Software Intern</b>")


def test_message_reports_company_location_and_extracted_fields():
    job = Job(
        id="x:y:1",
        title="Backend Engineer",
        company="Acme",
        location="Toronto, Canada",
        url="https://example.com/jobs/1",
        posted_at="2026-06-17T20:31:02+00:00",
        description="We want strong Python and Java skills, OOP, and REST. "
        "Compensation is $120,000 - $150,000 per year.",
        source="x/y",
    )
    text = format_message(job)
    assert "Acme — Toronto, Canada" in text
    assert "Pay: $120,000 - $150,000" in text
    assert "Keywords: Python, Java, OOP, REST" in text


def test_categorize_buckets_by_title():
    assert categorize(make_job(title="Software Engineering Intern")) == "internship"
    assert categorize(make_job(title="Co-op Developer")) == "internship"
    assert categorize(make_job(title="New Grad Software Engineer")) == "new_grad"
    assert categorize(make_job(title="Junior Backend Engineer")) == "new_grad"
    assert categorize(make_job(title="Software Engineer 1")) == "new_grad"
    assert categorize(make_job(title="Software Engineer I")) == "new_grad"
    assert categorize(make_job(title="Software Engineer II")) == "full_time"
    assert categorize(make_job(title="Staff Software Engineer")) == "full_time"
    assert categorize(make_job(title="AI Prompt Engineer")) == "full_time"
    assert categorize(make_job(title="Student Web Developer")) == "internship"


def test_categorize_trusts_internship_feeds_over_the_title():
    feed = "github/SimplifyJobs/Summer2026-Internships"
    assert categorize(make_job(title="Web Developer", source=feed)) == "internship"
    new_grad_feed = "github/SimplifyJobs/New-Grad-Positions"
    assert categorize(make_job(title="Web Developer", source=new_grad_feed)) == "full_time"
    # an ATS company slug containing "intern" is not a feed hint
    assert categorize(make_job(title="Web Developer", source="lever/internode")) == "full_time"


def test_message_credits_github_feed_and_hides_ats_slugs():
    ats = format_message(make_job(source="workday/nvidia"))
    assert "workday/nvidia" not in ats
    repo = "SimplifyJobs/New-Grad-Positions"
    feed = format_message(make_job(source=f"github/{repo}"))
    assert f'via <a href="https://github.com/{repo}">{repo}</a>' in feed


def test_age_counts_whole_days_since_posting():
    now = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
    assert _age("2026-09-27T01:00:00+00:00", now) == "🔥 today"
    assert _age("2026-09-26T23:59:00+00:00", now) == "1d ago"
    assert _age("2026-09-19", now) == "8d ago"  # naive date-only is read as UTC
    assert _age("2026-09-28T00:00:00+00:00", now) == "🔥 today"  # clock skew
    assert _age("not-a-date", now) is None


def test_message_and_digest_show_posting_age():
    assert "Posted: 2026-06-17 (" in format_message(make_job())
    [message] = format_digest([make_job(1, location="Toronto, Canada")])
    assert "\n  Toronto, Canada · " in message
    assert "d ago" in message


def test_group_duplicates_collapses_same_company_title_and_location():
    zt = [
        make_job(n, title="Validation  Engineer", company="ZT Systems", location="Secaucus, NJ")
        for n in range(3)
    ]
    other_city = make_job(9, title="Validation Engineer", company="ZT Systems", location="Austin")
    groups = group_duplicates([zt[0], other_city, zt[1], zt[2]])
    assert groups == [zt, [other_city]]


def test_digest_lists_duplicates_once_with_a_count():
    jobs = [make_job(n, title="Validation Engineer", location="Secaucus, NJ") for n in range(3)]
    [message] = format_digest([*jobs, make_job(7)])
    assert message.startswith("<b>💼 FULL-TIME (2)</b>")
    assert message.count("Validation Engineer") == 1
    assert "Validation Engineer</a> ×3" in message


def test_notify_sends_duplicates_once_and_records_every_copy(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(main.telegram, "send", lambda job, copies=1: sent.append((job, copies)))
    store = SeenStore(str(tmp_path / "seen.json"))
    dupes = [make_job(n, title="Validation Engineer") for n in range(3)]
    jobs = [*dupes, make_job(5)]

    result = main.notify(jobs, store, dry_run=False, digest_threshold=10)

    assert sent == [(dupes[0], 3), (jobs[3], 1)]
    assert result == jobs
    assert all(store.has(job.id) for job in jobs)


def test_notify_failed_send_leaves_every_duplicate_unseen(tmp_path, monkeypatch):
    def boom(job, copies=1):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(main.telegram, "send", boom)
    store = SeenStore(str(tmp_path / "seen.json"))
    dupes = [make_job(n, title="Validation Engineer") for n in range(3)]

    assert main.notify(dupes, store, dry_run=False, digest_threshold=10) == []
    assert not any(store.has(job.id) for job in dupes)


def test_notify_digest_threshold_counts_collapsed_entries(tmp_path, monkeypatch):
    digests, singles = [], []
    monkeypatch.setattr(main.telegram, "send_digest", digests.append)
    monkeypatch.setattr(main.telegram, "send", lambda job, copies=1: singles.append(job))
    store = SeenStore(str(tmp_path / "seen.json"))
    # 12 postings but only 2 distinct entries: stays under a threshold of 10
    jobs = [make_job(n, title="Validation Engineer") for n in range(11)] + [make_job(99)]

    main.notify(jobs, store, dry_run=False, digest_threshold=10)

    assert digests == []
    assert len(singles) == 2


def test_digest_lists_jobs_and_counts():
    jobs = [make_job(n) for n in range(1, 4)]
    messages = format_digest(jobs)
    assert len(messages) == 1
    assert messages[0].startswith("<b>💼 FULL-TIME (3)</b>")
    assert messages[0].count("<a href=") == 3


def test_digest_bolds_company_and_puts_location_on_its_own_line():
    [message] = format_digest([make_job(1, location="Toronto, Canada")])
    entry = [block for block in message.split("\n") if block.startswith("- ")][0]
    assert entry.startswith("- <b>Acme</b> — <a href=")
    assert "\n  Toronto, Canada" in message


def test_digest_credits_the_github_feed_a_job_came_from():
    repo = "SimplifyJobs/New-Grad-Positions"
    [message] = format_digest([make_job(1, source=f"github/{repo}")])
    assert f'via <a href="https://github.com/{repo}">{repo}</a>' in message


def test_digest_omits_the_feed_line_for_direct_ats_sources():
    [message] = format_digest([make_job(1, source="ashby/acme")])
    assert "via" not in message


def test_digest_capitalizes_company_slugs():
    [message] = format_digest([make_job(1, company="northrop-grumman")])
    assert "<b>Northrop Grumman</b>" in message


def test_display_company_fixes_slugs_but_trusts_cased_names():
    assert display_company("cursor") == "Cursor"
    assert display_company("texas-instruments") == "Texas Instruments"
    assert display_company("td") == "TD"  # acronym override
    assert display_company("openai") == "OpenAI"  # camel-case override
    assert display_company("Domino Data Lab") == "Domino Data Lab"
    assert display_company("eBay") == "eBay"  # already cased, left alone
    assert display_company("") == ""


def test_region_classifies_locations():
    assert _region("Toronto, Canada") == "canada"
    assert _region("Vancouver, BC") == "canada"
    assert _region("Waterloo, ON") == "canada"
    assert _region("Ottawa") == "canada"
    assert _region("Seattle, WA") == "us"
    assert _region("Redmond, Washington, United States") == "us"  # full name, capitalized
    assert _region("USA - Oklahoma City, OK") == "us"
    assert _region("New York, NY") == "us"
    assert _region("Remote - US") == "us"
    assert _region("U.S.") == "us"
    assert _region("Waterloo, IA") == "us"  # state code wins over city name
    assert _region("San Francisco") == "us"  # bare hub city
    assert _region("SF") == "us"
    assert _region("London, UK") == "other"
    assert _region("Remote") == "other"
    assert _region("") == "other"


def test_region_ignores_english_words_as_state_codes():
    # "or"/"in"/"me" must not be read as Oregon/Indiana/Maine.
    assert _region("Remote or hybrid") == "other"
    assert _region("Work in office") == "other"


def test_digest_groups_by_region_with_flags():
    jobs = [
        make_job(1, location="Toronto, Canada"),
        make_job(2, location="Seattle, WA"),
        make_job(3, location="London, UK"),
        make_job(4, location="Vancouver, BC"),
    ]
    [message] = format_digest(jobs)
    assert message.startswith("<b>💼 FULL-TIME (4)</b>")
    assert "<b>🇨🇦 Canada (2)</b>" in message
    assert "<b>🇺🇸 United States (1)</b>" in message
    assert "<b>🌐 Other (1)</b>" in message
    # region order: Canada before US before Other
    assert message.index("🇨🇦") < message.index("🇺🇸") < message.index("🌐")


def test_digest_omits_empty_regions():
    messages = format_digest([make_job(1, location="Toronto, Canada")])
    assert "🇨🇦 Canada (1)" in messages[0]
    assert "United States" not in messages[0]
    assert "Other" not in messages[0]


def test_digest_separates_categories_into_own_messages():
    jobs = [
        make_job(1, title="Software Engineering Intern"),
        make_job(2, title="New Grad Software Engineer"),
        make_job(3, title="Staff Software Engineer"),
        make_job(4, title="Infrastructure Intern"),
    ]
    messages = format_digest(jobs)
    assert len(messages) == 3
    assert messages[0].startswith("<b>🌱 INTERNSHIPS (2)</b>")
    assert messages[1].startswith("<b>🎓 NEW GRAD & JUNIOR (1)</b>")
    assert messages[2].startswith("<b>💼 FULL-TIME (1)</b>")
    assert messages[0].count("<a href=") == 2


def test_digest_splits_into_multiple_messages_under_telegram_cap():
    jobs = [make_job(n, title="X" * 200 + str(n)) for n in range(100)]
    messages = format_digest(jobs)
    assert len(messages) > 1
    assert all(len(m) <= 4000 for m in messages)
    # every job appears exactly once across all messages
    assert sum(m.count("<a href=") for m in messages) == 100
    assert messages[1].startswith("<b>💼 FULL-TIME (100)</b> (continued)")

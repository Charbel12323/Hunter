"""Per-source silent seeding: a source new to the watchlist has its stale
backlog recorded without notifying, keeps its recent postings, and existing
sources keep alerting normally."""

from datetime import UTC, datetime, timedelta

from scraper.main import seed_new_sources
from scraper.models import Job
from scraper.store import SeenStore


def make_job(n: int, source: str, posted_at: str | None = None) -> Job:
    return Job(
        id=f"{source.replace('/', ':')}:{n}",
        title=f"Engineer {n}",
        company=source.split("/")[1],
        location="Remote",
        url=f"https://example.com/{n}",
        posted_at=posted_at,
        description="",
        source=source,
    )


def days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def test_new_source_backlog_is_seeded_silently(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    old = make_job(1, "ashby/known")
    store.add(old)

    new_at_known = make_job(2, "ashby/known")  # genuinely new posting
    backlog = [make_job(n, "greenhouse/justadded") for n in range(3)]  # new source
    normalized = [old, new_at_known, *backlog]
    fresh = [new_at_known, *backlog]

    remaining = seed_new_sources(fresh, normalized, store)

    assert remaining == [new_at_known]  # existing source still alerts
    assert all(store.has(job.id) for job in backlog)  # backlog recorded
    assert not store.has(new_at_known.id)  # left for notify to record


def test_new_source_recent_postings_are_kept_for_notification(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    store.add(make_job(1, "ashby/known"))

    recent = make_job(10, "workday/justadded", posted_at=days_ago(3))
    stale = make_job(11, "workday/justadded", posted_at=days_ago(30))
    undated = make_job(12, "workday/justadded", posted_at=None)
    unparseable = make_job(13, "workday/justadded", posted_at="Posted Recently")
    fresh = [recent, stale, undated, unparseable]

    remaining = seed_new_sources(fresh, [make_job(1, "ashby/known"), *fresh], store, 14)

    assert remaining == [recent]  # inside the window: still alerts
    for job in (stale, undated, unparseable):
        assert store.has(job.id)  # everything else seeded silently
    assert not store.has(recent.id)  # left for notify to record


def test_without_max_age_days_whole_backlog_seeds_silently(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    store.add(make_job(1, "ashby/known"))

    backlog = [make_job(n, "lever/justadded", posted_at=days_ago(1)) for n in range(3)]
    remaining = seed_new_sources(backlog, [make_job(1, "ashby/known"), *backlog], store)

    assert remaining == []
    assert all(store.has(job.id) for job in backlog)


def test_source_recovering_from_outage_is_not_reseeded(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    store.add(make_job(1, "lever/flaky"))

    # After an outage the source comes back with one old and one new job:
    # it must alert for the new one, not silently swallow it.
    old, new = make_job(1, "lever/flaky"), make_job(2, "lever/flaky")
    remaining = seed_new_sources([new], [old, new], store)

    assert remaining == [new]

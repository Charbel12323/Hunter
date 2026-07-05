"""Per-source silent seeding: a source new to the watchlist has its backlog
recorded without notifying; existing sources keep alerting normally."""

from scraper.main import seed_new_sources
from scraper.models import Job
from scraper.store import SeenStore


def make_job(n: int, source: str) -> Job:
    return Job(
        id=f"{source.replace('/', ':')}:{n}",
        title=f"Engineer {n}",
        company=source.split("/")[1],
        location="Remote",
        url=f"https://example.com/{n}",
        posted_at=None,
        description="",
        source=source,
    )


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


def test_source_recovering_from_outage_is_not_reseeded(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    store.add(make_job(1, "lever/flaky"))

    # After an outage the source comes back with one old and one new job:
    # it must alert for the new one, not silently swallow it.
    old, new = make_job(1, "lever/flaky"), make_job(2, "lever/flaky")
    remaining = seed_new_sources([new], [old, new], store)

    assert remaining == [new]

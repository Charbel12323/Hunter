import json

from scraper.models import Job
from scraper.store import SeenStore


def make_job(job_id: str = "ashby:acme:123") -> Job:
    return Job(
        id=job_id,
        title="Software Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/123",
        posted_at=None,
        description="Build things.",
        source="ashby/acme",
    )


def test_missing_file_yields_empty_store(tmp_path):
    store = SeenStore(str(tmp_path / "seen.json"))
    assert len(store) == 0
    assert not store.has("anything")


def test_bare_empty_object_is_accepted_as_first_run_state(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{}", encoding="utf-8")
    store = SeenStore(str(path))
    assert len(store) == 0


def test_round_trip(tmp_path):
    path = str(tmp_path / "seen.json")
    store = SeenStore(path)
    store.add(make_job("ashby:acme:1"))
    store.add(make_job("ashby:acme:2"))
    store.save()

    reloaded = SeenStore(path)
    assert len(reloaded) == 2
    assert reloaded.has("ashby:acme:1")
    assert reloaded.has("ashby:acme:2")
    assert not reloaded.has("ashby:acme:3")


def test_prune_removes_old_entries_and_keeps_recent_ones(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text(
        json.dumps(
            {
                "jobs": {
                    "old:1": {"seen_at": "2020-01-01T00:00:00+00:00"},
                    "undated:1": {},
                }
            }
        ),
        encoding="utf-8",
    )
    store = SeenStore(str(path))
    store.add(make_job("fresh:1"))

    store.prune(max_age_days=60)

    assert not store.has("old:1")
    assert store.has("fresh:1")
    assert store.has("undated:1")  # undated entries are kept, not pruned

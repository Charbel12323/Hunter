"""Stage 3 tests: retry with backoff, the bulkhead, and health thresholds.
All offline - HTTP mocked, dates injected."""

import time
from datetime import date, timedelta

import pytest
import requests
import responses

from scraper import health, main
from scraper.adapters import REGISTRY, ashby
from scraper.models import Job

ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/acme"


def make_job(n: int = 1, source: str = "ashby/acme") -> Job:
    return Job(
        id=f"{source}:{n}",
        title=f"Engineer {n}",
        company="acme",
        location="Remote",
        url=f"https://example.com/{n}",
        posted_at=None,
        description="",
        source=source,
    )


@pytest.fixture
def no_waits(monkeypatch):
    waits: list[int] = []
    monkeypatch.setattr(main, "RETRY_WAITS", (1, 4, 16))
    monkeypatch.setattr(main.time, "sleep", waits.append)
    return waits


# --- retry -------------------------------------------------------------------


@responses.activate
def test_retry_backs_off_on_5xx_then_succeeds(no_waits):
    responses.get(ASHBY_URL, status=500)
    responses.get(ASHBY_URL, status=503)
    responses.get(ASHBY_URL, json={"jobs": []})

    result = main.fetch_with_retry(ashby.fetch, {"company": "acme"}, "ashby/acme")

    assert result == []
    assert len(responses.calls) == 3
    assert no_waits == [1, 4]  # increasing backoff between attempts


@responses.activate
def test_retry_gives_up_after_all_attempts(no_waits):
    for _ in range(4):
        responses.get(ASHBY_URL, status=500)

    with pytest.raises(requests.exceptions.HTTPError):
        main.fetch_with_retry(ashby.fetch, {"company": "acme"}, "ashby/acme")

    assert len(responses.calls) == 4  # initial try + one per wait
    assert no_waits == [1, 4, 16]


@responses.activate
def test_4xx_fails_fast_without_retry(no_waits):
    responses.get(ASHBY_URL, status=404)

    with pytest.raises(requests.exceptions.HTTPError):
        main.fetch_with_retry(ashby.fetch, {"company": "acme"}, "ashby/acme")

    assert len(responses.calls) == 1
    assert no_waits == []


# --- bulkhead ----------------------------------------------------------------


def test_one_broken_source_does_not_sink_the_run(monkeypatch):
    def boom(config):
        raise RuntimeError("adapter exploded")

    def ok(config):
        return [make_job(1, source="ok/good")]

    monkeypatch.setitem(REGISTRY, "boom", boom)
    monkeypatch.setitem(REGISTRY, "ok", ok)

    jobs, stats = main.fetch_all(
        [{"type": "boom", "name": "bad"}, {"type": "ok", "name": "good"}]
    )

    assert [job.source for job in jobs] == ["ok/good"]
    assert stats["boom/bad"] == {"fetched": 0, "errors": 1}
    assert stats["ok/good"] == {"fetched": 1, "errors": 0}


def test_sources_are_fetched_concurrently(monkeypatch):
    def slow(config):
        time.sleep(0.2)
        return []

    monkeypatch.setitem(REGISTRY, "slow", slow)

    start = time.perf_counter()
    main.fetch_all([{"type": "slow", "name": "a"}, {"type": "slow", "name": "b"}])
    elapsed = time.perf_counter() - start

    # Sequential would take >= 0.4s; concurrent should land close to 0.2s.
    assert elapsed < 0.35


# --- health ------------------------------------------------------------------


def failing(n_errors: int = 1) -> dict:
    return {"fetched": 0, "errors": n_errors}


def succeeding(fetched: int) -> dict:
    return {"fetched": fetched, "errors": 0}


def test_consecutive_failures_warn_exactly_once():
    counters: dict = {}
    warnings = []
    for _ in range(8):
        warnings += health.record_run(counters, {"lever/acme": failing()})

    assert len(warnings) == 1  # fired at 5, silent at 6, 7, 8
    assert "failed 5 runs in a row" in warnings[0]


def test_failure_counter_resets_on_success_and_can_warn_again():
    counters: dict = {}
    for _ in range(5):
        health.record_run(counters, {"lever/acme": failing()})
    health.record_run(counters, {"lever/acme": succeeding(10)})
    assert counters["lever/acme"]["consecutive_failures"] == 0

    warnings = []
    for _ in range(5):
        warnings += health.record_run(counters, {"lever/acme": failing()})
    assert len(warnings) == 1  # a fresh breakage warns again


def test_zero_jobs_warns_once_after_seven_days():
    counters: dict = {}
    start = date(2026, 7, 1)
    warnings = []
    for day in range(10):
        warnings += health.record_run(
            counters, {"lever/acme": succeeding(0)}, today=start + timedelta(days=day)
        )

    assert len(warnings) == 1
    assert "zero jobs since 2026-07-01" in warnings[0]
    assert "migrated" in warnings[0]


def test_zero_jobs_window_resets_when_jobs_return():
    counters: dict = {}
    start = date(2026, 7, 1)
    for day in range(5):
        health.record_run(
            counters, {"lever/acme": succeeding(0)}, today=start + timedelta(days=day)
        )
    health.record_run(counters, {"lever/acme": succeeding(3)}, today=start + timedelta(days=5))

    assert counters["lever/acme"]["zero_since"] is None
    assert not counters["lever/acme"]["warned_zero"]


def test_removed_sources_are_dropped_from_health():
    counters: dict = {}
    health.record_run(counters, {"lever/acme": succeeding(3), "ashby/beta": succeeding(1)})
    health.record_run(counters, {"ashby/beta": succeeding(1)})

    assert "lever/acme" not in counters
    assert "ashby/beta" in counters


def test_store_round_trips_health(tmp_path):
    from scraper.store import SeenStore

    path = str(tmp_path / "seen.json")
    store = SeenStore(path)
    store.health["lever/acme"] = {"consecutive_failures": 2}
    store.save()

    assert SeenStore(path).health == {"lever/acme": {"consecutive_failures": 2}}

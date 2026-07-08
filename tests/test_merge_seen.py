"""Union-merge of two seen_jobs.json snapshots (overlapping-run recovery)."""

import json

from scraper.merge_seen import main, merge


def test_merge_unions_jobs_and_keeps_earliest_seen_at():
    ours = {
        "jobs": {
            "a": {"seen_at": "2026-07-08T10:00:00+00:00"},
            "b": {"seen_at": "2026-07-08T12:00:00+00:00"},
        },
        "health": {"ashby/x": {"consecutive_failures": 0}},
    }
    theirs = {
        "jobs": {
            "b": {"seen_at": "2026-07-08T11:00:00+00:00"},
            "c": {"seen_at": "2026-07-08T09:00:00+00:00"},
        },
        "health": {"ashby/x": {"consecutive_failures": 3}},
    }

    merged = merge(ours, theirs)

    assert set(merged["jobs"]) == {"a", "b", "c"}  # a job seen by either side stays seen
    assert merged["jobs"]["b"]["seen_at"] == "2026-07-08T11:00:00+00:00"  # earliest wins
    assert merged["health"] == ours["health"]  # ours is the most recent completed run


def test_cli_writes_canonical_store_format(tmp_path):
    ours_path = tmp_path / "ours.json"
    theirs_path = tmp_path / "theirs.json"
    ours_path.write_text(json.dumps({"jobs": {"a": {"seen_at": "2026-07-08T10:00:00+00:00"}}}))
    theirs_path.write_text(json.dumps({"jobs": {"b": {"seen_at": "2026-07-08T11:00:00+00:00"}}}))

    assert main([str(ours_path), str(theirs_path)]) == 0

    merged = json.loads(ours_path.read_text())
    assert set(merged["jobs"]) == {"a", "b"}
    assert ours_path.read_text().endswith("\n")

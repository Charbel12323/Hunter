"""Union-merge two seen_jobs.json snapshots: ``python -m scraper.merge_seen OURS THEIRS``.

Used by the poll workflow when its push is rejected because an overlapping
run pushed state first (GitHub's concurrency group has let two runs execute
simultaneously, and a textual rebase of the JSON state conflicts on adjacent
lines). The merge is semantic instead: the union of both sides' jobs, since
a job recorded seen by either side has already been notified or filtered and
must never alert again. When both sides have a job, the earliest seen_at
wins so prune ages it from the first sighting. Health counters keep OURS,
which reflects the most recently completed run.

The result is written back to OURS in the store's canonical format.
"""

import json
import sys


def merge(ours: dict, theirs: dict) -> dict:
    jobs = dict(theirs.get("jobs", {}))
    for job_id, meta in ours.get("jobs", {}).items():
        other = jobs.get(job_id)
        if other is None or (meta.get("seen_at") or "") < (other.get("seen_at") or ""):
            jobs[job_id] = meta
    return {"jobs": jobs, "health": ours.get("health", {}) or theirs.get("health", {})}


def main(argv: list[str] | None = None) -> int:
    ours_path, theirs_path = (argv or sys.argv[1:])[:2]
    with open(ours_path, encoding="utf-8") as f:
        ours = json.load(f)
    with open(theirs_path, encoding="utf-8") as f:
        theirs = json.load(f)
    merged = merge(ours, theirs)
    with open(ours_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
        f.write("\n")
    print(
        f"Merged {len(theirs.get('jobs', {}))} remote + "
        f"{len(ours.get('jobs', {}))} local -> {len(merged['jobs'])} jobs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

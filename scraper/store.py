"""SeenStore - the persistent set of job ids we have already processed.

Backed by a JSON file that GitHub Actions commits back to the repo after each
run. The pipeline talks only to has/add/prune/save, so the backend can become
SQLite/S3 later with zero changes elsewhere.

File layout (a bare ``{}`` is also accepted as the empty first-run state):

    {
      "jobs": {
        "<job_id>": {"seen_at": "2026-07-05T14:00:00+00:00"}
      }
    }
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from scraper.models import Job

log = logging.getLogger(__name__)


class SeenStore:
    def __init__(self, path: str = "seen_jobs.json") -> None:
        self.path = path
        self._jobs: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f) or {}
        except FileNotFoundError:
            log.info("%s not found; starting with an empty store (first run).", self.path)
            return
        self._jobs = data.get("jobs", {})

    def __len__(self) -> int:
        return len(self._jobs)

    def has(self, job_id: str) -> bool:
        return job_id in self._jobs

    def add(self, job: Job) -> None:
        self._jobs[job.id] = {"seen_at": datetime.now(UTC).isoformat(timespec="seconds")}

    def prune(self, max_age_days: int) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        stale = [job_id for job_id, meta in self._jobs.items() if self._seen_at(meta) < cutoff]
        for job_id in stale:
            del self._jobs[job_id]
        if stale:
            log.info("Pruned %d store entries older than %d days.", len(stale), max_age_days)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"jobs": self._jobs}, f, indent=2, sort_keys=True)
            f.write("\n")

    @staticmethod
    def _seen_at(meta: dict) -> datetime:
        # An entry we can't date is kept (treated as fresh): keeping a seen id
        # only suppresses a duplicate, while dropping one risks a re-notify.
        try:
            return datetime.fromisoformat(meta["seen_at"])
        except (KeyError, TypeError, ValueError):
            return datetime.now(UTC)

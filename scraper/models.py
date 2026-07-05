"""The Job schema - the one shape the entire pipeline speaks.

Every adapter returns a list of these; every later stage (dedup, filter,
notify) consumes them without knowing which source they came from.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    id: str  # stable dedup key: "{source_type}:{company}:{ats_job_id}",
    #          falling back to a hash of the URL if no ATS id exists
    title: str
    company: str
    location: str
    url: str  # the apply / posting link
    posted_at: str | None
    description: str  # plain text, truncated to ~500 chars
    source: str  # human-readable, e.g. "ashby/wealthsimple"

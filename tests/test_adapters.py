"""Adapter mapping tests. All HTTP is mocked from recorded fixtures - no
network access. Each test asserts the fixture's real payload maps to the
canonical Job shape, including the skip rules."""

import json
from datetime import UTC, datetime, timedelta

import responses

from scraper.adapters import (
    REGISTRY,
    ashby,
    get_adapter,
    github_repo,
    greenhouse,
    lever,
    workday,
)


def test_registry_dispatches_all_five_types():
    for type_str in ["ashby", "greenhouse", "lever", "github", "workday"]:
        assert callable(get_adapter(type_str))


def test_registry_rejects_unknown_type():
    try:
        get_adapter("taleo")
        raise AssertionError("expected KeyError")
    except KeyError as exc:
        assert "taleo" in str(exc)
        assert "ashby" in str(exc)  # error names the known types


def test_registry_has_no_stale_entries():
    assert set(REGISTRY) == {"ashby", "greenhouse", "lever", "github", "workday"}


@responses.activate
def test_ashby_maps_jobs_and_skips_unlisted(fixture):
    responses.get(
        "https://api.ashbyhq.com/posting-api/job-board/wealthsimple",
        json=fixture("ashby_wealthsimple.json"),
    )
    jobs = ashby.fetch({"type": "ashby", "company": "wealthsimple"})

    assert len(jobs) == 2  # the fixture's third posting is unlisted
    job = jobs[0]
    assert job.id.startswith("ashby:wealthsimple:")
    assert job.title and job.url and job.location
    assert job.company == "wealthsimple"
    assert job.source == "ashby/wealthsimple"
    assert len(job.description) <= 500
    assert "<" not in job.description  # plain text, no HTML
    assert all(j.title != "Hidden Posting" for j in jobs)


@responses.activate
def test_greenhouse_maps_jobs(fixture):
    responses.get(
        "https://boards-api.greenhouse.io/v1/boards/duolingo/jobs",
        json=fixture("greenhouse_duolingo.json"),
    )
    jobs = greenhouse.fetch({"type": "greenhouse", "company": "duolingo"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id.startswith("greenhouse:duolingo:")
    assert job.title and job.url and job.location
    assert job.posted_at  # first_published or updated_at
    assert len(job.description) <= 500
    assert "&lt;" not in job.description  # double-escaped HTML fully unescaped
    assert "<" not in job.description


@responses.activate
def test_lever_maps_jobs(fixture):
    responses.get(
        "https://api.lever.co/v0/postings/palantir",
        json=fixture("lever_palantir.json"),
    )
    jobs = lever.fetch({"type": "lever", "company": "palantir"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id.startswith("lever:palantir:")
    assert job.title and job.url and job.location
    assert job.posted_at and job.posted_at.startswith("20")  # ms epoch -> ISO
    assert len(job.description) <= 500


@responses.activate
def test_github_maps_active_visible_listings(fixture, tmp_path):
    url = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/l.json"
    responses.get(url, json=fixture("github_listings.json"), headers={"ETag": 'W/"abc"'})
    config = {
        "type": "github",
        "repo": "SimplifyJobs/New-Grad-Positions",
        "path": "l.json",
        "branch": "dev",
        "etag_cache_path": str(tmp_path / "etags.json"),
    }
    jobs = github_repo.fetch(config)

    assert len(jobs) == 2  # inactive and invisible listings skipped
    job = jobs[0]
    assert job.id.startswith("github:SimplifyJobs/New-Grad-Positions:")
    assert job.title and job.url and job.company
    assert all(j.title not in ("Old Job", "Hidden Job") for j in jobs)


@responses.activate
def test_github_304_returns_empty_without_parsing(fixture, tmp_path):
    url = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/l.json"
    cache_path = str(tmp_path / "etags.json")
    config = {
        "type": "github",
        "repo": "SimplifyJobs/New-Grad-Positions",
        "path": "l.json",
        "branch": "dev",
        "etag_cache_path": cache_path,
    }

    responses.get(url, json=fixture("github_listings.json"), headers={"ETag": 'W/"abc"'})
    assert len(github_repo.fetch(config)) == 2  # first run primes the cache

    responses.reset()
    responses.get(url, status=304)
    assert github_repo.fetch(config) == []
    # and the conditional header was actually sent
    assert responses.calls[0].request.headers["If-None-Match"] == 'W/"abc"'


WORKDAY_URL = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
WORKDAY_CONFIG = {
    "type": "workday",
    "company": "nvidia",
    "instance": "wd5",
    "site": "NVIDIAExternalCareerSite",
}


@responses.activate
def test_workday_maps_jobs_and_relative_dates(fixture):
    responses.post(WORKDAY_URL, json=fixture("workday_nvidia.json"))
    jobs = workday.fetch(WORKDAY_CONFIG)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.id == "workday:nvidia:JR2020259"
    assert job.title.startswith("Software Engineer")
    assert job.company == "nvidia"
    assert job.location == "US, CA, Santa Clara"
    assert job.url == (
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        "/job/US-CA-Santa-Clara/Software-Engineer--GPU-Compute_JR2020259"
    )
    assert job.source == "workday/nvidia"

    today = datetime.now(UTC).date()
    assert jobs[0].posted_at == today.isoformat()  # "Posted Today"
    # "Posted 30+ Days Ago" maps to exactly 30 days back - old enough either way
    assert jobs[1].posted_at == (today - timedelta(days=30)).isoformat()
    assert jobs[2].posted_at is None  # missing postedOn stays undated (never-miss)
    # empty bulletFields falls back to the stable externalPath
    assert jobs[2].id == "workday:nvidia:/job/US-WA-Redmond/Site-Reliability-Engineer_JR2019001"


@responses.activate
def test_workday_stops_at_page_cap_not_board_size():
    calls = []

    def callback(request):
        body = json.loads(request.body)
        calls.append(body)
        postings = [
            {
                "title": f"Engineer {body['offset'] + i}",
                "externalPath": f"/job/X/Engineer_{body['offset'] + i}",
                "locationsText": "US",
                "postedOn": "Posted Today",
                "bulletFields": [f"JR{body['offset'] + i}"],
            }
            for i in range(20)
        ]
        return (200, {}, json.dumps({"total": 1000, "jobPostings": postings}))

    responses.add_callback(responses.POST, WORKDAY_URL, callback=callback)
    jobs = workday.fetch(WORKDAY_CONFIG)

    assert len(calls) == 3  # DEFAULT_PAGES, not total/20 = 50 requests
    assert [c["offset"] for c in calls] == [0, 20, 40]
    assert all(c["limit"] == 20 for c in calls)  # the server's hard cap
    assert len(jobs) == 60


@responses.activate
def test_workday_stops_early_on_small_board(fixture):
    responses.post(WORKDAY_URL, json=fixture("workday_nvidia.json"))
    workday.fetch(WORKDAY_CONFIG)
    assert len(responses.calls) == 1  # total=3 fits in one page


@responses.activate
def test_workday_trusts_only_first_page_total():
    # Some tenants (e.g. visa) report total=0 on every offset>0 page that
    # still carries postings; pagination must not stop early because of it.
    def callback(request):
        offset = json.loads(request.body)["offset"]
        postings = [
            {
                "title": f"Engineer {offset + i}",
                "externalPath": f"/job/X/Engineer_{offset + i}",
                "locationsText": "US",
                "postedOn": "Posted Today",
                "bulletFields": [f"JR{offset + i}"],
            }
            for i in range(20)
        ]
        total = 500 if offset == 0 else 0
        return (200, {}, json.dumps({"total": total, "jobPostings": postings}))

    responses.add_callback(responses.POST, WORKDAY_URL, callback=callback)
    jobs = workday.fetch(WORKDAY_CONFIG)

    assert len(responses.calls) == 3  # all DEFAULT_PAGES fetched despite total=0
    assert len(jobs) == 60

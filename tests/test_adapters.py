"""Adapter mapping tests. All HTTP is mocked from recorded fixtures - no
network access. Each test asserts the fixture's real payload maps to the
canonical Job shape, including the skip rules."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import responses

from scraper.adapters import (
    REGISTRY,
    amazon,
    ashby,
    get_adapter,
    github_repo,
    google_careers,
    greenhouse,
    lever,
    workday,
)


def test_registry_dispatches_all_seven_types():
    for type_str in ["ashby", "greenhouse", "lever", "github", "workday", "amazon", "google"]:
        assert callable(get_adapter(type_str))


def test_registry_rejects_unknown_type():
    try:
        get_adapter("taleo")
        raise AssertionError("expected KeyError")
    except KeyError as exc:
        assert "taleo" in str(exc)
        assert "ashby" in str(exc)  # error names the known types


def test_registry_has_no_stale_entries():
    assert set(REGISTRY) == {
        "ashby",
        "greenhouse",
        "lever",
        "github",
        "workday",
        "amazon",
        "google",
    }


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


AMAZON_URL = "https://www.amazon.jobs/en/search.json"


@responses.activate
def test_amazon_maps_jobs(fixture):
    responses.get(AMAZON_URL, json=fixture("amazon_search.json"))
    jobs = amazon.fetch({"type": "amazon", "name": "canada", "country": "CAN"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "amazon:amazon:3059609"
    assert job.title.startswith("Software Development Engineer")
    assert job.company == "amazon"
    assert job.source == "amazon/canada"
    assert job.location == "Toronto, Ontario, CAN"
    assert job.url == (
        "https://www.amazon.jobs/en/jobs/3059609"
        "/software-development-engineer-amazon-fulfillment-technologies"
    )
    assert job.posted_at == "2026-07-14"  # "July 14, 2026" -> ISO
    assert "<" not in job.description and "&amp;" not in job.description
    # missing normalized_location falls back to the raw one; missing
    # posted_date stays undated (never-miss: the age filter keeps it)
    assert jobs[1].location == "CA, BC, Vancouver"
    assert jobs[1].posted_at is None

    request = responses.calls[0].request
    assert "sort=recent" in request.url  # newest-first, so one page is lossless
    assert "country=CAN" in request.url
    assert "Mozilla" in request.headers["User-Agent"]  # default UA gets a 403


GOOGLE_URL = "https://www.google.com/about/careers/applications/jobs/results"


def _google_page(entries: list, total: int) -> str:
    blob = json.dumps([entries, None, total, 20])
    return (
        "<html><body><script>AF_initDataCallback({key: 'ds:1', hash: '2', "
        f"data:{blob}, sideChannel: {{}}}});</script></body></html>"
    )


def _google_entry(job_id: str, title: str = "Software Engineer") -> list:
    entry: list = [None] * 21
    entry[0] = job_id
    entry[1] = title
    entry[7] = "Google"
    entry[9] = [["Toronto, ON, Canada", [], "Toronto"]]
    entry[10] = [None, "<p>desc</p>"]
    entry[12] = [1783000000, 0]
    return entry


@responses.activate
def test_google_maps_jobs_and_skips_malformed(fixture):
    responses.get(GOOGLE_URL, body=fixture("google_careers.html"))
    jobs = google_careers.fetch({"type": "google", "name": "canada", "location": "Canada"})

    assert len(jobs) == 2  # the fixture's truncated third entry is skipped
    job = jobs[0]
    assert job.id == "google:google:142342334078427846"
    assert job.title == "Software Developer III, Google Cloud"
    assert job.company == "Google"
    assert job.source == "google/canada"
    assert job.location == "Toronto, ON, Canada; Waterloo, ON, Canada"
    assert job.url == GOOGLE_URL + "/142342334078427846"
    assert job.posted_at and job.posted_at.startswith("20")  # epoch -> ISO
    assert "<" not in job.description and "&#39;" not in job.description
    # entry with null company/timestamps still maps (never-miss)
    assert jobs[1].company == "Google"
    assert jobs[1].posted_at is None

    request = responses.calls[0].request
    assert "sort_by=date" in request.url
    assert "location=Canada" in request.url
    assert len(responses.calls) == 1  # 2 entries < page size: pagination stops


@responses.activate
def test_google_paginates_and_dedupes_shifted_entries():
    pages = {
        "1": _google_page([_google_entry(str(i)) for i in range(20)], total=26),
        "2": _google_page([_google_entry(str(i)) for i in range(19, 26)], total=26),
    }

    def callback(request):
        page = dict(parse_qsl(urlsplit(request.url).query)).get("page", "1")
        return (200, {}, pages[page])

    responses.add_callback(responses.GET, GOOGLE_URL, callback=callback)
    jobs = google_careers.fetch({"type": "google"})

    assert len(responses.calls) == 2  # DEFAULT_PAGES
    # entry 19 slid onto page 2 between requests; it must not alert twice
    assert len(jobs) == 26
    assert len({job.id for job in jobs}) == 26


@responses.activate
def test_google_raises_when_data_blob_missing():
    responses.get(GOOGLE_URL, body="<html><body>redesigned page</body></html>")
    try:
        google_careers.fetch({"type": "google"})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "ds:1" in str(exc)  # a layout change must alarm, not report 0 jobs

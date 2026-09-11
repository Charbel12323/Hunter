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
    apple,
    ashby,
    get_adapter,
    github_repo,
    google_careers,
    greenhouse,
    lever,
    microsoft,
    netflix,
    rivian,
    uber,
    ultipro,
    workday,
)


def test_registry_dispatches_all_types():
    types = [
        "ashby",
        "greenhouse",
        "lever",
        "github",
        "workday",
        "amazon",
        "google",
        "microsoft",
        "ultipro",
        "apple",
        "netflix",
        "rivian",
        "uber",
    ]
    for type_str in types:
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
        "microsoft",
        "ultipro",
        "apple",
        "netflix",
        "rivian",
        "uber",
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


MICROSOFT_URL = "https://apply.careers.microsoft.com/api/pcsx/search"


@responses.activate
def test_microsoft_maps_jobs(fixture):
    responses.get(MICROSOFT_URL, json=fixture("microsoft_search.json"))
    jobs = microsoft.fetch({"type": "microsoft", "name": "canada", "location": "Canada"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "microsoft:microsoft:200041999"  # displayJobId, not internal id
    assert job.title == "Software Engineer II - Full Stack"
    assert job.company == "microsoft"
    assert job.source == "microsoft/canada"
    # multiple locations are joined
    assert job.location == "Canada, British Columbia, Vancouver; Canada, Ontario, Toronto"
    assert job.url == "https://apply.careers.microsoft.com/careers/job/1970393556914401"
    assert job.posted_at and job.posted_at.startswith("20")  # epoch -> ISO
    # missing postedTs stays undated (never-miss); missing positionUrl falls
    # back to the id-derived path
    assert jobs[1].posted_at is None
    assert jobs[1].url == "https://apply.careers.microsoft.com/careers/job/1970393556862981"

    request = responses.calls[0].request
    assert "sort_by=timestamp" in request.url  # newest-first
    assert "location=Canada" in request.url
    assert "domain=microsoft.com" in request.url
    assert "Mozilla" in request.headers["User-Agent"]


@responses.activate
def test_microsoft_paginates_and_dedupes_shifted_entries():
    def position(i):
        return {
            "id": 1000 + i,
            "displayJobId": str(i),
            "name": f"Engineer {i}",
            "locations": ["Canada, Ontario, Toronto"],
            "postedTs": 1784234857,
            "positionUrl": f"/careers/job/{1000 + i}",
        }

    pages = {
        0: [position(i) for i in range(10)],
        10: [position(i) for i in range(9, 19)],  # entry 9 slid onto page 2
        20: [position(i) for i in range(19, 24)],  # short page ends pagination
    }

    def callback(request):
        start = int(dict(parse_qsl(urlsplit(request.url).query)).get("start", "0"))
        return (200, {}, json.dumps({"data": {"positions": pages[start]}}))

    responses.add_callback(responses.GET, MICROSOFT_URL, callback=callback)
    jobs = microsoft.fetch({"type": "microsoft"})

    assert len(responses.calls) == 3  # DEFAULT_PAGES, last page short
    assert len(jobs) == 24  # 0..23, the shared entry 9 counted once
    assert len({job.id for job in jobs}) == 24


ULTIPRO_URL = (
    "https://recruiting.ultipro.ca/PAS5000PASON/JobBoard"
    "/736c1025-c469-4ece-a487-4884545272a7/JobBoardView/LoadSearchResults"
)
ULTIPRO_CONFIG = {
    "type": "ultipro",
    "company": "pason",
    "host": "recruiting.ultipro.ca",
    "tenant": "PAS5000PASON",
    "board": "736c1025-c469-4ece-a487-4884545272a7",
}


@responses.activate
def test_ultipro_maps_jobs(fixture):
    responses.post(ULTIPRO_URL, json=fixture("ultipro_pason.json"))
    jobs = ultipro.fetch(ULTIPRO_CONFIG)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.id == "ultipro:pason:FIELD001983"  # RequisitionNumber, not the GUID
    assert job.title == "Field Service Technician - Peace River"
    assert job.company == "pason"
    assert job.source == "ultipro/pason"
    assert job.location == "Remote Alberta"
    assert job.url == (
        "https://recruiting.ultipro.ca/PAS5000PASON/JobBoard"
        "/736c1025-c469-4ece-a487-4884545272a7/OpportunityDetail"
        "?opportunityId=173a7329-3e04-4b96-ba18-bf372d2d7671"
    )
    assert job.posted_at == "2026-07-23T19:47:22.998Z"  # real ISO timestamp
    assert len(job.description) <= 500

    # BriefDescription newlines are collapsed to plain single-spaced text
    assert "\n" not in jobs[1].description
    # null LocalizedName falls back to the structured Address; missing
    # PostedDate stays undated (never-miss)
    assert jobs[2].location == "Athabasca, AB, CAN"
    assert jobs[2].posted_at is None

    body = json.loads(responses.calls[0].request.body)
    assert body["opportunitySearch"]["OrderBy"][0]["Value"] == "postedDateDesc"  # newest-first
    assert len(responses.calls) == 1  # totalCount=3 fits in one page


@responses.activate
def test_ultipro_paginates_and_dedupes_shifted_entries():
    def opportunity(i):
        return {
            "Id": f"guid-{i}",
            "Title": f"Engineer {i}",
            "RequisitionNumber": f"REQ{i:06d}",
            "Locations": [{"LocalizedName": "Calgary"}],
            "PostedDate": "2026-07-23T00:00:00.000Z",
            "BriefDescription": "desc",
        }

    pages = {
        0: [opportunity(i) for i in range(50)],
        50: [opportunity(i) for i in range(49, 70)],  # entry 49 slid onto page 2
    }

    def callback(request):
        skip = json.loads(request.body)["opportunitySearch"]["Skip"]
        return (200, {}, json.dumps({"totalCount": 70, "opportunities": pages[skip]}))

    responses.add_callback(responses.POST, ULTIPRO_URL, callback=callback)
    jobs = ultipro.fetch(ULTIPRO_CONFIG)

    assert len(responses.calls) == 2  # 70 postings fit in two pages of 50
    assert len(jobs) == 70  # the shared entry 49 counted once
    assert len({job.id for job in jobs}) == 70


APPLE_URL = "https://jobs.apple.com/en-us/search"


def _apple_page(entries: list[tuple[str, str]], total: int) -> str:
    # entries: list of (job_id, title) pairs.
    blocks = "".join(
        f'<div><h3><a class="link-inline" aria-label="{title} {job_id}" '
        f'href="/en-us/details/{job_id}/{title.lower().replace(" ", "-")}?team=SFTWR" '
        f'data-discover="true">{title}</a></h3>'
        f'<span class="team-name mt-0">Software and Services</span>'
        f'<span class="job-posted-date">Sep 11, 2026</span></div>'
        f'<div class="job-title-location"><span class="a11y">Location</span>'
        f'<span id="search-store-name-container-1">Vancouver</span></div>'
        for job_id, title in entries
    )
    return f"<html><body><div>{total} Result(s)</div>{blocks}</body></html>"


@responses.activate
def test_apple_maps_jobs_and_unescapes_html(fixture):
    responses.get(APPLE_URL, body=fixture("apple_search.html"))
    jobs = apple.fetch({"type": "apple", "name": "canada"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "apple:apple:200680206-3350"
    assert job.title == "R&D Software Engineer"  # &amp; unescaped
    assert job.company == "apple"
    assert job.source == "apple/canada"
    assert job.location == "Vancouver"
    assert job.url == (
        "https://jobs.apple.com/en-us/details/200680206-3350/rd-software-engineer"
    )
    assert job.posted_at == "2026-09-11"  # "Sep 11, 2026" -> ISO
    # the multi-location variant (store-name, no "-container") still maps
    assert jobs[1].location == "Various Locations within Canada"

    request = responses.calls[0].request
    assert "location=canada-CANC" in request.url  # default geo code


@responses.activate
def test_apple_stops_at_total_result_count():
    pages = {
        1: _apple_page([(str(i), f"Engineer {i}") for i in range(20)], total=22),
        2: _apple_page([(str(i), f"Engineer {i}") for i in range(20, 22)], total=22),
    }

    def callback(request):
        page = int(dict(parse_qsl(urlsplit(request.url).query)).get("page", "1"))
        return (200, {}, pages[page])

    responses.add_callback(responses.GET, APPLE_URL, callback=callback)
    jobs = apple.fetch({"type": "apple"})

    assert len(responses.calls) == 2  # 22 results fit in two pages of 20
    assert len(jobs) == 22


NETFLIX_URL = "https://explore.jobs.netflix.net/api/apply/v2/jobs"


@responses.activate
def test_netflix_maps_jobs_and_falls_back_ids(fixture):
    responses.get(NETFLIX_URL, json=fixture("netflix_jobs.json"))
    jobs = netflix.fetch({"type": "netflix", "name": "canada", "location": "Canada"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "netflix:netflix:JR42454"  # display_job_id
    assert job.title == "Front-End Developer - Netflix Animation Studios"
    assert job.company == "netflix"
    assert job.source == "netflix/canada"
    assert job.location == "Vancouver,Canada"
    assert job.url == "https://explore.jobs.netflix.net/careers/job/790318358916"
    assert job.posted_at and job.posted_at.startswith("2026")
    assert job.description == ""  # Eightfold search payload carries none
    # empty display_job_id falls back to the internal id; null
    # canonicalPositionUrl falls back to the constructed job-detail URL
    assert jobs[1].id == "netflix:netflix:790316470001"
    assert jobs[1].url == "https://explore.jobs.netflix.net/careers/job/790316470001"

    request = responses.calls[0].request
    assert "sort_by=timestamp" in request.url  # newest-first
    assert "location=Canada" in request.url


@responses.activate
def test_netflix_paginates_and_dedupes_shifted_entries():
    def position(i):
        return {
            "id": 1000 + i,
            "name": f"Engineer {i}",
            "locations": ["Toronto,Canada"],
            "t_create": 1784234857,
            "display_job_id": str(i),
            "canonicalPositionUrl": f"https://explore.jobs.netflix.net/careers/job/{1000 + i}",
        }

    pages = {
        0: [position(i) for i in range(10)],
        10: [position(i) for i in range(9, 19)],  # entry 9 slid onto page 2
        20: [position(i) for i in range(19, 24)],  # short page ends pagination
    }

    def callback(request):
        start = int(dict(parse_qsl(urlsplit(request.url).query)).get("start", "0"))
        return (200, {}, json.dumps({"positions": pages[start]}))

    responses.add_callback(responses.GET, NETFLIX_URL, callback=callback)
    jobs = netflix.fetch({"type": "netflix"})

    assert len(responses.calls) == 3  # DEFAULT_PAGES, last page short
    assert len(jobs) == 24  # 0..23, the shared entry 9 counted once
    assert len({job.id for job in jobs}) == 24


RIVIAN_URL = "https://careers.rivian.com/api/jobs"


@responses.activate
def test_rivian_maps_jobs_and_falls_back_fields(fixture):
    responses.get(RIVIAN_URL, json=fixture("rivian_jobs.json"))
    jobs = rivian.fetch({"type": "rivian", "name": "canada", "location": "Canada"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "rivian:rivian:31222"
    assert job.title == "Staff Platform Engineer"
    assert job.company == "rivian"
    assert job.source == "rivian/canada"
    assert job.location == "Palo Alto, California; Vancouver, Canada"
    assert job.url == "https://careers.rivian.com/jobs/31222?lang=en-us"
    assert job.posted_at == "2026-05-27T21:45:00+00:00"
    assert "Internal Developer Platform" in job.description
    assert "<" not in job.description
    # null req_id falls back to slug; missing canonical_url falls back to
    # apply_url; null posted_date/description stay undated/empty (never-miss)
    assert jobs[1].id == "rivian:rivian:31500"
    assert jobs[1].url == "https://us-careers-rivian.icims.com/jobs/31500/login"
    assert jobs[1].posted_at is None
    assert jobs[1].description == ""

    request = responses.calls[0].request
    assert "location=Canada" in request.url
    assert len(responses.calls) == 1  # totalCount=2 fits in one page


@responses.activate
def test_rivian_stops_at_total_count():
    def posting(i):
        return {
            "data": {
                "slug": str(i),
                "req_id": str(i),
                "title": f"Engineer {i}",
                "full_location": "Vancouver, Canada",
                "posted_date": "2026-05-27T21:45:00+0000",
                "meta_data": {"canonical_url": f"https://careers.rivian.com/jobs/{i}"},
            }
        }

    pages = {
        1: [posting(i) for i in range(10)],
        2: [posting(i) for i in range(10, 13)],
    }

    def callback(request):
        page = int(dict(parse_qsl(urlsplit(request.url).query)).get("page", "1"))
        return (200, {}, json.dumps({"totalCount": 13, "jobs": pages[page]}))

    responses.add_callback(responses.GET, RIVIAN_URL, callback=callback)
    jobs = rivian.fetch({"type": "rivian"})

    assert len(responses.calls) == 2  # 13 postings fit in two pages of 10
    assert len(jobs) == 13


UBER_URL = "https://jobs.uber.com/api/jobs/search/"


@responses.activate
def test_uber_maps_jobs_and_falls_back_url(fixture):
    responses.get(UBER_URL, json=fixture("uber_jobs.json"))
    jobs = uber.fetch({"type": "uber", "name": "canada", "country": "Canada"})

    assert len(jobs) == 2
    job = jobs[0]
    assert job.id == "uber:uber:300286"
    assert job.title == "Specialist Account Executive, Ad Sales"
    assert job.company == "uber"
    assert job.source == "uber/canada"
    assert job.location == "Toronto, ON, Canada"
    assert job.url == "https://jobs.uber.com/en/jobs/300286/"
    assert job.posted_at == "2026-09-10T19:38:01Z"  # passed through as-is
    assert "Ad Sales" in job.description
    assert "<" not in job.description
    # missing Urls falls back to an id-derived path; null Description/
    # DisplayDate stay empty/undated (never-miss)
    assert jobs[1].url == "https://jobs.uber.com/en/jobs/301905/"
    assert jobs[1].description == ""
    assert jobs[1].posted_at is None

    request = responses.calls[0].request
    assert "countries=Canada" in request.url


@responses.activate
def test_uber_stops_at_total_pages():
    def posting(i):
        return {
            "Id": str(i),
            "Title": f"Engineer {i}",
            "Locations": [{"Address": "Toronto, ON, Canada"}],
            "Urls": [{"Url": f"/en/jobs/{i}/", "IsDefault": True}],
        }

    pages = {
        1: [posting(i) for i in range(10)],
        2: [posting(i) for i in range(10, 14)],
    }

    def callback(request):
        page = int(dict(parse_qsl(urlsplit(request.url).query)).get("page", "1"))
        return (200, {}, json.dumps({"totalPages": 2, "jobs": pages[page]}))

    responses.add_callback(responses.GET, UBER_URL, callback=callback)
    jobs = uber.fetch({"type": "uber"})

    assert len(responses.calls) == 2
    assert len(jobs) == 14

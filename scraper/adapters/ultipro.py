"""UKG Pro Recruiting (UltiPro) adapter.

Talks to the JSON endpoint behind every UltiPro-hosted job board:

    POST https://{host}/{tenant}/JobBoard/{board}/JobBoardView/LoadSearchResults

The board URL carries every value the adapter needs: for
https://recruiting.ultipro.ca/PAS5000PASON/JobBoard/736c1025-.../ the entry
is host: recruiting.ultipro.ca / tenant: PAS5000PASON / board: 736c1025-....
The endpoint is unauthenticated and, with the postedDateDesc OrderBy sent
here, returns newest-first - so the first pages always contain every posting
added since the last poll.

PostedDate is a real ISO-8601 timestamp (no relative-date reconstruction
needed), and BriefDescription is plain text.
"""

import requests

from scraper.models import Job

API_URL = "https://{host}/{tenant}/JobBoard/{board}/JobBoardView/LoadSearchResults"
JOB_URL = "https://{host}/{tenant}/JobBoard/{board}/OpportunityDetail?opportunityId={id}"
PAGE_SIZE = 50
DEFAULT_PAGES = 3  # 150 newest postings; UltiPro boards are typically small
TIMEOUT_SECONDS = 30
DESCRIPTION_LIMIT = 500


def fetch(config: dict) -> list[Job]:
    company = config["company"]
    host = config.get("host", "recruiting.ultipro.com")
    tenant = config["tenant"]
    board = config["board"]
    pages = int(config.get("pages", DEFAULT_PAGES))
    api_url = API_URL.format(host=host, tenant=tenant, board=board)

    jobs: list[Job] = []
    seen_ids: set[str] = set()  # the board can shift between page requests
    total: int | None = None
    for page in range(pages):
        response = requests.post(
            api_url,
            json={
                "opportunitySearch": {
                    "Top": PAGE_SIZE,
                    "Skip": page * PAGE_SIZE,
                    "QueryString": "",
                    "OrderBy": [
                        {
                            "Value": "postedDateDesc",
                            "PropertyName": "PostedDate",
                            "Ascending": False,
                        }
                    ],
                    "Filters": [],
                },
                "matchCriteria": {
                    "PreferredJobs": [],
                    "Educations": [],
                    "LicenseAndCertifications": [],
                    "Skills": [],
                    "hasNoLicenses": False,
                    "SkippedSkills": [],
                },
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        if total is None:
            total = data.get("totalCount") or 0
        opportunities = data.get("opportunities") or []
        for opportunity in opportunities:
            job = _to_job(opportunity, company, host, tenant, board)
            if job.id in seen_ids:
                continue
            seen_ids.add(job.id)
            jobs.append(job)
        if (page + 1) * PAGE_SIZE >= total or not opportunities:
            break
    return jobs


def _to_job(opportunity: dict, company: str, host: str, tenant: str, board: str) -> Job:
    opportunity_id = opportunity.get("Id") or ""
    # RequisitionNumber (e.g. "FIELD001983") is the stable human-facing id;
    # the opportunity GUID is the fallback for postings that lack one.
    req_id = opportunity.get("RequisitionNumber") or opportunity_id
    locations = "; ".join(
        text for loc in opportunity.get("Locations") or [] if (text := _location(loc))
    )
    return Job(
        id=f"ultipro:{company}:{req_id}",
        title=opportunity.get("Title", ""),
        company=company,
        location=locations,
        url=JOB_URL.format(host=host, tenant=tenant, board=board, id=opportunity_id)
        if opportunity_id
        else "",
        posted_at=opportunity.get("PostedDate"),
        description=_description(opportunity.get("BriefDescription") or ""),
        source=f"ultipro/{company}",
    )


def _location(location: dict) -> str:
    # LocalizedName is usually set ("Calgary", "Remote Alberta") but some
    # postings leave it null and carry only the structured Address.
    if name := location.get("LocalizedName"):
        return name
    address = location.get("Address") or {}
    parts = (
        address.get("City"),
        (address.get("State") or {}).get("Code"),
        (address.get("Country") or {}).get("Code"),
    )
    return ", ".join(part for part in parts if part)


def _description(text: str) -> str:
    return " ".join(text.split())[:DESCRIPTION_LIMIT]

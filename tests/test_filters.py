from scraper.filters import build_predicates, keep
from scraper.models import Job


def make_job(title: str = "Software Engineer", location: str = "Remote (Canada)") -> Job:
    return Job(
        id="x:y:1",
        title=title,
        company="Acme",
        location=location,
        url="https://example.com",
        posted_at=None,
        description="",
        source="x/y",
    )


def test_empty_config_builds_no_predicates_and_keeps_everything():
    predicates = build_predicates({})
    assert predicates == []
    assert keep(make_job(), predicates)


def test_include_keywords_match_title_case_insensitively():
    predicates = build_predicates({"include_keywords": ["ENGINEER"]})
    assert keep(make_job("Senior Software Engineer"), predicates)
    assert not keep(make_job("Account Executive"), predicates)


def test_exclude_keywords_drop_matching_titles():
    predicates = build_predicates({"exclude_keywords": ["staff"]})
    assert not keep(make_job("Staff Engineer"), predicates)
    assert keep(make_job("Software Engineer"), predicates)


def test_locations_match_location_field():
    predicates = build_predicates({"locations": ["remote", "toronto"]})
    assert keep(make_job(location="Remote (Canada)"), predicates)
    assert keep(make_job(location="Toronto, ON"), predicates)
    assert not keep(make_job(location="London, England"), predicates)


def test_rules_compose_and_do_not_leak_into_each_other():
    predicates = build_predicates(
        {"include_keywords": ["engineer"], "exclude_keywords": ["staff"]}
    )
    assert keep(make_job("Software Engineer"), predicates)
    assert not keep(make_job("Staff Engineer"), predicates)  # excluded wins
    assert not keep(make_job("Product Designer"), predicates)  # not included
    # regression: include must not accidentally use the exclude word list
    assert not keep(make_job("Staff Accountant"), predicates)

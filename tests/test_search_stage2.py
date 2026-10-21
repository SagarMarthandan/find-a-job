from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import HTTPException

from find_a_job import api, job_sources
from find_a_job.job_identity import normalize_job_url
from find_a_job.search import normalize_assessments, validate_config

SOURCE_TEXT = "Ada Example built Atlas Ledger in Python. Ada Example led a small data team."
EVIDENCE = {"id": "e1", "text": "Ada Example built Atlas Ledger in Python.", "source": "source-1"}
PROFILE = {
    "name": "Ada Example",
    "skills": ["Python"],
    "experience": [],
    "projects": ["Atlas Ledger"],
    "education": [],
    "evidence": [EVIDENCE],
}
DETAIL_URL = "https://www.arbeitnow.com/jobs/companies/acme/python-developer-123"
FETCHED_AT = "2026-10-21T12:00:00+00:00"
DETAIL_TEXT = "\n".join(
    [
        "Python Developer",
        "Acme GmbH",
        "Berlin, Germany",
        "Posted 2 days ago",
        "Requirements: 3+ years of experience. German B1 required.",
        "Remote within Europe.",
    ]
)


def _page(**overrides):
    page = {
        "id": "arbeitnow-detail-1",
        "source_id": "arbeitnow",
        "kind": "detail",
        "url": DETAIL_URL,
        "title": "Python Developer",
        "text": DETAIL_TEXT,
        "fetched_at": FETCHED_AT,
        "links": [],
        "artifacts": [],
    }
    page.update(overrides)
    return page


def _assessment(**overrides):
    job = {
        "source_id": "arbeitnow",
        "url": DETAIL_URL,
        "title": "Python Developer",
        "company": "Acme GmbH",
        "location": "Berlin, Germany",
        "matched_role_id": "role-1",
        "required_experience_years": 3,
        "seniority": None,
        "work_mode": "remote",
        "remote_regions": ["Europe"],
        "posted_at": "2026-10-19T00:00:00+00:00",
        "employment_type": None,
        "languages": [{"language": "German", "level": "B1"}],
        "evidence": [
            {"field": "title", "quote": "Python Developer", "page_id": "arbeitnow-detail-1"},
            {"field": "company", "quote": "Acme GmbH", "page_id": "arbeitnow-detail-1"},
            {"field": "location", "quote": "Berlin, Germany", "page_id": "arbeitnow-detail-1"},
            {"field": "required_experience_years", "quote": "3+ years of experience", "page_id": "arbeitnow-detail-1"},
            {"field": "work_mode", "quote": "Remote within Europe", "page_id": "arbeitnow-detail-1"},
            {"field": "remote_regions", "quote": "Remote within Europe", "page_id": "arbeitnow-detail-1"},
            {"field": "posted_at", "quote": "Posted 2 days ago", "page_id": "arbeitnow-detail-1"},
            {"field": "languages", "quote": "German B1 required", "page_id": "arbeitnow-detail-1"},
        ],
        "notes": [],
    }
    job.update(overrides)
    return job


def _with_evidence(job: dict, field: str, quote: str) -> dict:
    return {**job, "evidence": [{**item, "quote": quote} if item["field"] == field else item for item in job["evidence"]]}


def _located(location: str, **overrides) -> dict:
    return _with_evidence(_assessment(location=location, **overrides), "location", location)


def _posted(posted_at: str, quote: str, **overrides) -> dict:
    return _with_evidence(_assessment(posted_at=posted_at, **overrides), "posted_at", quote)


def _experienced(years: float, quote: str, **overrides) -> dict:
    return _with_evidence(_assessment(required_experience_years=years, **overrides), "required_experience_years", quote)


def _config(**overrides):
    config = {
        "roles": [{"id": "role-1", "title": "Python Developer", "search_terms": ["python developer"], "custom": False}],
        "excluded_titles": [],
        "max_required_experience_years": 5,
        "seniority": [],
        "countries": ["Germany"],
        "cities": ["Berlin"],
        "radius_km": None,
        "work_modes": ["remote"],
        "remote_regions": [],
        "posted_within_days": 7,
        "posted_after": None,
        "posted_before": None,
        "employment_types": [],
        "languages": [{"language": "German", "level": "B2"}],
        "sources": ["arbeitnow"],
        "unknown_policy": "review",
        "exclude_seen": False,
        "exclude_applied": False,
        "ats_board_urls": [],
        "max_results_per_source": 10,
    }
    config.update(overrides)
    return config


class Stage2TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        data_dir = Path(self.temporary_directory.name)
        self.paths = patch.multiple(api, DATA_DIR=data_dir, TOKEN_FILE=data_dir / "session-token", DB_FILE=data_dir / "tasks.sqlite3")
        self.paths.start()
        self.addCleanup(self.paths.stop)
        api._initialize()

    def approved_intake(self) -> str:
        source = {"id": "source-1", "kind": "resume", "label": "synthetic.md", "format": "md", "status": "ready", "text": SOURCE_TEXT}
        task = api._create_intake("Synthetic intake", [source])
        result = api._validate_intake_result(PROFILE, [source])
        with api._db() as conn:
            conn.execute("UPDATE tasks SET status='completed',result_json=? WHERE id=?", (json.dumps(result), task["id"]))
        api.approve_intake(task["id"], {}, None)
        return task["id"]

    def complete_roles(self, intake_id: str, model: str = "synthetic-model", refresh: bool = False) -> tuple[str, dict]:
        task = api.suggest_roles(intake_id, {"refresh": refresh}, None)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model=model), None)["task"]
        self.assertEqual(claimed["id"], task["id"])
        self.assertIn("profile", claimed["input"])
        self.assertEqual(claimed["output_schema"]["roles"][0]["id"], "unique string")
        result = {
            "roles": [{
                "id": "role-1",
                "title": "Python Developer",
                "search_terms": ["python developer", "backend engineer"],
                "evidence_ids": ["e1"],
                "rationale": "Evidence names a Python project.",
                "gaps": ["No professional backend title stated."],
            }]
        }
        saved = api.complete(task["id"], api.CompleteRequest(worker_id="worker", result=result), None)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["model"], model)
        return task["id"], saved["result"]

    def save_config(self, intake_id: str, roles_task_id: str, **overrides) -> dict:
        body = {"roles_task_id": roles_task_id, **_config(**overrides)}
        return api.put_search(intake_id, body, None)["config"]

    def create_search_task(self, intake_id: str, config: dict) -> str:
        task = api.run_search(intake_id, {}, None)
        acquisition = {
            "fetched_at": FETCHED_AT,
            "source_results": [{
                "source_id": "arbeitnow", "status": "ok", "listing_urls": ["https://www.arbeitnow.com/?search=python"],
                "discovered_count": 1, "captured_count": 1, "error": None, "unsupported_filters": [],
            }],
            "pages": [_page()],
            "artifacts": [],
        }
        with api._db() as conn:
            row = conn.execute("SELECT input_json FROM tasks WHERE id=?", (task["id"],)).fetchone()
            task_input = json.loads(row["input_json"])
            task_input["acquisition"] = acquisition
            conn.execute("UPDATE tasks SET status='waiting_for_harness',input_json=? WHERE id=?", (json.dumps(task_input), task["id"]))
        return task["id"]


class RoleSuggestionTests(Stage2TestCase):
    def test_role_claim_prompt_completion_and_model_scoped_cache(self) -> None:
        intake_id = self.approved_intake()
        first_id, first_result = self.complete_roles(intake_id)

        second = api.suggest_roles(intake_id, {"refresh": False}, None)
        self.assertEqual(api.claim(api.ClaimRequest(worker_id="worker", model="synthetic-model"), None), {"task": None})
        with api._db() as conn:
            row = conn.execute("SELECT status,result_json FROM tasks WHERE id=?", (second["id"],)).fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(json.loads(row["result_json"]), first_result)

        refreshed = api.suggest_roles(intake_id, {"refresh": True}, None)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="synthetic-model"), None)["task"]
        self.assertEqual(claimed["id"], refreshed["id"])
        api.fail(refreshed["id"], api.FailRequest(worker_id="worker", error="synthetic stop"), None)

        other_model = api.suggest_roles(intake_id, {"refresh": False}, None)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="other-model"), None)["task"]
        self.assertEqual(claimed["id"], other_model["id"])
        api.fail(other_model["id"], api.FailRequest(worker_id="worker", error="synthetic stop"), None)

    def test_role_prompts_and_results_are_isolated_between_profiles(self) -> None:
        first = self.approved_intake()
        text = "Grace Lin built Nimbus in Go. Grace Lin maintained a synthetic service."
        source = {"id": "source-2", "kind": "resume", "label": "grace.md", "format": "md", "status": "ready", "text": text}
        task = api._create_intake("Second synthetic intake", [source])
        profile = {
            "name": "Grace Lin", "skills": ["Go"], "experience": ["Grace Lin built Nimbus in Go."],
            "projects": ["Nimbus"], "education": [],
            "evidence": [{"id": "g1", "text": "Grace Lin built Nimbus in Go.", "source": "source-2"}],
        }
        result = api._validate_intake_result(profile, [source])
        with api._db() as conn:
            conn.execute("UPDATE tasks SET status='completed',result_json=? WHERE id=?", (json.dumps(result), task["id"]))
        api.approve_intake(task["id"], {}, None)

        first_task = api.suggest_roles(first, {"refresh": False}, None)
        second_task = api.suggest_roles(task["id"], {"refresh": False}, None)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="shared-model"), None)["task"]
        self.assertEqual(claimed["id"], first_task["id"])
        self.assertIn("Ada Example", claimed["prompt"])
        api.complete(first_task["id"], api.CompleteRequest(worker_id="worker", result={"roles": [{
            "id": "ada-role", "title": "Python Developer", "search_terms": ["python"],
            "evidence_ids": ["e1"], "rationale": "Uses Ada evidence.", "gaps": [],
        }]}), None)

        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="shared-model"), None)["task"]
        self.assertEqual(claimed["id"], second_task["id"])
        self.assertIn("Grace Lin", claimed["prompt"])
        self.assertNotIn("Ada Example", claimed["prompt"])
        api.complete(second_task["id"], api.CompleteRequest(worker_id="worker", result={"roles": [{
            "id": "grace-role", "title": "Go Developer", "search_terms": ["go"],
            "evidence_ids": ["g1"], "rationale": "Uses Grace evidence.", "gaps": [],
        }]}), None)
        self.assertEqual(api.get_search(first, None)["roles_task"]["id"], first_task["id"])
        self.assertEqual(api.get_search(task["id"], None)["roles_task"]["id"], second_task["id"])

    def test_role_results_are_stale_after_profile_revision_changes(self) -> None:
        intake_id = self.approved_intake()
        self.complete_roles(intake_id)
        source = {"id": "source-1", "kind": "resume", "label": "synthetic.md", "status": "ready", "text": SOURCE_TEXT}
        api.review_intake(intake_id, {"result": PROFILE}, None)
        api.approve_intake(intake_id, {}, None)
        response = api.get_search(intake_id, None)
        self.assertTrue(response["stale"])
        self.assertEqual(response["roles_task"]["status"], "completed")
        with self.assertRaises(HTTPException) as raised:
            api.put_search(intake_id, {"roles_task_id": response["roles_task"]["id"], **_config()}, None)
        self.assertEqual(raised.exception.status_code, 409)


class ConfigTests(Stage2TestCase):
    def test_config_rejects_unknown_sources_and_unknown_fields(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        with self.assertRaises(HTTPException) as raised:
            self.save_config(intake_id, roles_id, sources=["not-a-source"])
        self.assertEqual(raised.exception.status_code, 422)
        with self.assertRaises(HTTPException) as raised:
            api.put_search(intake_id, {"roles_task_id": roles_id, "unexpected": 1, **_config()}, None)
        self.assertEqual(raised.exception.status_code, 422)

    def test_config_defaults_and_radius_requirement(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        config = api.put_search(intake_id, {
            "roles_task_id": roles_id,
            "roles": [{"id": "role-1", "title": "Python Developer"}],
            "sources": ["arbeitnow"],
        }, None)["config"]
        self.assertIsNone(config["max_required_experience_years"])
        self.assertIsNone(config["posted_within_days"])
        self.assertEqual(config["unknown_policy"], "review")
        self.assertEqual(config["max_results_per_source"], 10)
        with self.assertRaises(HTTPException) as raised:
            api.put_search(intake_id, {
                "roles_task_id": roles_id,
                "roles": [{"id": "role-1", "title": "Python Developer"}],
                "sources": ["arbeitnow"],
                "radius_km": 25,
                "cities": [],
            }, None)
        self.assertEqual(raised.exception.status_code, 422)

    def test_suggested_role_title_edit_is_saved_and_used_in_source_urls(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        edited = [{"id": "role-1", "title": "Platform Engineer", "search_terms": ["python", "backend"], "custom": False}]
        config = self.save_config(intake_id, roles_id, roles=edited)
        self.assertEqual(config["roles"][0]["title"], "Platform Engineer")
        self.assertIn("Platform Engineer", job_sources._all_terms(config))
        urls = job_sources._listing_urls("arbeitnow", config)
        self.assertTrue(any("Platform+Engineer" in url for url in urls))
        self.assertTrue(any("search=python" in url for url in urls))
        with self.assertRaises(HTTPException) as raised:
            self.save_config(intake_id, roles_id, roles=[{"id": "not-suggested", "title": "Unlinked", "search_terms": ["x"], "custom": False}])
        self.assertEqual(raised.exception.status_code, 422)

    def test_config_rejects_non_finite_numbers(self) -> None:
        role_task = {"id": "roles", "result": {"roles": [{"id": "role-1", "title": "Python Developer", "search_terms": ["python"]}]}}
        for value in (float("inf"), float("-inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                validate_config({"roles": [{"id": "role-1", "title": "Python Developer"}], "sources": ["arbeitnow"], "max_required_experience_years": value}, "intake", "revision", role_task, {"arbeitnow"}, "config", 1)

    def test_validate_config_accepts_unspecified_defaults(self) -> None:
        role_task = {"id": "roles", "result": {"roles": [{"id": "role-1", "title": "Python Developer", "search_terms": ["python"]}]}}
        value = {"roles": [{"id": "role-1", "title": "Python Developer"}], "sources": ["arbeitnow"]}
        config = validate_config(value, "intake", "revision", role_task, {"arbeitnow"}, "config", 1)
        self.assertIsNone(config["posted_within_days"])
        self.assertEqual(config["seniority"], [])
        self.assertEqual(config["roles"][0]["search_terms"], ["python"])


class AssessmentTests(Stage2TestCase):
    def assess(self, config: dict, job: dict | None = None, page: dict | None = None, fetched_at: str = FETCHED_AT) -> dict:
        jobs = normalize_assessments({"jobs": [job or _assessment()]}, [page or _page()], config, set(), set(), fetched_at)
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    def test_experience_location_freshness_and_language_change_decisions(self) -> None:
        base = _config()
        self.assertEqual(self.assess(base)["decision"], "match")
        self.assertEqual(self.assess({**base, "max_required_experience_years": 2})["decision"], "excluded")
        self.assertEqual(self.assess({**base, "countries": ["France"], "cities": ["Paris"]}, job=_assessment(work_mode=None))["decision"], "excluded")
        self.assertEqual(self.assess({**base, "posted_within_days": 1})["decision"], "excluded")
        self.assertEqual(self.assess({**base, "languages": [{"language": "German", "level": "A2"}]})["decision"], "excluded")

    def test_geography_requires_city_and_exact_city_names(self) -> None:
        base = {**_config(), "work_modes": []}
        berlin_page = _page()
        berlin = _located("Berlin, Germany", work_mode=None)
        self.assertEqual(self.assess(base, berlin, berlin_page)["decision"], "match")
        paris = _located("Paris, Germany", work_mode=None)
        paris_page = _page(text=DETAIL_TEXT + "\nParis, Germany")
        self.assertEqual(self.assess(base, paris, paris_page)["decision"], "excluded")
        paris_config = {**base, "cities": ["Paris"]}
        self.assertEqual(self.assess(paris_config, paris, paris_page)["decision"], "match")
        self.assertEqual(self.assess(paris_config, berlin, berlin_page)["decision"], "excluded")
        self.assertEqual(self.assess({**base, "countries": ["Germany"], "cities": ["Berlin"]}, _located("Berlin", work_mode=None), _page(text=DETAIL_TEXT + "\nBerlin"))["decision"], "review")
        york_config = {**base, "countries": ["United States"], "cities": ["York"]}
        york = _located("New York, USA", work_mode=None)
        self.assertEqual(self.assess(york_config, york, _page(text=DETAIL_TEXT + "\nNew York, USA"))["decision"], "excluded")
        kiel_config = {**base, "cities": ["Kiel"]}
        kielce = _located("Kielce, Germany", work_mode=None)
        self.assertEqual(self.assess(kiel_config, kielce, _page(text=DETAIL_TEXT + "\nKielce, Germany"))["decision"], "excluded")

    def test_remote_worldwide_or_europe_without_country_is_unknown(self) -> None:
        remote = _assessment(location="Remote", work_mode="remote", remote_regions=["Europe"])
        page = _page(text=DETAIL_TEXT + "\nRemote\nEurope")
        job = {**remote, "evidence": [*_with_evidence(remote, "location", "Remote")["evidence"], {"field": "remote_regions", "quote": "Europe", "page_id": "arbeitnow-detail-1"}]}
        review = self.assess(_config(), job, page)
        self.assertEqual(review["decision"], "review")
        self.assertTrue(any("country" in reason for reason in review["review_reasons"]))
        excluded = self.assess({**_config(), "unknown_policy": "exclude"}, job, page)
        self.assertEqual(excluded["decision"], "excluded")
        no_mode = {**job, "work_mode": None}
        outside = self.assess({**_config(), "remote_regions": ["Germany"]}, no_mode, page)
        self.assertEqual(outside["decision"], "excluded")

    def test_core_field_values_must_appear_in_their_quote(self) -> None:
        job = _with_evidence(_assessment(location="Berlin", work_mode=None), "location", "Paris")
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [job]}, [_page(text=DETAIL_TEXT + "\nParis")], _config(), set(), set(), FETCHED_AT)
        normalized = _with_evidence(_assessment(title="Python   Developer"), "title", "python developer")
        self.assertEqual(self.assess(_config(), normalized)["decision"], "match")

    def test_experience_quote_anchors_minimum_and_ignores_optional_text(self) -> None:
        range_page = _page(text=DETAIL_TEXT + "\nRequirements: 3-5 years of experience.")
        self.assertEqual(self.assess(_config(), _experienced(3, "3-5 years of experience"), range_page)["decision"], "match")
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [_experienced(5, "3-5 years of experience")]}, [range_page], _config(), set(), set(), FETCHED_AT)
        optional_page = _page(text=DETAIL_TEXT + "\n3 years of experience is nice to have.")
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [_experienced(3, "3 years of experience is nice to have")]}, [optional_page], _config(), set(), set(), FETCHED_AT)
        required_page = _page(text=DETAIL_TEXT + "\n3 years of experience required.")
        self.assertEqual(self.assess(_config(), _experienced(3, "3 years of experience required"), required_page)["decision"], "match")
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [_experienced(3, "Project timeline: 3 years.")]}, [_page(text=DETAIL_TEXT + "\nProject timeline: 3 years.")], _config(), set(), set(), FETCHED_AT)

    def test_zero_experience_filter_is_enforced(self) -> None:
        zero = _experienced(0, "No experience required")
        self.assertEqual(self.assess({**_config(), "max_required_experience_years": 0}, zero, _page(text=DETAIL_TEXT + "\nNo experience required"))["decision"], "match")
        one = _experienced(1, "1 year of experience required")
        self.assertEqual(self.assess({**_config(), "max_required_experience_years": 0}, one, _page(text=DETAIL_TEXT + "\n1 year of experience required"))["decision"], "excluded")

    def test_posting_timestamp_does_not_override_quote_precision(self) -> None:
        page = _page(text=DETAIL_TEXT + "\nPosted 2 days ago")
        early = _posted("2026-10-19T00:00:00+00:00", "Posted 2 days ago")
        late = _posted("2026-10-19T23:59:59+00:00", "Posted 2 days ago")
        self.assertEqual(self.assess(_config(), early, page)["decision"], "match")
        self.assertEqual(self.assess(_config(), late, page)["decision"], "match")
        day_config = {**_config(), "posted_within_days": 1}
        self.assertEqual(self.assess(day_config, early, page)["decision"], "excluded")
        self.assertEqual(self.assess(day_config, late, page)["decision"], "excluded")

    def test_posting_boundaries_relative_precision_and_future_dates(self) -> None:
        today_page = _page(text=DETAIL_TEXT + "\nPosted today")
        today = _posted("2026-10-21T23:00:00+00:00", "Posted today")
        self.assertEqual(self.assess({**_config(), "posted_within_days": 1}, today, today_page)["decision"], "match")
        yesterday_page = _page(text=DETAIL_TEXT + "\nPosted yesterday")
        for stamp in ("2026-10-20T00:00:00+00:00", "2026-10-20T23:59:59+00:00"):
            self.assertEqual(self.assess({**_config(), "posted_within_days": 1}, _posted(stamp, "Posted yesterday"), yesterday_page)["decision"], "review")
        hours = _posted("2026-10-21T10:00:00+00:00", "Posted 2 hours ago")
        self.assertEqual(self.assess({**_config(), "posted_within_days": 1}, hours, _page(text=DETAIL_TEXT + "\nPosted 2 hours ago"))["decision"], "match")
        old_hours = _posted("2026-10-20T11:00:00+00:00", "Posted 25 hours ago")
        self.assertEqual(self.assess({**_config(), "posted_within_days": 1}, old_hours, _page(text=DETAIL_TEXT + "\nPosted 25 hours ago"))["decision"], "excluded")
        future_page = _page(text=DETAIL_TEXT + "\nPosted 2026-10-22")
        future = _posted("2026-10-22T00:00:00+00:00", "Posted 2026-10-22")
        self.assertEqual(self.assess(_config(), future, future_page)["decision"], "excluded")

    def test_posting_timezone_offsets_anchor_to_utc_observation(self) -> None:
        fetched = "2026-10-21T00:30:00+02:00"
        today = _posted("2026-10-20T12:00:00+00:00", "Posted today")
        self.assertEqual(self.assess({**_config(), "posted_within_days": 1}, today, _page(text=DETAIL_TEXT + "\nPosted today"), fetched)["decision"], "match")
        yesterday = _posted("2026-10-19T12:00:00+00:00", "Posted yesterday")
        self.assertEqual(self.assess({**_config(), "posted_within_days": 1}, yesterday, _page(text=DETAIL_TEXT + "\nPosted yesterday"), fetched)["decision"], "review")

    def test_unknown_remote_geography_and_policy_are_tri_state(self) -> None:
        remote_world = _assessment(location="Remote", remote_regions=["Worldwide"])
        page = _page(text=DETAIL_TEXT + "\nLocation: Remote\nWorldwide")
        job = {**remote_world, "evidence": [*remote_world["evidence"], {"field": "location", "quote": "Location: Remote", "page_id": "arbeitnow-detail-1"}, {"field": "remote_regions", "quote": "Worldwide", "page_id": "arbeitnow-detail-1"}]}
        review = normalize_assessments({"jobs": [job]}, [page], _config(), set(), set(), FETCHED_AT)[0]
        self.assertEqual(review["decision"], "review")
        excluded = normalize_assessments({"jobs": [job]}, [page], _config(unknown_policy="exclude"), set(), set(), FETCHED_AT)[0]
        self.assertEqual(excluded["decision"], "excluded")

    def test_numeric_date_and_language_claims_require_parseable_quotes(self) -> None:
        bad = _assessment(required_experience_years=4)
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [bad]}, [_page()], _config(), set(), set(), FETCHED_AT)
        bad_date = _assessment(posted_at="2026-10-20T00:00:00+00:00")
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [bad_date]}, [_page()], _config(), set(), set(), FETCHED_AT)
        bad_language = _assessment(languages=[{"language": "German", "level": "C1"}])
        with self.assertRaises(ValueError):
            normalize_assessments({"jobs": [bad_language]}, [_page()], _config(), set(), set(), FETCHED_AT)
        for value in (float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                normalize_assessments({"jobs": [_assessment(required_experience_years=value)]}, [_page()], _config(), set(), set(), FETCHED_AT)

    def test_duplicate_urls_merge_roles_and_evidence(self) -> None:
        second = _assessment(matched_role_id="role-2", url=DETAIL_URL + "?utm_source=test")
        page_two = _page(id="arbeitnow-detail-2", url=DETAIL_URL + "?utm_source=test")
        second["evidence"] = [{**item, "page_id": "arbeitnow-detail-2"} for item in second["evidence"]]
        jobs = normalize_assessments({"jobs": [_assessment(), second]}, [_page(), page_two], _config(roles=[
            {"id": "role-1", "title": "Python Developer", "search_terms": ["python"], "custom": False},
            {"id": "role-2", "title": "Backend Engineer", "search_terms": ["backend"], "custom": False},
        ]), set(), set(), FETCHED_AT)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["matched_role_ids"], ["role-1", "role-2"])
        self.assertEqual(jobs[0]["source_ids"], ["arbeitnow"])
        self.assertEqual(normalize_job_url(jobs[0]["url"]), normalize_job_url(DETAIL_URL))


class SearchLifecycleTests(Stage2TestCase):
    def test_search_claim_completion_and_history(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        config = self.save_config(intake_id, roles_id)
        task_id = self.create_search_task(intake_id, config)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="synthetic-model"), None)["task"]
        self.assertEqual(claimed["id"], task_id)
        self.assertEqual(claimed["input"]["config"]["revision"], config["revision"])
        self.assertIn("acquisition", claimed["input"])
        saved = api.complete(task_id, api.CompleteRequest(worker_id="worker", result={"jobs": [_assessment()]}), None)
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["result"]["jobs"][0]["decision"], "match")
        self.assertEqual(saved["result"]["fetched_at"], FETCHED_AT)

        history = api.intake_history(intake_id, None)
        self.assertIn(DETAIL_URL, history["seen_urls"])

        second_config = self.save_config(intake_id, roles_id, exclude_seen=True)
        second_id = self.create_search_task(intake_id, second_config)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="synthetic-model"), None)["task"]
        self.assertEqual(claimed["id"], second_id)
        saved = api.complete(second_id, api.CompleteRequest(worker_id="worker", result={"jobs": [_assessment()]}), None)
        self.assertEqual(saved["result"]["jobs"][0]["decision"], "excluded")
        self.assertIn("Previously seen", saved["result"]["jobs"][0]["exclusion_reasons"][0])

    def test_partial_source_failure_and_cancelled_search_safety(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        config = self.save_config(intake_id, roles_id)
        task_id = self.create_search_task(intake_id, config)
        with api._db() as conn:
            row = conn.execute("SELECT input_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            task_input = json.loads(row["input_json"])
            task_input["acquisition"]["source_results"].append({
                "source_id": "linkedin", "status": "blocked", "listing_urls": ["https://www.linkedin.com/jobs/search/?keywords=python"],
                "discovered_count": 0, "captured_count": 0, "error": "HTTP 429", "unsupported_filters": [],
            })
            conn.execute("UPDATE tasks SET input_json=? WHERE id=?", (json.dumps(task_input), task_id))
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="synthetic-model"), None)["task"]
        self.assertEqual(claimed["id"], task_id)
        api.cancel_task(task_id, None)
        with self.assertRaises(HTTPException) as raised:
            api.complete(task_id, api.CompleteRequest(worker_id="worker", result={"jobs": [_assessment()]}), None)
        self.assertEqual(raised.exception.status_code, 409)
        with api._db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0], "cancelled")

    def test_stale_search_completion_is_rejected(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        config = self.save_config(intake_id, roles_id)
        task_id = self.create_search_task(intake_id, config)
        claimed = api.claim(api.ClaimRequest(worker_id="worker", model="synthetic-model"), None)["task"]
        self.assertEqual(claimed["id"], task_id)
        api.review_intake(intake_id, {"result": PROFILE}, None)
        api.approve_intake(intake_id, {}, None)
        with self.assertRaises(HTTPException) as raised:
            api.complete(task_id, api.CompleteRequest(worker_id="worker", result={"jobs": [_assessment()]}), None)
        self.assertEqual(raised.exception.status_code, 409)
        with api._db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0], "failed")

    def test_history_and_seen_state_are_isolated_by_intake(self) -> None:
        first = self.approved_intake()
        second = self.approved_intake()
        stamp = api.now()
        with api._db() as conn:
            conn.execute(
                "INSERT INTO tasks(id,kind,status,title,input_json,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ("first-search", "search", "completed", "First search", json.dumps({"intake_id": first}),
                 json.dumps({"jobs": [{"url": DETAIL_URL}]}), stamp, stamp),
            )
        api.application_status(first, {"url": DETAIL_URL, "status": "applied"}, None)
        first_history = api.intake_history(first, None)
        second_history = api.intake_history(second, None)
        self.assertIn(DETAIL_URL, first_history["seen_urls"])
        self.assertIn(DETAIL_URL, first_history["applied_urls"])
        self.assertEqual(second_history, {"applied_urls": [], "seen_urls": []})

    def test_all_sources_blocked_marks_search_failed(self) -> None:
        intake_id = self.approved_intake()
        roles_id, _ = self.complete_roles(intake_id)
        config = self.save_config(intake_id, roles_id)
        task = api.run_search(intake_id, {}, None)
        acquisition = {
            "fetched_at": FETCHED_AT,
            "source_results": [{
                "source_id": "arbeitnow", "status": "blocked", "listing_urls": ["https://www.arbeitnow.com/?search=python"],
                "discovered_count": 0, "captured_count": 0, "error": "access challenge", "unsupported_filters": [],
            }],
            "pages": [],
            "artifacts": [],
        }
        with patch("find_a_job.job_sources.run_search", return_value=acquisition):
            api._execute_search(task["id"], object())
        with api._db() as conn:
            row = conn.execute("SELECT status,error FROM tasks WHERE id=?", (task["id"],)).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("All selected sources failed", row["error"])


class SourceUrlTests(unittest.TestCase):
    def test_host_allowlists_reject_impostor_domains(self) -> None:
        self.assertFalse(job_sources._source_detail_url("indeed", "https://indeed.com.attacker.com/viewjob?jk=abc"))
        self.assertFalse(job_sources._source_detail_url("indeed", "https://de.indeed.com.attacker.com/viewjob?jk=abc"))
        self.assertTrue(job_sources._source_detail_url("indeed", "https://de.indeed.com/viewjob?jk=abc"))
        cases = [
            ("arbeitnow", "https://www.arbeitnow.com/jobs/companies/acme/python-123", "https://arbeitnow.com.attacker.com/jobs/companies/acme/python-123"),
            ("xing", "https://www.xing.com/jobs/python-123", "https://xing.com.attacker.com/jobs/python-123"),
            ("stepstone", "https://www.stepstone.de/stellenangebote--Python-123-inline.html", "https://stepstone.de.attacker.com/stellenangebote--Python-123-inline.html"),
            ("wellfound", "https://wellfound.com/jobs/123-python", "https://wellfound.com.attacker.com/jobs/123-python"),
            ("eu_remote", "https://euremotejobs.com/job/python-engineer/", "https://euremotejobs.com.attacker.com/job/python-engineer/"),
        ]
        for source_id, allowed, impostor in cases:
            self.assertTrue(job_sources._source_detail_url(source_id, allowed), source_id)
            self.assertFalse(job_sources._source_detail_url(source_id, impostor), source_id)

    def test_indeed_uses_each_selected_country_domain(self) -> None:
        config = _config(countries=["Germany", "France"], cities=[], posted_within_days=7)
        urls, warnings = job_sources._indeed_listing_urls(config)
        hosts = {urlsplit(url).hostname for url in urls}
        self.assertEqual(hosts, {"de.indeed.com", "fr.indeed.com"})
        self.assertFalse(warnings)
        for url in urls:
            host = urlsplit(url).hostname
            self.assertIn("fromage=7", url)
            if host == "de.indeed.com":
                self.assertIn("l=Germany", url)
            else:
                self.assertIn("l=France", url)
        unsupported, warnings = job_sources._indeed_listing_urls(_config(countries=["Atlantis"], cities=[]))
        self.assertEqual(unsupported, [])
        self.assertTrue(any("Atlantis" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()

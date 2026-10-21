from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .job_identity import normalize_job_url

SENIORITY = {"internship", "working_student", "junior", "mid", "senior", "lead"}
WORK_MODES = {"onsite", "hybrid", "remote"}
EMPLOYMENT = {"permanent", "contract", "part_time", "internship", "working_student"}
LEVEL_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")
LEVELS = set(LEVEL_ORDER) | {"any"}
_CONFIG_FIELDS = {
    "roles_task_id", "roles", "excluded_titles", "max_required_experience_years",
    "seniority", "countries", "cities", "radius_km", "work_modes", "remote_regions",
    "posted_within_days", "posted_after", "posted_before", "employment_types",
    "languages", "sources", "unknown_policy", "exclude_seen", "exclude_applied",
    "ats_board_urls", "max_results_per_source", "id", "revision", "intake_id",
    "profile_revision",
}
_EVIDENCE_FIELDS = {
    "title", "company", "location", "required_experience_years", "seniority",
    "work_mode", "remote_regions", "posted_at", "employment_type", "languages",
}
_CORE_FIELDS = ("title", "company", "location")
_REQUIREMENT_FIELDS = tuple(sorted(_EVIDENCE_FIELDS - set(_CORE_FIELDS)))
_YEARS_REQUIRED_CUES = (
    "experience", "erfahrung", "required", "requirement", "must", "minimum",
    "mindestens", "at least", "voraussetzung", "erforderlich",
)
_YEARS_OPTIONAL_CUES = (
    "nice to have", "optional", "wünschenswert", "von vorteil", "preferred", "desirable",
)
_SENIORITY_TOKENS = {
    "internship": ("intern", "praktik"),
    "working_student": ("working student", "werkstudent"),
    "junior": ("junior", "entry level", "einstieg"),
    "mid": ("mid-level", "mid level", "intermediate", "mid"),
    "senior": ("senior", "erfahren"),
    "lead": ("lead", "principal", "head of"),
}
_WORK_MODE_TOKENS = {
    "onsite": ("on-site", "onsite", "on site", "vor ort", "office"),
    "hybrid": ("hybrid",),
    "remote": ("remote", "home office", "homeoffice"),
}
_EMPLOYMENT_TOKENS = {
    "permanent": ("permanent", "unbefristet", "full-time", "full time", "vollzeit"),
    "contract": ("contract", "fixed term", "befristet", "freelance"),
    "part_time": ("part-time", "part time", "teilzeit"),
    "internship": ("internship", "intern", "praktikum"),
    "working_student": ("working student", "werkstudent"),
}
_RELATIVE_DAYS = re.compile(r"(?:vor\s+)?(\d+)\s*(?:days?|tage?n?)\s*(?:ago|her)?", re.IGNORECASE)
_RELATIVE_HOURS = re.compile(r"(?:vor\s+)?(\d+)\s*(?:hours?|stunden?)\s*(?:ago|her)?", re.IGNORECASE)
_RELATIVE_WEEKS = re.compile(r"(?:vor\s+)?(\d+)\s*(?:weeks?|wochen?)\s*(?:ago|her)?", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")
_RANGE_YEARS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:-|–|to)\s*(\d+(?:[.,]\d+)?)\s*(?:years?|yrs?|jahre?n?)\b", re.IGNORECASE)
_SINGLE_YEARS = re.compile(r"(\d+(?:[.,]\d+)?)\s*\+?\s*(?:years?|yrs?|jahre?n?)\b", re.IGNORECASE)


def _strings(value: Any, name: str, limit: int = 25, max_length: int = 120) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of non-empty strings")
    cleaned = list(dict.fromkeys(item.strip() for item in value))
    if len(cleaned) > limit or any(len(item) > max_length for item in cleaned):
        raise ValueError(f"{name} is limited to {limit} entries of {max_length} characters")
    return cleaned


def _selected_role_terms(item: dict[str, Any], base: dict[str, Any] | None, custom: bool) -> list[str]:
    if not custom and base is None:
        raise ValueError("Non-custom role must match a current suggested role")
    fallback = base["search_terms"] if base else None
    return _strings(item.get("search_terms", fallback), "role search_terms", limit=12, max_length=120)


def _selected_role(item: Any, available: dict[str, dict[str, Any]], role_ids: set[str]) -> dict[str, Any]:
    if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "title")):
        raise ValueError("Each selected role requires id and title")
    if len(item["id"]) > 120 or len(item["title"]) > 160:
        raise ValueError("Role ids and titles are too long")
    if item["id"] in role_ids:
        raise ValueError("Selected role ids must be unique")
    custom = item.get("custom", False)
    if not isinstance(custom, bool):
        raise ValueError("Role custom must be boolean")
    terms = _selected_role_terms(item, available.get(item["id"]), custom)
    role_ids.add(item["id"])
    return {"id": item["id"], "title": item["title"].strip(), "search_terms": terms, "custom": custom}


def _validate_roles(value: Any, roles_task: dict[str, Any]) -> list[dict[str, Any]]:
    selected = value.get("roles")
    if not isinstance(selected, list) or not selected:
        raise ValueError("Select at least one suggested or custom role")
    requested_task = value.get("roles_task_id")
    if requested_task is not None and requested_task != roles_task["id"]:
        raise ValueError("roles_task_id must identify the completed role task being used")
    available = {item["id"]: item for item in roles_task["result"]["roles"]}
    role_ids: set[str] = set()
    return [_selected_role(item, available, role_ids) for item in selected]


def _validate_languages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("languages must be an array")
    languages, names = [], set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("language"), str) or not item["language"].strip() or item.get("level") not in LEVELS:
            raise ValueError("languages require a language and A1-C2 or any level")
        name = item["language"].strip()
        if len(name) > 80 or name.casefold() in names:
            raise ValueError("languages must be unique and at most 80 characters")
        names.add(name.casefold())
        languages.append({"language": name, "level": item["level"]})
    return languages


def _iso_date(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must use ISO YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use ISO YYYY-MM-DD") from exc
    return value


def _validate_dates(value: dict[str, Any]) -> tuple[str | None, str | None]:
    after = _iso_date(value.get("posted_after"), "posted_after")
    before = _iso_date(value.get("posted_before"), "posted_before")
    if after and before and date.fromisoformat(after) > date.fromisoformat(before):
        raise ValueError("posted_after must not follow posted_before")
    return after, before


def _validate_boards(value: Any) -> list[str]:
    boards = _strings(value, "ats_board_urls", limit=20, max_length=2048)
    if any(not _public_http(url) for url in boards):
        raise ValueError("ATS boards must be public HTTP(S) URLs without embedded credentials")
    return boards


def _validate_selections(value: dict[str, Any], source_ids: set[str]) -> dict[str, Any]:
    sources = _strings(value.get("sources"), "sources", limit=8, max_length=40)
    if not sources or not set(sources) <= source_ids:
        raise ValueError("Select one or more supported source IDs")
    selected: dict[str, list[str]] = {}
    for key, allowed in (("seniority", SENIORITY), ("work_modes", WORK_MODES), ("employment_types", EMPLOYMENT)):
        vals = _strings(value.get(key, []), key, limit=6, max_length=40)
        if not set(vals) <= allowed:
            raise ValueError(f"Unsupported value in {key}")
        selected[key] = vals
    selected["sources"] = sources
    selected["languages"] = _validate_languages(value.get("languages", []))
    for key, limit, length in (("countries", 25, 80), ("cities", 50, 80), ("remote_regions", 25, 80), ("excluded_titles", 100, 160)):
        selected[key] = _strings(value.get(key, []), key, limit=limit, max_length=length)
    return selected


def _valid_optional_number(value: Any, minimum: float, maximum: float) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return minimum <= value <= maximum


def _validate_limits(value: dict[str, Any], cities: list[str]) -> dict[str, Any]:
    experience = value.get("max_required_experience_years")
    if not _valid_optional_number(experience, 0, 80):
        raise ValueError("max_required_experience_years must be null or between 0 and 80")
    radius = value.get("radius_km")
    if radius is not None and (not _valid_optional_number(radius, 1, 1000) or not cities):
        raise ValueError("radius_km must be positive and requires at least one city")
    age = value.get("posted_within_days")
    if age is not None and (type(age) is not int or not 1 <= age <= 3650):
        raise ValueError("posted_within_days must be null or an integer from 1 to 3650")
    limit = value.get("max_results_per_source", 10)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("max_results_per_source must be from 1 to 50")
    return {"max_required_experience_years": experience, "radius_km": radius, "posted_within_days": age, "max_results_per_source": limit}


def _validate_flags(value: dict[str, Any]) -> dict[str, Any]:
    policy = value.get("unknown_policy", "review")
    if policy not in {"review", "exclude"}:
        raise ValueError("unknown_policy must be review or exclude")
    for key in ("exclude_seen", "exclude_applied"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"{key} must be boolean")
    return {"unknown_policy": policy, "exclude_seen": value.get("exclude_seen", False), "exclude_applied": value.get("exclude_applied", False)}


def validate_config(value: Any, intake_id: str, revision: str, roles_task: dict[str, Any], source_ids: set[str], config_id: str, config_revision: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Search config must be an object")
    unknown = set(value) - _CONFIG_FIELDS
    if unknown:
        raise ValueError("Unknown search config fields: " + ", ".join(sorted(unknown)))
    roles = _validate_roles(value, roles_task)
    selections = _validate_selections(value, source_ids)
    limits = _validate_limits(value, selections["cities"])
    flags = _validate_flags(value)
    after, before = _validate_dates(value)
    return {
        "id": config_id, "revision": config_revision, "intake_id": intake_id, "profile_revision": revision,
        "roles_task_id": roles_task["id"], "roles": roles, "excluded_titles": selections["excluded_titles"],
        "max_required_experience_years": limits["max_required_experience_years"], "seniority": selections["seniority"],
        "countries": selections["countries"], "cities": selections["cities"], "radius_km": limits["radius_km"],
        "work_modes": selections["work_modes"], "remote_regions": selections["remote_regions"],
        "posted_within_days": limits["posted_within_days"], "posted_after": after, "posted_before": before,
        "employment_types": selections["employment_types"], "languages": selections["languages"], "sources": selections["sources"],
        "unknown_policy": flags["unknown_policy"], "exclude_seen": flags["exclude_seen"], "exclude_applied": flags["exclude_applied"],
        "ats_board_urls": _validate_boards(value.get("ats_board_urls", [])),
        "max_results_per_source": limits["max_results_per_source"],
    }


def _public_http(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username and not parts.password and parts.port in {None, 80, 443}
    except ValueError:
        return False


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _valid_evidence_item(item: Any, pages: dict[str, dict[str, Any]]) -> dict[str, str]:
    if not isinstance(item, dict) or item.get("field") not in _EVIDENCE_FIELDS:
        raise ValueError("Evidence requires a supported field")
    page_id, quote = item.get("page_id"), item.get("quote")
    page = pages.get(page_id) if isinstance(page_id, str) else None
    if not page or not isinstance(quote, str) or len(quote.strip()) < 3 or len(quote) > 4000:
        raise ValueError("Evidence requires a captured page and a 3-4000 character quote")
    if quote.casefold() not in page.get("text", "").casefold():
        raise ValueError("Every evidence quote must appear on its cited archived page")
    return {"field": item["field"], "quote": quote.strip(), "page_id": page_id}


def _evidence_items(job: dict[str, Any], pages: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    evidence = job.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Job assessments require evidence")
    return [_valid_evidence_item(item, pages) for item in evidence]


def _core_fields(job: dict[str, Any], role_ids: set[str]) -> None:
    for key, limit in (("title", 300), ("company", 300), ("location", 300)):
        if not isinstance(job.get(key), str) or not job[key].strip() or len(job[key]) > limit:
            raise ValueError("Job assessments require title, company, and location")
    if not isinstance(job.get("url"), str) or not job["url"].strip():
        raise ValueError("Job assessments require a captured detail URL")
    if not isinstance(job.get("source_id"), str) or not job["source_id"].strip():
        raise ValueError("Job assessments require source_id")
    if job.get("matched_role_id") not in role_ids:
        raise ValueError("matched_role_id must identify a selected role")


def _valid_enum(job: dict[str, Any]) -> None:
    for key, allowed in (("seniority", SENIORITY), ("work_mode", WORK_MODES), ("employment_type", EMPLOYMENT)):
        if job.get(key) is not None and job[key] not in allowed:
            raise ValueError(f"Unsupported {key} value")


def _validate_job_enums(job: dict[str, Any]) -> None:
    experience = job.get("required_experience_years")
    if not _valid_optional_number(experience, 0, 80):
        raise ValueError("required_experience_years must be null or between 0 and 80")
    _valid_enum(job)
    posted_at = job.get("posted_at")
    if posted_at is not None and _parse_datetime(posted_at) is None:
        raise ValueError("posted_at must be an ISO datetime or null")
    notes = job.get("notes", [])
    if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise ValueError("notes must be an array of strings")


def _validate_job_regions(job: dict[str, Any]) -> list[str]:
    regions = job.get("remote_regions", [])
    if not isinstance(regions, list) or any(not isinstance(item, str) or not item.strip() for item in regions):
        raise ValueError("remote_regions must be an array of non-empty strings")
    return list(dict.fromkeys(item.strip() for item in regions))


def _validate_job_languages(job: dict[str, Any]) -> list[dict[str, str]]:
    languages = job.get("languages", [])
    if not isinstance(languages, list):
        raise ValueError("languages must be an array")
    cleaned = []
    for item in languages:
        if not isinstance(item, dict) or not isinstance(item.get("language"), str) or not item["language"].strip() or item.get("level") not in set(LEVEL_ORDER):
            raise ValueError("languages require a language and A1-C2 level")
        cleaned.append({"language": item["language"].strip(), "level": item["level"]})
    if len({item["language"].casefold() for item in cleaned}) != len(cleaned):
        raise ValueError("languages must be unique per job")
    return cleaned


def _enum_fields(job: dict[str, Any]) -> tuple[float | None, list[str], list[dict[str, str]], str | None]:
    _validate_job_enums(job)
    return (
        job.get("required_experience_years"),
        _validate_job_regions(job),
        _validate_job_languages(job),
        job.get("posted_at"),
    )


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _core_values_supported(job: dict[str, Any], by_field: dict[str, list[str]]) -> None:
    for field in _CORE_FIELDS:
        target = _normalized_text(job[field])
        if not any(target in _normalized_text(quote) for quote in by_field.get(field, [])):
            raise ValueError(f"{field} value does not appear in its evidence quote")


def _require_evidence(job: dict[str, Any], evidence: list[dict[str, str]], page_id: str) -> dict[str, list[str]]:
    by_field: dict[str, list[str]] = {}
    for item in evidence:
        if item["page_id"] == page_id:
            by_field.setdefault(item["field"], []).append(item["quote"])
    for field in _CORE_FIELDS:
        if not by_field.get(field):
            raise ValueError(f"{field} requires evidence from the captured detail page")
    _core_values_supported(job, by_field)
    for field in _REQUIREMENT_FIELDS:
        value = job.get(field)
        if value in (None, "", []):
            continue
        if not by_field.get(field):
            raise ValueError(f"{field} requires evidence from the captured detail page")
    return by_field


def _quotes(by_field: dict[str, list[str]], field: str) -> list[str]:
    return by_field.get(field, [])


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _year_context(quote: str, start: int, end: int) -> str:
    left = max((quote.rfind(mark, 0, start) for mark in ".!?;\n"), default=-1) + 1
    rights = [index for index in (quote.find(mark, end) for mark in ".!?;\n") if index != -1]
    return quote[left : min(rights) if rights else len(quote)].casefold()


def _years_are_required(context: str) -> bool:
    if any(cue in context for cue in _YEARS_OPTIONAL_CUES):
        return False
    return any(cue in context for cue in _YEARS_REQUIRED_CUES)


def _years_supported(quote: str, value: float) -> bool:
    lowered = quote.casefold()
    if any(marker in lowered for marker in ("no experience", "keine berufserfahrung", "without experience")):
        return value == 0
    for match in _RANGE_YEARS.finditer(quote):
        context = _year_context(quote, match.start(), match.end())
        if abs(_number(match.group(1)) - value) < 0.01 and _years_are_required(context):
            return True
    if _RANGE_YEARS.search(quote):
        return False
    for match in _SINGLE_YEARS.finditer(quote):
        context = _year_context(quote, match.start(), match.end())
        if abs(_number(match.group(1)) - value) < 0.01 and _years_are_required(context):
            return True
    return False


def _day_interval(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _date_quote(quote: str) -> date | None:
    match = _ISO_DATE.search(quote)
    if match:
        return date.fromisoformat(match.group(1))
    match = _DMY_DATE.search(quote)
    return date(int(match.group(3)), int(match.group(2)), int(match.group(1))) if match else None


def _relative_posted_interval(quote: str, anchor: datetime) -> tuple[datetime, datetime] | None:
    lowered = quote.casefold()
    if any(token in lowered for token in ("today", "heute", "just posted")):
        return _day_interval(anchor.date())
    if any(token in lowered for token in ("yesterday", "gestern")):
        return _day_interval(anchor.date() - timedelta(days=1))
    for pattern, unit in ((_RELATIVE_HOURS, "hours"), (_RELATIVE_DAYS, "days"), (_RELATIVE_WEEKS, "weeks")):
        match = pattern.search(quote)
        if not match:
            continue
        amount = int(match.group(1))
        if unit == "hours":
            end = anchor - timedelta(hours=amount)
            return end - timedelta(hours=1), end
        return _day_interval(anchor.date() - timedelta(**{unit: amount}))
    return None


def _posted_interval(quote: str, fetched_at: str) -> tuple[datetime, datetime] | None:
    explicit = _date_quote(quote)
    if explicit is not None:
        return _day_interval(explicit)
    anchor = _parse_datetime(fetched_at)
    return _relative_posted_interval(quote, anchor) if anchor is not None else None


def _interval_covers_date(interval: tuple[datetime, datetime], day: date) -> bool:
    return interval[0].date() <= day <= (interval[1] - timedelta(microseconds=1)).date()


def _job_posted_interval(job: dict[str, Any], fetched_at: str, posted: datetime) -> tuple[datetime, datetime] | None:
    intervals = []
    for item in job.get("evidence") or []:
        if isinstance(item, dict) and item.get("field") == "posted_at" and isinstance(item.get("quote"), str):
            interval = _posted_interval(item["quote"], fetched_at)
            if interval is not None:
                intervals.append(interval)
    if not intervals:
        return None
    for interval in intervals:
        if _interval_covers_date(interval, posted.date()):
            return interval
    return max(intervals, key=lambda item: item[1] - item[0])


def _check_years_quote(job: dict[str, Any], quotes: list[str]) -> None:
    experience = job.get("required_experience_years")
    if experience is not None and not any(_years_supported(quote, float(experience)) for quote in quotes):
        raise ValueError("required_experience_years is not supported by a parseable years quote")


def _check_posted_quote(job: dict[str, Any], quotes: list[str], fetched_at: str) -> None:
    posted = job.get("posted_at")
    if posted is None:
        return
    posted_at = _parse_datetime(posted)
    for quote in quotes:
        interval = _posted_interval(quote, fetched_at)
        if interval is None:
            continue
        last_day = (interval[1] - timedelta(microseconds=1)).date()
        if interval[0].date() <= posted_at.date() <= last_day:
            return
    raise ValueError("posted_at must match a date stated in its evidence quote")


def _check_token_quote(value: str | None, quotes: list[str], tokens: dict[str, tuple[str, ...]], label: str) -> None:
    if value and not any(any(token in quote.casefold() for token in tokens[value]) for quote in quotes):
        raise ValueError(f"{label} is not supported by its evidence quote")


def _check_region_quotes(regions: list[str], quotes: list[str]) -> None:
    for region in regions:
        if not any(region.casefold() in quote.casefold() for quote in quotes):
            raise ValueError("remote_regions are not supported by their evidence quote")


def _check_language_quotes(languages: list[dict[str, str]], quotes: list[str]) -> None:
    for language in languages:
        matches = [quote for quote in quotes if language["language"].casefold() in quote.casefold()]
        if not any(language["level"].casefold() in quote.casefold() for quote in matches):
            raise ValueError("language level is not supported by its evidence quote")


def _requirement_quotes(job: dict[str, Any], by_field: dict[str, list[str]], fetched_at: str) -> None:
    _check_years_quote(job, _quotes(by_field, "required_experience_years"))
    _check_posted_quote(job, _quotes(by_field, "posted_at"), fetched_at)
    _check_token_quote(job.get("seniority"), _quotes(by_field, "seniority"), _SENIORITY_TOKENS, "seniority")
    _check_token_quote(job.get("work_mode"), _quotes(by_field, "work_mode"), _WORK_MODE_TOKENS, "work_mode")
    _check_token_quote(job.get("employment_type"), _quotes(by_field, "employment_type"), _EMPLOYMENT_TOKENS, "employment_type")
    _check_region_quotes(job.get("remote_regions") or [], _quotes(by_field, "remote_regions"))
    _check_language_quotes(job.get("languages", []), _quotes(by_field, "languages"))


def _assessment_page(job: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
    page = next(
        (item for item in pages if isinstance(item, dict) and item.get("kind") == "detail" and item.get("url") == job.get("url")),
        None,
    )
    if not page:
        raise ValueError("Assessment URL must identify an archived detail page")
    return page


def _normalized_assessment(job: dict[str, Any], experience, regions, languages, posted_at, evidence) -> dict[str, Any]:
    return {
        "source_id": job["source_id"], "url": job["url"], "title": job["title"].strip(),
        "company": job["company"].strip(), "location": job["location"].strip(),
        "matched_role_id": job["matched_role_id"], "required_experience_years": experience,
        "seniority": job.get("seniority"), "work_mode": job.get("work_mode"),
        "remote_regions": regions, "posted_at": posted_at, "employment_type": job.get("employment_type"),
        "languages": languages, "evidence": evidence, "notes": [item.strip() for item in job.get("notes", [])],
    }


def validate_assessment(job: Any, pages: list[dict[str, Any]], role_ids: set[str], fetched_at: str) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise ValueError("Each job assessment must be an object")
    page = _assessment_page(job, pages)
    _core_fields(job, role_ids)
    if job.get("source_id") != page.get("source_id"):
        raise ValueError("Assessment source must match the captured detail page")
    page_map = {item["id"]: item for item in pages if isinstance(item, dict) and isinstance(item.get("id"), str)}
    evidence = _evidence_items(job, page_map)
    by_field = _require_evidence(job, evidence, page["id"])
    experience, regions, languages, posted_at = _enum_fields(job)
    _requirement_quotes({**job, "remote_regions": regions, "languages": languages, "posted_at": posted_at}, by_field, fetched_at)
    return _normalized_assessment(job, experience, regions, languages, posted_at, evidence)


def _merge_languages(existing: dict[str, Any], incoming: list[dict[str, str]]) -> None:
    for language in incoming:
        current = next((item for item in existing["languages"] if item["language"].casefold() == language["language"].casefold()), None)
        if current is None:
            existing["languages"].append(language)
        elif LEVEL_ORDER.index(language["level"]) > LEVEL_ORDER.index(current["level"]):
            current["level"] = language["level"]


def _merge_experience(existing: dict[str, Any], incoming: float | None) -> None:
    if incoming is None:
        return
    current = existing["required_experience_years"]
    existing["required_experience_years"] = incoming if current is None else max(current, incoming)


def _merge_job(existing: dict[str, Any], job: dict[str, Any]) -> None:
    new_role = job["matched_role_id"] not in existing["matched_role_ids"]
    if new_role:
        existing["matched_role_ids"].append(job["matched_role_id"])
    if job["source_id"] not in existing["source_ids"]:
        existing["source_ids"].append(job["source_id"])
    existing["evidence"] = list({(item["field"], item["page_id"], item["quote"]): item for item in existing["evidence"] + job["evidence"]}.values())
    extra_notes = [f"Also matched role {job['matched_role_id']}"] if new_role else []
    existing["notes"] = list(dict.fromkeys(existing["notes"] + job["notes"] + extra_notes))
    existing["remote_regions"] = list(dict.fromkeys(existing["remote_regions"] + job["remote_regions"]))
    _merge_languages(existing, job["languages"])
    _merge_experience(existing, job["required_experience_years"])


def _experience_state(job: dict[str, Any], config: dict[str, Any]) -> tuple[bool | None, str]:
    maximum = config["max_required_experience_years"]
    if maximum is None:
        return True, ""
    actual = job.get("required_experience_years")
    if actual is None:
        return None, "Required experience is unknown"
    return actual <= maximum, "Required experience exceeds the configured maximum"


def _enum_state(job: dict[str, Any], config: dict[str, Any], config_key: str, job_key: str, label: str) -> tuple[bool | None, str]:
    allowed = config[config_key]
    if not allowed:
        return True, ""
    actual = job.get(job_key)
    if actual is None:
        return None, f"{label} is unknown"
    return actual in allowed, f"{label} is outside the requested values"


def _word_present(text: str, phrase: str) -> bool:
    wanted = " ".join(phrase.casefold().split())
    return bool(wanted) and re.search(r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", text.casefold()) is not None


def _city_present(text: str, city: str) -> bool:
    folded = text.casefold()
    wanted = " ".join(city.casefold().split())
    if not wanted:
        return False
    for match in re.finditer(r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", folded):
        prefix = folded[: match.start()].rstrip()
        if not prefix or not prefix[-1].isalpha():
            return True
    return False


def _country_state(text: str, countries: list[str]) -> tuple[bool | None, str]:
    if not countries:
        return True, ""
    if any(_word_present(text, country) for country in countries):
        return True, ""
    return None, "Location does not establish the requested country"


def _city_state(text: str, cities: list[str], remote: bool) -> tuple[bool | None, str]:
    if not cities:
        return True, ""
    if any(_city_present(text, city) for city in cities):
        return True, ""
    if remote:
        return None, "Remote location does not establish the selected city"
    return False, "Location does not match the selected city"


def _job_is_remote(job: dict[str, Any]) -> bool:
    return job.get("work_mode") == "remote" or "remote" in (job.get("location") or "").casefold()


def _combined_geography(states: list[tuple[bool | None, str]]) -> tuple[bool | None, str]:
    failures = [reason for state, reason in states if state is False]
    unknowns = [reason for state, reason in states if state is None]
    if failures:
        return False, failures[0]
    if unknowns:
        return None, unknowns[0]
    return True, ""


def _geography_state(job: dict[str, Any], config: dict[str, Any]) -> tuple[bool | None, str]:
    if not config["countries"] and not config["cities"]:
        return True, ""
    location = job.get("location") or ""
    text = " ".join([location, *(job.get("remote_regions") or [])])
    return _combined_geography([
        _country_state(text, config["countries"]),
        _city_state(location, config["cities"], _job_is_remote(job)),
    ])


def _remote_region_state(job: dict[str, Any], config: dict[str, Any]) -> tuple[bool | None, str]:
    requested = config["remote_regions"]
    if not requested or not _job_is_remote(job):
        return True, ""
    regions = [item.casefold() for item in job.get("remote_regions") or []]
    if not regions:
        return None, "Remote region is unknown"
    for wanted in requested:
        folded = wanted.casefold()
        if any(folded in region or region in folded for region in regions):
            return True, ""
    return False, "Remote region is outside the requested remote regions"


def _posting_age_state(interval: tuple[datetime, datetime], fetched: datetime | None, days: int) -> bool | None:
    if fetched is None:
        return None
    start, end = interval
    if start > fetched:
        return False
    cutoff = fetched - timedelta(days=days)
    if end <= cutoff:
        return False
    if start >= cutoff:
        return True
    return None


def _posting_range_failed(posted: datetime, after: str | None, before: str | None) -> bool:
    return bool((after and posted.date() < date.fromisoformat(after)) or (before and posted.date() > date.fromisoformat(before)))


def _posting_age_reason(state: bool | None) -> tuple[bool | None, str] | None:
    if state is False:
        return False, "Posting is older than the requested age"
    if state is None:
        return None, "Posting time precision cannot establish the requested age"
    return None


def _posting_age_check(job: dict[str, Any], config: dict[str, Any], fetched_at: str, posted: datetime) -> tuple[bool | None, str] | None:
    fetched = _parse_datetime(fetched_at)
    interval = _job_posted_interval(job, fetched_at, posted) or (posted, posted + timedelta(microseconds=1))
    if fetched is not None and interval[0] > fetched:
        return False, "Posting date is later than the observed search"
    days = config["posted_within_days"]
    return _posting_age_reason(_posting_age_state(interval, fetched, days)) if days else None


def _posting_state(job: dict[str, Any], config: dict[str, Any], fetched_at: str) -> tuple[bool | None, str]:
    if not config["posted_within_days"] and not config["posted_after"] and not config["posted_before"]:
        return True, ""
    posted = _parse_datetime(job.get("posted_at"))
    if posted is None:
        return None, "Posting date is unknown"
    age_reason = _posting_age_check(job, config, fetched_at, posted)
    if age_reason is not None:
        return age_reason
    if _posting_range_failed(posted, config["posted_after"], config["posted_before"]):
        return False, "Posting is outside the requested date range"
    return True, ""


def _language_states(job: dict[str, Any], config: dict[str, Any]) -> list[tuple[bool | None, str]]:
    states = []
    for wanted in config["languages"]:
        found = next((item for item in job.get("languages", []) if item["language"].casefold() == wanted["language"].casefold()), None)
        if found is None:
            states.append((None, f"Required language proficiency is unknown: {wanted['language']}"))
        elif wanted["level"] == "any":
            states.append((True, ""))
        elif found["level"] not in LEVEL_ORDER:
            states.append((None, f"Required language level is unknown: {wanted['language']}"))
        else:
            states.append((LEVEL_ORDER.index(found["level"]) <= LEVEL_ORDER.index(wanted["level"]), f"Required language proficiency exceeds the accepted maximum: {wanted['language']}"))
    return states


def _radius_state(config: dict[str, Any]) -> tuple[bool | None, str]:
    if not config["radius_km"]:
        return True, ""
    return None, "Distance from the configured city cannot be established"


def _all_states(job: dict[str, Any], config: dict[str, Any], fetched_at: str) -> list[tuple[bool | None, str]]:
    return [
        _experience_state(job, config),
        _enum_state(job, config, "seniority", "seniority", "Seniority"),
        _enum_state(job, config, "work_modes", "work_mode", "Work mode"),
        _enum_state(job, config, "employment_types", "employment_type", "Employment type"),
        _geography_state(job, config),
        _remote_region_state(job, config),
        _radius_state(config),
        _posting_state(job, config, fetched_at),
        *_language_states(job, config),
    ]


def _excluded_job(job: dict[str, Any], config: dict[str, Any], seen: set[str], applied: set[str]) -> dict[str, Any] | None:
    key = normalize_job_url(job["url"])
    if config["exclude_seen"] and key in {normalize_job_url(url) for url in seen}:
        return {**job, "decision": "excluded", "reasons": ["Previously seen in an earlier run"], "exclusion_reasons": ["Previously seen in an earlier run"], "review_reasons": []}
    if config["exclude_applied"] and key in {normalize_job_url(url) for url in applied}:
        return {**job, "decision": "excluded", "reasons": ["Marked applied in this intake"], "exclusion_reasons": ["Marked applied in this intake"], "review_reasons": []}
    if any(value.casefold() in job["title"].casefold() for value in config["excluded_titles"]):
        return {**job, "decision": "excluded", "reasons": ["Title matches an excluded title"], "exclusion_reasons": ["Title matches an excluded title"], "review_reasons": []}
    return None


def _decision(states: list[tuple[bool | None, str]], policy: str) -> tuple[str, list[str], list[str]]:
    failures = [reason for state, reason in states if state is False]
    unknowns = [reason for state, reason in states if state is None]
    if failures or (unknowns and policy == "exclude"):
        return "excluded", failures + unknowns, []
    if unknowns:
        return "review", [], unknowns
    return "match", [], []


def assess(job: dict[str, Any], config: dict[str, Any], pages: list[dict[str, Any]], seen: set[str], applied: set[str], fetched_at: str) -> dict[str, Any]:
    excluded = _excluded_job(job, config, seen, applied)
    if excluded is not None:
        return excluded
    decision, exclusion, review = _decision(_all_states(job, config, fetched_at), config["unknown_policy"])
    return {**job, "decision": decision, "reasons": exclusion + review, "exclusion_reasons": exclusion, "review_reasons": review}


def _validated_jobs(result: dict[str, Any], pages: list[dict[str, Any]], config: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    roles = {item["id"] for item in config["roles"]}
    jobs = []
    for index, raw in enumerate(result["jobs"]):
        try:
            job = validate_assessment(raw, pages, roles, fetched_at)
        except ValueError as exc:
            raise ValueError(f"Job {index + 1}: {exc}") from exc
        job.update(matched_role_ids=[job["matched_role_id"]], source_ids=[job["source_id"]])
        jobs.append(job)
    return jobs


def _merged_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for job in jobs:
        key = normalize_job_url(job["url"]) or job["url"]
        if key in merged:
            _merge_job(merged[key], job)
        else:
            merged[key] = job
    return list(merged.values())


def normalize_assessments(result: Any, pages: Any, config: dict[str, Any], seen: set[str], applied: set[str], fetched_at: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
        raise ValueError("Search result requires a jobs array")
    if not isinstance(pages, list) or not isinstance(fetched_at, str):
        raise ValueError("Search acquisition pages and fetched_at are required")
    jobs = _merged_jobs(_validated_jobs(result, pages, config, fetched_at))
    return [assess(job, config, pages, seen, applied, fetched_at) for job in jobs]

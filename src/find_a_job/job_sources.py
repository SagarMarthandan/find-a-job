"""Visible-browser job discovery across native public job sources.

Only :class:`find_a_job.browser.BrowserSession` fetches pages. No HTTP API,
headless browser or cached listing is substituted for an actual visit.
"""

from __future__ import annotations

import copy
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlencode, urlsplit

from .browser import (
    BrowserError,
    BrowserSessionClosedError,
    BrowserUnavailableError,
    UnsafeUrlError,
)
from .job_identity import normalize_job_url

SOURCE_IDS = (
    "linkedin",
    "indeed",
    "arbeitnow",
    "xing",
    "stepstone",
    "wellfound",
    "eu_remote",
    "ats",
)

DEFAULT_RESULTS_PER_SOURCE = 10
MAX_RESULTS_PER_SOURCE = 50

_ACQUISITION_FIELDS = (
    "excluded_titles",
    "max_required_experience_years",
    "seniority",
    "countries",
    "cities",
    "radius_km",
    "work_modes",
    "remote_regions",
    "posted_within_days",
    "posted_after",
    "posted_before",
    "employment_types",
    "languages",
)

_NATIVE_FIELDS = {
    "linkedin": {"roles", "cities", "countries", "posted_within_days"},
    "indeed": {"roles", "cities", "countries", "posted_within_days"},
    "arbeitnow": {"roles"},
    "xing": {"roles", "cities", "countries"},
    "stepstone": {"roles", "cities", "countries"},
    "wellfound": {"roles"},
    "eu_remote": {"roles"},
    "ats": set(),
}

_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "arbeitnow": "Arbeitnow",
    "xing": "XING",
    "stepstone": "Stepstone",
    "wellfound": "Wellfound",
    "eu_remote": "EU Remote Jobs",
    "ats": "ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters)",
}

_NOTES = {
    "linkedin": (
        "Public job search; logged-out access may redirect to an authwall or "
        "rate-limit, which is reported as blocked. Freshness uses f_TPR."
    ),
    "indeed": (
        "Public job search on the country domain; login, 403/429 and challenge "
        "pages are reported as blocked. Freshness uses fromage where supported."
    ),
    "arbeitnow": (
        "Public board with a native keyword search; location is not a native "
        "query and must be verified on captured detail pages."
    ),
    "xing": (
        "Public job search with keyword and location; posting dates, work mode "
        "and seniority require postchecks."
    ),
    "stepstone": (
        "Public job search using path-based keyword/location URLs; only the "
        "24-hour age filter is native. Other filter ranges are postchecked."
    ),
    "wellfound": (
        "Public role listing pages; location, company and dates require "
        "postchecks on captured pages."
    ),
    "eu_remote": (
        "Public WordPress job search; location/region and dates require "
        "postchecks on captured pages."
    ),
    "ats": (
        "User-supplied visible board URLs on Greenhouse, Lever, Ashby or "
        "SmartRecruiters; no board is inferred from a candidate profile."
    ),
}

_BLOCK_TITLE_PATTERNS = (
    "just a moment",
    "access denied",
    "attention required",
    "authwall",
    "captcha",
    "security check",
    "sign in",
    "log in",
)
_BLOCK_BODY_PATTERNS = (
    "verify you are human",
    "verify you are a human",
    "complete the captcha",
    "unusual traffic from your computer",
    "enable javascript and cookies to continue",
)
_BLOCKED_STATUSES = frozenset({401, 403, 429, 999})

_ATS_HOST_KINDS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "careers.smartrecruiters.com": "smartrecruiters",
}

_INDEED_COUNTRY_HOSTS = {
    "germany": "de.indeed.com",
    "deutschland": "de.indeed.com",
    "austria": "at.indeed.com",
    "österreich": "at.indeed.com",
    "switzerland": "ch.indeed.com",
    "schweiz": "ch.indeed.com",
    "france": "fr.indeed.com",
    "netherlands": "nl.indeed.com",
    "spain": "es.indeed.com",
    "italy": "it.indeed.com",
    "poland": "pl.indeed.com",
    "united kingdom": "uk.indeed.com",
    "uk": "uk.indeed.com",
    "united states": "www.indeed.com",
    "usa": "www.indeed.com",
    "canada": "ca.indeed.com",
    "ireland": "ie.indeed.com",
    "belgium": "be.indeed.com",
    "portugal": "pt.indeed.com",
    "sweden": "se.indeed.com",
    "denmark": "dk.indeed.com",
    "norway": "no.indeed.com",
    "finland": "fi.indeed.com",
}

_STEPSTONE_PLACES = {
    "germany": "deutschland",
    "deutschland": "deutschland",
    "austria": "oesterreich",
    "österreich": "oesterreich",
    "switzerland": "schweiz",
    "schweiz": "schweiz",
}

_JOB_LINK_PATTERNS = {
    "linkedin": re.compile(r"^/jobs/view/\d+"),
    "arbeitnow": re.compile(r"^/jobs/companies/[^/]+/[^/]+-\d+/?$"),
    "xing": re.compile(r"^/jobs/(?!search)(?:[^/]+-)?\d+/?$"),
    "stepstone": re.compile(r"^(?:/stellenangebote--[^/]+|/jobs/[^/]+--[^/]+-inline\.html)$"),
    "wellfound": re.compile(r"^/jobs/\d+"),
    "eu_remote": re.compile(r"^/job/[^/]+/?$"),
}
_INDEED_DOMAINS = (
    "indeed.com", "indeed.co.uk", "indeed.com.au", "indeed.co.in", "indeed.co.jp",
    "indeed.com.br", "indeed.com.mx", "indeed.com.sg", "indeed.com.hk", "indeed.co.kr",
    "indeed.com.tr", "indeed.com.ar", "indeed.de", "indeed.fr", "indeed.it", "indeed.es",
    "indeed.nl", "indeed.at", "indeed.ch", "indeed.be", "indeed.ie", "indeed.ca",
    "indeed.pl", "indeed.se", "indeed.dk", "indeed.no", "indeed.fi", "indeed.pt",
)
_SOURCE_HOSTS = {
    "linkedin": ("linkedin.com",),
    "indeed": _INDEED_DOMAINS,
    "arbeitnow": ("arbeitnow.com",),
    "xing": ("xing.com",),
    "stepstone": ("stepstone.de", "stepstone.at", "stepstone.ch"),
    "wellfound": ("wellfound.com",),
    "eu_remote": ("euremotejobs.com",),
}
_INDEED_PATHS = ("/viewjob", "/m/viewjob", "/rc/clk")


def _catalog_entry(source_id: str) -> dict:
    native = _NATIVE_FIELDS[source_id]
    return {
        "id": source_id,
        "label": _LABELS[source_id],
        "native_filters": [f for f in ("roles",) + _ACQUISITION_FIELDS if f in native],
        "post_filters": [f for f in _ACQUISITION_FIELDS if f not in native],
        "notes": _NOTES[source_id],
    }


_CATALOG = [_catalog_entry(source_id) for source_id in SOURCE_IDS]


def source_catalog() -> list[dict]:
    """Return the eight supported sources and their real filter capabilities."""
    return copy.deepcopy(_CATALOG)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = item.strip() if isinstance(item, str) else ""
        if text and text not in out:
            out.append(text)
    return out


def _positive_int(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _result_limit(config: dict) -> int:
    configured = _positive_int(config.get("max_results_per_source"))
    return min(MAX_RESULTS_PER_SOURCE, configured or DEFAULT_RESULTS_PER_SOURCE)


def _terms_by_role(config: dict) -> list[list[str]]:
    grouped: list[list[str]] = []
    for role in config.get("roles") or []:
        if not isinstance(role, dict):
            continue
        terms = _strings([*_strings(role.get("title")), *_strings(role.get("search_terms"))])
        grouped.append(terms)
    return [terms for terms in grouped if terms]


def _all_terms(config: dict) -> list[str]:
    words: list[str] = []
    for terms in _terms_by_role(config):
        for term in terms:
            if term not in words:
                words.append(term)
    return words


def _locations(config: dict) -> list[str]:
    cities = _strings(config.get("cities"))
    countries = _strings(config.get("countries"))
    if cities and countries:
        return [f"{city}, {country}" for country in countries for city in cities]
    return cities or countries or [""]


def _slug(text: str) -> str:
    folded = text.strip().lower()
    folded = (
        folded.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def _linkedin_url(term: str, location: str, config: dict) -> str:
    params = {"keywords": term}
    if location:
        params["location"] = location
    days = _positive_int(config.get("posted_within_days"))
    if days:
        params["f_TPR"] = f"r{days * 86400}"
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


def _indeed_params(term: str, location: str, config: dict) -> str:
    params = {"q": term}
    if location:
        params["l"] = location
    days = _positive_int(config.get("posted_within_days"))
    if days in {1, 3, 7, 14}:
        params["fromage"] = str(days)
    return urlencode(params)


def _indeed_country_groups(countries: list[str], cities: list[str]) -> tuple[list[tuple[str, list[str]]], list[str]]:
    groups: list[tuple[str, list[str]]] = []
    warnings: list[str] = []
    for country in countries:
        host = _INDEED_COUNTRY_HOSTS.get(country.casefold())
        if host is None:
            warnings.append(f"Indeed has no configured country domain for {country!r}; that country was not searched")
            continue
        groups.append((host, [f"{city}, {country}" for city in cities] if cities else [country]))
    return groups, warnings


def _indeed_listing_urls(config: dict) -> tuple[list[str], list[str]]:
    terms = _all_terms(config)
    cities = _strings(config.get("cities"))
    countries = _strings(config.get("countries"))
    if countries:
        groups, warnings = _indeed_country_groups(countries, cities)
    else:
        groups, warnings = [("www.indeed.com", cities or [""])], []
        if cities:
            warnings.append("No Indeed country was selected; using www.indeed.com for city queries")
    urls: list[str] = []
    for host, locations in groups:
        for term in terms:
            for location in locations:
                url = f"https://{host}/jobs?" + _indeed_params(term, location, config)
                if url not in urls:
                    urls.append(url)
    return urls, warnings


def _arbeitnow_url(term: str, _location: str, _config: dict) -> str:
    return "https://www.arbeitnow.com/?search=" + quote_plus(term)


def _xing_url(term: str, location: str, _config: dict) -> str:
    params = {"keywords": term}
    if location:
        params["location"] = location
    return "https://www.xing.com/search/in/jobs?" + urlencode(params)


def _stepstone_url(term: str, location: str, config: dict) -> str:
    term_slug = _slug(term) or "jobs"
    city = location.split(",", 1)[0].strip()
    place = _STEPSTONE_PLACES.get(city.casefold(), _slug(city)) if city else ""
    url = f"https://www.stepstone.de/jobs/{term_slug}"
    if place:
        url += f"/in-{place}"
    params = {"sort": "2"}
    if _positive_int(config.get("posted_within_days")) == 1:
        params["ag"] = "age_1"
    return url + "?" + urlencode(params)


def _wellfound_url(term: str, _location: str, _config: dict) -> str:
    role_slug = _slug(term) or "jobs"
    return f"https://wellfound.com/role/l/{role_slug}"


def _eu_remote_url(term: str, _location: str, _config: dict) -> str:
    return "https://euremotejobs.com/?" + urlencode(
        {"s": term, "post_type": "job_listing"}
    )


_LISTING_BUILDERS = {
    "linkedin": _linkedin_url,
    "arbeitnow": _arbeitnow_url,
    "xing": _xing_url,
    "stepstone": _stepstone_url,
    "wellfound": _wellfound_url,
    "eu_remote": _eu_remote_url,
}


def _listing_urls(source_id: str, config: dict) -> list[str]:
    if source_id == "indeed":
        return _indeed_listing_urls(config)[0]
    builder = _LISTING_BUILDERS[source_id]
    urls: list[str] = []
    for terms in _terms_by_role(config):
        for term in terms:
            for location in _locations(config):
                url = builder(term, location, config)
                if url and url not in urls:
                    urls.append(url)
    return urls


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _safe_parts(url: str):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    try:
        if parts.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    return parts


def _allowed_source_host(source_id: str, host: str) -> bool:
    return any(_host_is(host, domain) for domain in _SOURCE_HOSTS.get(source_id, ()))


def _source_detail_url(source_id: str, url: str) -> bool:
    parts = _safe_parts(url)
    if parts is None:
        return False
    host = (parts.hostname or "").casefold().rstrip(".")
    if not _allowed_source_host(source_id, host):
        return False
    path = parts.path
    if source_id == "indeed":
        return path.rstrip("/") in _INDEED_PATHS
    pattern = _JOB_LINK_PATTERNS.get(source_id)
    return bool(pattern and pattern.match(path))


def _ats_board_spec(url: str) -> dict | None:
    parts = _safe_parts(url)
    if parts is None:
        return None
    host = (parts.hostname or "").casefold().rstrip(".")
    kind = _ATS_HOST_KINDS.get(host)
    if not kind:
        return None
    slug = next((part for part in parts.path.split("/") if part), "")
    if not slug:
        return None
    return {"kind": kind, "slug": slug}


def _ats_posting_match(kind: str, rest: list[str]) -> bool:
    if kind == "greenhouse":
        return len(rest) == 2 and rest[0] == "jobs" and rest[1].isdigit()
    if kind == "smartrecruiters":
        return bool(rest and re.match(r"\d", rest[0]))
    return len(rest) == 1 and bool(re.fullmatch(r"[0-9a-fA-F-]{8,}", rest[0]))


def _ats_detail_url(spec: dict, url: str) -> bool:
    parts = _safe_parts(url)
    if parts is None:
        return False
    host = (parts.hostname or "").casefold().rstrip(".")
    if _ATS_HOST_KINDS.get(host) != spec["kind"]:
        return False
    segments = [part for part in parts.path.split("/") if part]
    if not segments or segments[0] != spec["slug"]:
        return False
    return _ats_posting_match(spec["kind"], segments[1:])


def _role_matches(text: str, url: str, terms: list[str]) -> bool:
    haystack = unquote(text or "") + " " + unquote(url or "")
    haystack = haystack.casefold().replace("-", " ")
    for term in terms:
        words = [
            word
            for word in re.split(r"[^0-9a-zäöüß+#.]+", term.casefold())
            if len(word) >= 2
        ]
        if words and all(word in haystack for word in words):
            return True
    return False


def _blocked_status(status) -> str:
    if isinstance(status, int) and status in _BLOCKED_STATUSES:
        return f"HTTP {status}"
    return ""


def _blocked_url_reason(url: str) -> str:
    parts = urlsplit(url or "")
    segments = [segment.casefold() for segment in parts.path.split("/") if segment]
    if any(segment in {"login", "authwall", "checkpoint"} for segment in segments):
        return "redirected to login/authwall"
    if "authwall" in (parts.hostname or "").casefold():
        return "redirected to login/authwall"
    return ""


def _blocked_text_reason(title: str, text: str) -> str:
    lowered_title = (title or "").casefold()
    if any(pattern in lowered_title for pattern in _BLOCK_TITLE_PATTERNS):
        return "login or challenge page"
    lowered_text = (text or "")[:4000].casefold()
    if any(pattern in lowered_text for pattern in _BLOCK_BODY_PATTERNS):
        return "access challenge on page"
    return ""


def _blocked_reason(status, url: str, title: str, text: str) -> str:
    return (
        _blocked_status(status)
        or _blocked_url_reason(url)
        or _blocked_text_reason(title, text)
    )


def _http_error_reason(status) -> str:
    if isinstance(status, int) and status >= 400:
        return f"HTTP {status}"
    return ""


def _is_configured(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return bool(value)


def _unsupported_filters(source_id: str, config: dict) -> list[str]:
    native = set(_NATIVE_FIELDS.get(source_id, set()))
    days = _positive_int(config.get("posted_within_days"))
    if days and source_id == "indeed" and days in {1, 3, 7, 14}:
        native.add("posted_within_days")
    if days == 1 and source_id == "stepstone":
        native.add("posted_within_days")
    unsupported: list[str] = []
    for field in _ACQUISITION_FIELDS:
        if _is_configured(config.get(field)) and field not in native:
            unsupported.append(field)
    return unsupported


def _result(source_id: str, config: dict) -> dict:
    return {
        "source_id": source_id,
        "status": "empty",
        "listing_urls": [],
        "discovered_count": 0,
        "captured_count": 0,
        "error": None,
        "unsupported_filters": _unsupported_filters(source_id, config),
    }


def _failure(source_id: str, config: dict, status: str, error: str) -> dict:
    result = _result(source_id, config)
    result["status"] = status
    result["error"] = error
    return result


class _SearchRun:
    """One bounded visible-browser acquisition run."""

    def __init__(self, session, config: dict, artifact_dir: Path, cancelled) -> None:
        self.session = session
        self.config = config if isinstance(config, dict) else {}
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self.cancelled = cancelled or (lambda: False)
        self.pages: list[dict] = []
        self.artifacts: list[dict] = []
        self.page_seq = 0
        self.fatal: str | None = None

    def run(self) -> dict:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict] = []
        for source_id in self._selected_sources():
            if self._stopped():
                results.append(self._stopped_result(source_id))
                continue
            if source_id not in SOURCE_IDS:
                results.append(
                    _failure(source_id, self.config, "error", "unknown source id")
                )
                continue
            try:
                results.append(self._run_source(source_id))
            except (BrowserSessionClosedError, BrowserUnavailableError) as exc:
                self.fatal = f"browser session unavailable: {exc}"
                results.append(_failure(source_id, self.config, "error", self.fatal))
            except Exception as exc:
                results.append(
                    _failure(
                        source_id,
                        self.config,
                        "error",
                        f"source acquisition crashed: {exc}",
                    )
                )
        return {
            "fetched_at": _utc_now(),
            "source_results": results,
            "pages": self.pages,
            "artifacts": self.artifacts,
        }

    def _selected_sources(self) -> list[str]:
        selected: list[str] = []
        for source_id in self.config.get("sources") or []:
            if isinstance(source_id, str) and source_id and source_id not in selected:
                selected.append(source_id)
        return selected

    def _stopped(self) -> bool:
        if self.fatal:
            return True
        try:
            return bool(self.cancelled())
        except Exception:
            return False

    def _stopped_result(self, source_id: str) -> dict:
        message = self.fatal or "cancelled"
        return _failure(source_id, self.config, "error", message)

    def _run_source(self, source_id: str) -> dict:
        if source_id == "ats":
            return self._run_ats()
        if source_id in _LISTING_BUILDERS:
            return self._run_standard(source_id)
        return _failure(source_id, self.config, "error", "unknown source id")

    def _run_standard(self, source_id: str) -> dict:
        result = _result(source_id, self.config)
        warnings: list[str] = []
        if source_id == "indeed":
            listing_urls, warnings = _indeed_listing_urls(self.config)
        else:
            listing_urls = _listing_urls(source_id, self.config)
        if not listing_urls:
            detail = "; ".join(warnings) if warnings else "no role search terms configured"
            return _failure(source_id, self.config, "error", detail)
        discovered, listing_state = self._scan_listings(
            source_id, listing_urls, result, _source_detail_url
        )
        result["discovered_count"] = len(discovered)
        detail_state = self._capture_details(source_id, discovered, result)
        self._finalize(result, listing_state, detail_state)
        if warnings:
            note = "; ".join(warnings)
            result["error"] = f"{result['error']}; {note}" if result["error"] else note
        return result

    def _run_ats(self) -> dict:
        result = _result("ats", self.config)
        boards = _strings(self.config.get("ats_board_urls"))
        if not boards:
            return _failure(
                "ats", self.config, "error", "ats requires ats_board_urls"
            )
        specs, invalid = [], []
        for board_url in boards:
            spec = _ats_board_spec(board_url)
            if spec is None:
                invalid.append(board_url)
            else:
                specs.append((board_url, spec))
        if not specs:
            return _failure(
                "ats",
                self.config,
                "error",
                "unsupported ATS board URL(s): " + ", ".join(invalid),
            )
        terms = _all_terms(self.config)
        discovered, listing_state = self._scan_ats_boards(specs, terms, result)
        result["discovered_count"] = len(discovered)
        detail_state = self._capture_details("ats", discovered, result)
        self._finalize(result, listing_state, detail_state)
        if invalid:
            note = "ignored unsupported ATS board URL(s): " + ", ".join(invalid)
            result["error"] = f"{result['error']}; {note}" if result["error"] else note
        return result

    def _scan_listings(self, source_id, listing_urls, result, matcher):
        discovered: list[str] = []
        seen: set[str] = set()
        attempted = captured = blocked = 0
        errors: list[str] = []
        for url in listing_urls:
            if self._stopped():
                break
            attempted += 1
            result["listing_urls"].append(url)
            capture = self._capture(source_id, "listing", url)
            if capture["error"]:
                errors.append(capture["error"])
            if capture["blocked"]:
                blocked += 1
            if not capture["usable"]:
                continue
            captured += 1
            for link in capture["page"]["links"]:
                if self._stopped():
                    break
                self._collect(matcher(source_id, link.get("url", "")), link["url"], discovered, seen)
        return discovered, {
            "attempted": attempted,
            "captured": captured,
            "blocked": blocked,
            "errors": errors,
        }

    def _scan_ats_boards(self, specs, terms, result):
        matched: list[str] = []
        others: list[str] = []
        seen: set[str] = set()
        attempted = captured = blocked = 0
        errors: list[str] = []
        for board_url, spec in specs:
            if self._stopped():
                break
            attempted += 1
            result["listing_urls"].append(board_url)
            capture = self._capture("ats", "listing", board_url)
            if capture["error"]:
                errors.append(capture["error"])
            if capture["blocked"]:
                blocked += 1
            if not capture["usable"]:
                continue
            captured += 1
            self._collect_ats_links(
                capture["page"]["links"], spec, terms, matched, others, seen
            )
        return matched + others, {
            "attempted": attempted,
            "captured": captured,
            "blocked": blocked,
            "errors": errors,
        }

    def _collect_ats_links(self, links, spec, terms, matched, others, seen) -> None:
        for link in links:
            if self._stopped():
                return
            url = link.get("url", "")
            if not _ats_detail_url(spec, url):
                continue
            role_hit = _role_matches(link.get("text", ""), url, terms)
            self._collect(True, url, matched if role_hit else others, seen)

    @staticmethod
    def _collect(matches: bool, url: str, discovered: list[str], seen: set[str]) -> None:
        if not matches:
            return
        key = normalize_job_url(url)
        if not key or key in seen:
            return
        seen.add(key)
        discovered.append(url)

    def _capture_details(self, source_id: str, discovered: list[str], result: dict):
        limit = _result_limit(self.config)
        attempted = captured = blocked = 0
        errors: list[str] = []
        for url in discovered[:limit]:
            if self._stopped():
                break
            attempted += 1
            capture = self._capture(source_id, "detail", url)
            if capture["error"]:
                errors.append(capture["error"])
            if capture["blocked"]:
                blocked += 1
            if capture["usable"]:
                captured += 1
        result["captured_count"] = captured
        return {
            "attempted": attempted,
            "captured": captured,
            "blocked": blocked,
            "errors": errors,
        }

    def _finalize(self, result: dict, listing_state: dict, detail_state: dict) -> None:
        if self._stopped():
            result["status"] = "error"
            result["error"] = self.fatal or "cancelled"
            return
        if result["captured_count"] > 0:
            result["status"] = "ok"
            return
        if listing_state["captured"] == 0:
            self._fail_without_listing(result, listing_state)
            return
        if result["discovered_count"] == 0:
            result["status"] = "empty"
            self._note_blocked_listings(result, listing_state)
            return
        self._fail_capture(result, listing_state, detail_state)

    @staticmethod
    def _note_blocked_listings(result: dict, listing_state: dict) -> None:
        if not listing_state["blocked"]:
            return
        result["error"] = (
            f"{listing_state['blocked']} listing page(s) blocked; "
            "no verified job links on readable pages"
        )

    @staticmethod
    def _fail_capture(result: dict, listing_state: dict, detail_state: dict) -> None:
        result["error"] = (
            detail_state["errors"]
            or listing_state["errors"]
            or ["unspecified capture failure"]
        )[0]
        all_blocked = (
            detail_state["attempted"] > 0
            and detail_state["blocked"] == detail_state["attempted"]
        )
        result["status"] = "blocked" if all_blocked else "error"

    @staticmethod
    def _fail_without_listing(result: dict, listing_state: dict) -> None:
        all_blocked = (
            listing_state["attempted"] > 0
            and listing_state["blocked"] == listing_state["attempted"]
        )
        result["status"] = "blocked" if all_blocked else "error"
        result["error"] = (listing_state["errors"] or ["listing page was not readable"])[0]

    def _capture(self, source_id: str, kind: str, url: str) -> dict:
        if self._stopped():
            return {"page": None, "blocked": "", "error": "cancelled", "usable": False}
        self.page_seq += 1
        page_id = f"{source_id}-{kind}-{self.page_seq}"
        temp_dir = self.artifact_dir / f".tmp-{page_id}"
        try:
            raw = self.session.fetch(url, temp_dir)
        except (BrowserSessionClosedError, BrowserUnavailableError):
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        except UnsafeUrlError as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"page": None, "blocked": "", "error": f"unsafe URL: {exc}", "usable": False}
        except BrowserError as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"page": None, "blocked": "", "error": str(exc), "usable": False}
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"page": None, "blocked": "", "error": f"fetch failed: {exc}", "usable": False}
        try:
            page, _ = self._archive_page(
                temp_dir, page_id, source_id, kind, url, raw
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        blocked = _blocked_reason(
            raw.get("status"), page["url"], page["title"], page["text"]
        )
        error = blocked or _http_error_reason(raw.get("status"))
        return {
            "page": page,
            "blocked": blocked,
            "error": error,
            "usable": not error,
        }

    def _archive_page(self, temp_dir, page_id, source_id, kind, requested_url, raw) -> tuple[dict, list]:
        moved = self._move_artifacts(temp_dir, page_id, raw)
        page = {
            "id": page_id,
            "source_id": source_id,
            "kind": kind,
            "url": raw.get("final_url") or requested_url,
            "title": raw.get("title") or "",
            "text": raw.get("text") or "",
            "fetched_at": raw.get("fetched_at") or _utc_now(),
            "links": raw.get("links") or [],
            "artifacts": moved,
        }
        self.pages.append(page)
        return page, moved

    def _move_artifacts(self, temp_dir, page_id: str, raw) -> list[dict]:
        moved: list[dict] = []
        for artifact in raw.get("artifacts") or []:
            name = Path(str(artifact.get("name", ""))).name
            source = Path(temp_dir) / name
            if not name or not source.is_file():
                continue
            flat_name = self._flat_name(page_id, name)
            shutil.move(str(source), str(self.artifact_dir / flat_name))
            entry = {
                "name": flat_name,
                "media_type": artifact.get("media_type") or "application/octet-stream",
            }
            moved.append(entry)
            self.artifacts.append(entry)
        return moved

    def _flat_name(self, page_id: str, name: str) -> str:
        base = f"{page_id}-{name}"
        candidate = base
        counter = 2
        while (self.artifact_dir / candidate).exists():
            candidate = f"{page_id}-{counter}-{name}"
            counter += 1
        return candidate


def run_search(
    session,
    config: dict,
    artifact_dir: Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Acquire visible listing and detail pages for the selected sources.

    ``session`` is an existing ``BrowserSession`` bound to the caller's browser
    thread. ``cancelled`` is polled before every page fetch; cancellation stops
    new navigation immediately and marks unfinished sources as errors.
    """
    return _SearchRun(session, config, artifact_dir, cancelled).run()

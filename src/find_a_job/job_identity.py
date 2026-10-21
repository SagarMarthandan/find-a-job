"""Stable posting URL identity, migrated from the legacy job_identity module.

Only the pure normalization code is carried over. Nothing here reads personal
exports, config or credentials, so it is safe to import from the search path.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_TRACKING_KEYS = {
    "campaign",
    "campaignid",
    "fbclid",
    "from",
    "gh_src",
    "gclid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "pagenum",
    "position",
    "ref",
    "referrer",
    "refid",
    "source",
    "source_type",
    "sourcetype",
    "src",
    "tracking",
    "trackingid",
    "tracking_id",
    "trk",
}
_TRACKING_PREFIXES = ("utm_", "trk_", "ref_", "tracking_")
_INDEED_DOMAINS = (
    "indeed.co.uk", "indeed.com.au", "indeed.co.in", "indeed.co.jp",
    "indeed.com.br", "indeed.com.mx", "indeed.com.sg", "indeed.com.hk",
    "indeed.co.kr", "indeed.com.tr", "indeed.com.ar",
    "indeed.de", "indeed.fr", "indeed.it", "indeed.es", "indeed.nl",
    "indeed.at", "indeed.ch", "indeed.be", "indeed.ie", "indeed.ca",
    "indeed.pl", "indeed.se", "indeed.dk", "indeed.no", "indeed.fi",
    "indeed.pt", "indeed.com",
)


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _is_indeed_host(host: str) -> bool:
    return any(_host_is(host, domain) for domain in _INDEED_DOMAINS)


def _is_tracking_key(key: str) -> bool:
    folded = key.casefold()
    return folded in _TRACKING_KEYS or folded.startswith(_TRACKING_PREFIXES)


def extract_linkedin_job_id(url: str) -> int:
    """Return LinkedIn's numeric posting ID, or 0 when the URL has none."""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return 0
    host = parsed.hostname or ""
    if not _host_is(host.casefold(), "linkedin.com"):
        return 0

    match = re.search(r"(?:^|[-/])(\d{6,})(?:/|$)", parsed.path)
    if match:
        return int(match.group(1))

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in {"currentjobid", "jobid"} and value.isdigit():
            return int(value)
    return 0


def _split_url(raw: str):
    try:
        return urlsplit(raw)
    except ValueError:
        return None


def _normalized_host(parsed) -> str:
    host = (parsed.hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def _non_tracking_pairs(parsed) -> list[tuple[str, str]]:
    return [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]


def _linkedin_key(host: str, raw: str, path: str) -> str:
    if not _host_is(host, "linkedin.com"):
        return ""
    job_id = extract_linkedin_job_id(raw)
    if job_id:
        return f"linkedin:{job_id}"
    return f"https://{host}{path}"


def _indeed_key(host: str, pairs: list[tuple[str, str]]) -> str:
    if not _is_indeed_host(host):
        return ""
    jk = next(
        (value for key, value in pairs if key.casefold() == "jk" and value),
        "",
    )
    return f"indeed:{jk}" if jk else ""


def _generic_key(host: str, path: str, pairs: list[tuple[str, str]]) -> str:
    pairs.sort(key=lambda pair: (pair[0].casefold(), pair[0], pair[1]))
    query = urlencode(pairs)
    return f"https://{host}{path}" + (f"?{query}" if query else "")


def normalize_job_url(url: str) -> str:
    """Normalize host/scheme and remove tracking, preserving exact posting IDs.

    LinkedIn numeric job IDs and Indeed ``jk`` IDs receive canonical keys.
    Other URLs retain case-sensitive paths and every non-tracking query pair.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = _split_url(raw)
    if parsed is None:
        return raw.rstrip("/")
    host = _normalized_host(parsed)
    if not host:
        return raw.rstrip("/")
    path = parsed.path.rstrip("/")

    linkedin = _linkedin_key(host, raw, path)
    if linkedin:
        return linkedin
    pairs = _non_tracking_pairs(parsed)
    indeed = _indeed_key(host, pairs)
    if indeed:
        return indeed
    return _generic_key(host, path, pairs)

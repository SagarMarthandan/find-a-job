from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Iterator

CONTEXT_SCHEMA_VERSION = 1
EXTRACTOR_IMPLEMENTATION_VERSION = "1"
_PARSER_DISTRIBUTIONS = ("pypdf", "python-docx", "openpyxl", "python-pptx", "striprtf")
_MAX_SOURCE_TEXT = 1_000_000


class ContextUnavailableError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extractor_fingerprint() -> str:
    versions = []
    for distribution in _PARSER_DISTRIBUTIONS:
        try:
            installed = version(distribution)
        except PackageNotFoundError:
            installed = "unavailable"
        versions.append(f"{distribution}={installed}")
    return f"{EXTRACTOR_IMPLEMENTATION_VERSION};" + ";".join(versions)


@contextmanager
def _cache_connection(data_dir: Path) -> Iterator[sqlite3.Connection]:
    directory = data_dir / "cache"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / "cache.sqlite3"
    conn = sqlite3.connect(path, timeout=15)
    try:
        os.chmod(path, 0o600)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS extraction_cache (
                cache_key TEXT PRIMARY KEY,
                content_sha256 TEXT NOT NULL,
                suffix TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                text_json TEXT NOT NULL,
                text_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS context_cache (
                intake_id TEXT PRIMARY KEY,
                revision TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            """
        )
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cache_key(content_sha256: str, suffix: str, extractor_version: str) -> str:
    value = _canonical_json([content_sha256, suffix, extractor_version]).encode("utf-8")
    return _sha256(value)


def _valid_extraction(row: sqlite3.Row, content_sha256: str, suffix: str, extractor_version: str) -> str | None:
    if (row["content_sha256"], row["suffix"], row["extractor_version"]) != (content_sha256, suffix, extractor_version):
        return None
    try:
        text = json.loads(row["text_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(text, str) or not text.strip() or _sha256(text.encode("utf-8")) != row["text_sha256"]:
        return None
    return text


def extract_cached(
    data_dir: Path,
    data: bytes,
    suffix: str,
    extractor: Callable[[bytes, str], str],
) -> dict[str, Any]:
    suffix = suffix.lower()
    content_sha256 = _sha256(data)
    extractor_version = _extractor_fingerprint()
    cache_key = _cache_key(content_sha256, suffix, extractor_version)
    with _cache_connection(data_dir) as conn:
        row = conn.execute("SELECT * FROM extraction_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if row:
            text = _valid_extraction(row, content_sha256, suffix, extractor_version)
            if text is not None:
                return {"text": text, "cache_hit": True, "content_sha256": content_sha256, "extractor_version": extractor_version}
            conn.execute("DELETE FROM extraction_cache WHERE cache_key=?", (cache_key,))
    text = extractor(data, suffix)
    if not isinstance(text, str):
        raise TypeError("Source extractor must return text")
    if not text.strip():
        return {"text": text, "cache_hit": False, "content_sha256": content_sha256, "extractor_version": extractor_version}
    text_sha256 = _sha256(text.encode("utf-8"))
    with _cache_connection(data_dir) as conn:
        conn.execute(
            "INSERT INTO extraction_cache VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET "
            "content_sha256=excluded.content_sha256,suffix=excluded.suffix,extractor_version=excluded.extractor_version,"
            "text_json=excluded.text_json,text_sha256=excluded.text_sha256",
            (cache_key, content_sha256, suffix, extractor_version, json.dumps(text, ensure_ascii=False), text_sha256),
        )
    return {"text": text, "cache_hit": False, "content_sha256": content_sha256, "extractor_version": extractor_version}


@contextmanager
def _tasks_readonly(data_dir: Path) -> Iterator[sqlite3.Connection]:
    path = (data_dir / "tasks.sqlite3").resolve()
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error as exc:
        raise ContextUnavailableError("Local task database is unavailable; run the application once to initialize this workspace.", 404) from exc
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        yield conn
    except sqlite3.Error as exc:
        raise ContextUnavailableError("Could not read the local intake state.") from exc
    finally:
        conn.close()


def _source_text_budget(sources: list[Any]) -> int:
    total = sum(
        len(source.get("text", ""))
        for source in sources
        if isinstance(source, dict) and source.get("kind") != "github" and source.get("status") == "ready" and isinstance(source.get("text", ""), str)
    )
    return _MAX_SOURCE_TEXT - total


def _github_task_text(task: sqlite3.Row) -> str | None:
    try:
        text = json.loads(task["result_json"] or "{}").get("text")
    except (json.JSONDecodeError, AttributeError):
        return None
    return text if isinstance(text, str) else None


def _refresh_github_source(conn: sqlite3.Connection, source: dict[str, Any], remaining: int) -> int:
    github_id = source.get("github_task_id")
    if not github_id:
        return remaining
    task = conn.execute("SELECT status,result_json,error FROM tasks WHERE id=?", (github_id,)).fetchone()
    if task and task["status"] == "completed":
        text = _github_task_text(task)
        if text is None or len(text) > remaining:
            source.update(status="failed", text="", error="GitHub source text is unavailable or exceeds the 1 MB aggregate source-text limit")
            return remaining
        source.update(status="ready", text=text, error=None)
        return remaining - len(text)
    if task and task["status"] in {"failed", "cancelled"}:
        source.update(status="failed", text="", error=task["error"] or "GitHub retrieval cancelled")
        return remaining
    source.update(status="pending", text="")
    return remaining


def _context_source(source: dict[str, Any]) -> dict[str, Any]:
    text = source.get("text", "")
    if not isinstance(text, str):
        raise ContextUnavailableError("Intake source text is malformed.")
    item = {
        "id": source.get("id"),
        "kind": source.get("kind"),
        "label": source.get("label"),
        "format": source.get("format"),
        "status": source.get("status"),
        "text": text,
        "content_sha256": _sha256(text.encode("utf-8")),
        "content_hash_basis": "snapshot_text_utf8",
    }
    if source.get("content_sha256"):
        item["original_content_sha256"] = source["content_sha256"]
    for key in ("url", "error"):
        if source.get(key) is not None:
            item[key] = source[key]
    return item


def _source_snapshot(conn: sqlite3.Connection, raw_sources: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_sources, list):
        raise ContextUnavailableError("Intake source records are malformed.")
    remaining = _source_text_budget(raw_sources)
    sources = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise ContextUnavailableError("Intake source records are malformed.")
        source = dict(raw)
        remaining = _refresh_github_source(conn, source, remaining)
        sources.append(_context_source(source))
    return sources


def _decode_context(row: sqlite3.Row, revision: str) -> dict[str, Any] | None:
    payload_json = row["payload_json"]
    if not isinstance(payload_json, str) or row["revision"] != revision or _sha256(payload_json.encode("utf-8")) != row["payload_sha256"]:
        return None
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _matches_context(payload: dict[str, Any], intake_id: str, revision: str, profile: dict[str, Any], sources: list[dict[str, Any]]) -> bool:
    if payload.get("schema_version") != CONTEXT_SCHEMA_VERSION:
        return False
    if payload.get("intake_id") != intake_id or payload.get("revision") != revision:
        return False
    return payload.get("profile") == profile and payload.get("sources") == sources


def _valid_cache_stamp(payload: dict[str, Any]) -> bool:
    cache = payload.get("cache")
    if not isinstance(cache, dict) or not isinstance(cache.get("hit"), bool) or not isinstance(cache.get("created_at"), str):
        return False
    try:
        datetime.fromisoformat(cache["created_at"])
    except ValueError:
        return False
    return True


def _valid_context(row: sqlite3.Row, intake_id: str, revision: str, profile: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    payload = _decode_context(row, revision)
    if payload is None or not _matches_context(payload, intake_id, revision, profile, sources) or not _valid_cache_stamp(payload):
        return None
    return payload


def _approved_intake_state(data_dir: Path, intake_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with _tasks_readonly(data_dir) as conn:
        row = conn.execute("SELECT kind,status,input_json,result_json FROM tasks WHERE id=?", (intake_id,)).fetchone()
        if row is None:
            raise ContextUnavailableError("Intake was not found.", 404)
        if row["kind"] != "profile":
            raise ContextUnavailableError("Task is not an intake profile.", 404)
        try:
            input_data = json.loads(row["input_json"])
            profile = json.loads(row["result_json"] or "null")
        except (json.JSONDecodeError, TypeError) as exc:
            raise ContextUnavailableError("Intake profile data is malformed.") from exc
        if not isinstance(input_data, dict) or not input_data.get("intake"):
            raise ContextUnavailableError("Task is not an intake profile.", 404)
        if row["status"] != "completed":
            raise ContextUnavailableError("Intake must be completed before context is available.")
        if not isinstance(profile, dict) or profile.get("review_status") != "approved":
            raise ContextUnavailableError("Intake profile must be reviewed and approved before context is available.")
        sources = _source_snapshot(conn, input_data.get("sources"))
    return profile, sources


def _context_revision(intake_id: str, profile: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    value = {"schema_version": CONTEXT_SCHEMA_VERSION, "intake_id": intake_id, "profile": profile, "sources": sources}
    return _sha256(_canonical_json(value).encode("utf-8"))


def _read_context_cache(conn: sqlite3.Connection, intake_id: str, revision: str, profile: dict[str, Any], sources: list[dict[str, Any]], refresh: bool) -> dict[str, Any] | None:
    cached = conn.execute("SELECT * FROM context_cache WHERE intake_id=?", (intake_id,)).fetchone()
    if cached is None or refresh:
        return None
    payload = _valid_context(cached, intake_id, revision, profile, sources)
    if payload is None:
        return None
    return {**payload, "cache": {"hit": True, "created_at": payload["cache"]["created_at"]}}


def _write_context_cache(conn: sqlite3.Connection, intake_id: str, revision: str, profile: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "intake_id": intake_id,
        "revision": revision,
        "profile": profile,
        "sources": sources,
        "cache": {"hit": False, "created_at": datetime.now(UTC).isoformat()},
    }
    payload_json = _canonical_json(payload)
    conn.execute(
        "INSERT INTO context_cache VALUES(?,?,?,?) ON CONFLICT(intake_id) DO UPDATE SET "
        "revision=excluded.revision,payload_json=excluded.payload_json,payload_sha256=excluded.payload_sha256",
        (intake_id, revision, payload_json, _sha256(payload_json.encode("utf-8"))),
    )
    return payload


def context_cached(data_dir: Path, intake_id: str, refresh: bool = False) -> dict[str, Any]:
    profile, sources = _approved_intake_state(data_dir, intake_id)
    revision = _context_revision(intake_id, profile, sources)
    with _cache_connection(data_dir) as conn:
        payload = _read_context_cache(conn, intake_id, revision, profile, sources, refresh)
        if payload is not None:
            return payload
        return _write_context_cache(conn, intake_id, revision, profile, sources)



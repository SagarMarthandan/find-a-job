from __future__ import annotations

import json
import os
import queue
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from .intake import extract as extract_source

DISCLAIMER = (
    "Output quality depends on the model you select, the source evidence and your configuration. "
    "Different models may produce different extraction, matching and resume-writing results. "
    "Review generated content for factual accuracy and suitability before use. "
    "The pipeline does not guarantee ATS acceptance, interviews or employment."
)
DATA_DIR = Path(os.path.expandvars(os.environ.get("FIND_A_JOB_DATA_DIR", "~/.local/share/find-a-job"))).expanduser().resolve()
TOKEN_FILE = DATA_DIR / "session-token"
DB_FILE = DATA_DIR / "tasks.sqlite3"
WEB_FILE = Path(__file__).parent / "web" / "index.html"
MAX_UPLOAD = 10 * 1024 * 1024
MAX_INTAKE = 50 * 1024 * 1024
MAX_SOURCES = 25
MAX_SOURCE_TEXT = 1_000_000


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["browser", "profile", "resume"]
    title: str = Field(min_length=1, max_length=240)
    input: dict[str, Any]


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=200)
    result: dict[str, Any]


class FailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=200)
    error: str = Field(min_length=1, max_length=4000)


def now() -> str:
    return datetime.now(UTC).isoformat()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _initialize() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DATA_DIR, 0o700)
    if not TOKEN_FILE.exists():
        try:
            fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    os.chmod(TOKEN_FILE, 0o600)
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                title TEXT NOT NULL, input_json TEXT NOT NULL, result_json TEXT,
                error TEXT, worker_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                name TEXT NOT NULL, media_type TEXT NOT NULL, path TEXT NOT NULL,
                PRIMARY KEY(task_id,name)
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        conn.execute("UPDATE tasks SET status=CASE WHEN kind='browser' THEN 'queued' ELSE 'waiting_for_harness' END, worker_id=NULL, updated_at=? WHERE status='running'", (now(),))
        conn.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('last_seen','')")


def _task(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"], "kind": row["kind"], "status": row["status"], "title": row["title"],
        "input": json.loads(row["input_json"]), "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _touch() -> None:
    with _db() as conn:
        conn.execute("UPDATE metadata SET value=? WHERE key='last_seen'", (now(),))


def _valid_http_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username and not parts.password and (port is None or 1 <= port <= 65535)


def _github_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    segments = [part for part in parts.path.split("/") if part]
    return parts.scheme == "https" and parts.hostname == "github.com" and port is None and not parts.username and not parts.password and not parts.query and not parts.fragment and len(segments) in {1, 2} and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in segments)

def _browser_input(data: dict[str, Any]) -> dict[str, Any]:
    url, purpose = data.get("url"), data.get("purpose")
    if not _valid_http_url(url) or purpose not in {"scrape", "research", "github"}:
        raise HTTPException(422, "Browser input requires a public http(s) url and purpose scrape, research, or github")
    return {"url": url, "purpose": purpose}




def _resume_input(data: dict[str, Any]) -> dict[str, Any]:
    profile_id, job_id = data.get("profile_task_id"), data.get("job_task_id")
    if not isinstance(profile_id, str) or not isinstance(job_id, str):
        raise HTTPException(422, "Resume input requires profile_task_id and job_task_id")
    with _db() as conn:
        profile = conn.execute("SELECT * FROM tasks WHERE id=?", (profile_id,)).fetchone()
        job = conn.execute("SELECT * FROM tasks WHERE id=?", (job_id,)).fetchone()
    if not profile or profile["kind"] != "profile" or profile["status"] != "completed":
        raise HTTPException(422, "profile_task_id must reference a completed profile task")
    profile_result = json.loads(profile["result_json"] or "{}")
    if json.loads(profile["input_json"]).get("intake") and profile_result.get("review_status") != "approved":
        raise HTTPException(422, "Intake profile must be reviewed and approved before resume creation")
    is_research = job and job["kind"] == "browser" and job["status"] == "completed" and json.loads(job["input_json"]).get("purpose") == "research"
    if not is_research:
        raise HTTPException(422, "job_task_id must reference a completed browser research task")
    return {"profile_task_id": profile_id, "job_task_id": job_id}


def _validate_task_input(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "profile":
        raise HTTPException(422, "Create profiles through POST /api/intakes with source files, notes, or GitHub URLs")
    validators = {"browser": _browser_input, "resume": _resume_input}
    validator = validators.get(kind)
    if validator is None:
        raise HTTPException(422, "Unsupported task kind")
    return validator(data)


def _create_task(kind: str, title: str, data: dict[str, Any]) -> dict[str, Any]:
    value = _validate_task_input(kind, data)
    task_id, stamp = uuid.uuid4().hex, now()
    github_id = uuid.uuid4().hex if kind == "profile" and value.get("github_url") else None
    status = "queued" if kind == "browser" else "waiting_for_harness"
    with _db() as conn:
        conn.execute("INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                     (task_id, kind, status, title.strip(), json.dumps(value, ensure_ascii=False), stamp, stamp))
        if github_id:
            github_input = {"url": value["github_url"], "purpose": "github"}
            conn.execute("INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?, 'browser','queued',?,?,?,?)",
                         (github_id, f"GitHub evidence: {title.strip()}", json.dumps(github_input), stamp, stamp))
            value["github_task_id"] = github_id
            conn.execute("UPDATE tasks SET input_json=? WHERE id=?", (json.dumps(value, ensure_ascii=False), task_id))
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if github_id:
        BROWSER_QUEUE.put(github_id)
    if kind == "browser":
        BROWSER_QUEUE.put(task_id)
    return _task(row)  # type: ignore[return-value]

def _source_texts(input_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if input_data.get("intake"):
        sources = input_data["sources"]
        texts = [source["text"] for source in sources if source.get("status") == "ready" and isinstance(source.get("text"), str)]
        return sources, texts
    source_texts = [input_data["text"]]
    github_id = input_data.get("github_task_id")
    if github_id:
        with _db() as conn:
            github = conn.execute("SELECT status,result_json FROM tasks WHERE id=?", (github_id,)).fetchone()
        if github and github["status"] == "completed":
            source_texts.append(json.loads(github["result_json"])["text"])
    return [], source_texts


def _create_intake(title: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    task_id, stamp = uuid.uuid4().hex, now()
    for source in sources:
        if source["kind"] == "github":
            source["github_task_id"] = uuid.uuid4().hex
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?, 'profile','waiting_for_harness',?,?,?,?)",
                     (task_id, title, json.dumps({"intake": True, "sources": sources}, ensure_ascii=False), stamp, stamp))
        for source in sources:
            github_id = source.get("github_task_id")
            if github_id:
                browser_input = {"url": source["url"], "purpose": "github"}
                conn.execute("INSERT INTO tasks(id,kind,status,title,input_json,created_at,updated_at) VALUES(?, 'browser','queued',?,?,?,?)",
                             (github_id, f"GitHub evidence: {source['label']}", json.dumps(browser_input), stamp, stamp))
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.commit()
    for source in sources:
        if source.get("github_task_id"):
            BROWSER_QUEUE.put(source["github_task_id"])
    return _task(row)  # type: ignore[return-value]


def _intake_source_snapshot(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    sources = input_data["sources"]
    remaining = MAX_SOURCE_TEXT - sum(len(source.get("text", "")) for source in sources if source.get("kind") != "github" and source.get("status") == "ready")
    for source in sources:
        github_id = source.get("github_task_id")
        if not github_id:
            continue
        with _db() as conn:
            task = conn.execute("SELECT status,result_json,error FROM tasks WHERE id=?", (github_id,)).fetchone()
        if task and task["status"] == "completed":
            text = json.loads(task["result_json"])["text"]
            if len(text) > remaining:
                source.update(status="failed", text="", error="GitHub source exceeds the 1 MB aggregate source-text limit")
            else:
                source.update(status="ready", text=text, error=None)
                remaining -= len(text)
        elif task and task["status"] in {"failed", "cancelled"}:
            source.update(status="failed", error=task["error"] or "GitHub retrieval cancelled")
        else:
            source.update(status="pending")
    return sources


def _validate_intake_result(result: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    documents = {source["id"]: source for source in sources if source.get("status") == "ready"}
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        raise HTTPException(422, "Profile must include source evidence")
    for item in evidence:
        if not isinstance(item, dict) or item.get("source") not in documents:
            raise HTTPException(422, "Evidence source must identify a ready source document ID")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or text.casefold() not in documents[item["source"]].get("text", "").casefold():
            raise HTTPException(422, "Evidence quote must occur in its specifically attributed source")
    output = _profile_output(result, [documents[item["source"]]["text"] for item in evidence])
    output["evidence"] = [{**item, "label": documents[item["source"]]["label"], "kind": documents[item["source"]]["kind"]} for item in output["evidence"]]
    output.update(review_status="pending", reviewed_at=None, acknowledged_source_errors=[])
    return output
def _register_artifact(conn: sqlite3.Connection, task_id: str, name: str, media_type: str, path: Path) -> dict[str, str]:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise HTTPException(422, "Invalid artifact name")
    target = path.resolve()
    if not target.is_file() or DATA_DIR not in target.parents:
        raise HTTPException(422, "Artifact must be a regular file under the private data directory")
    conn.execute("INSERT OR REPLACE INTO artifacts(task_id,name,media_type,path) VALUES(?,?,?,?)", (task_id, name, media_type, str(target)))
    return {"name": name, "media_type": media_type}

def _validate_profile_identity(value: dict[str, Any]) -> None:
    required = {"name", "skills", "experience", "projects", "education", "evidence"}
    if not required <= value.keys() or not isinstance(value["name"], str) or not value["name"].strip():
        raise HTTPException(422, "Profile result requires name, skills, experience, projects, education, and evidence")


def _validate_profile_lists(value: dict[str, Any]) -> None:
    for key in ("skills", "experience", "projects", "education"):
        if not isinstance(value[key], list) or any(not isinstance(item, str) or not item.strip() for item in value[key]):
            raise HTTPException(422, f"{key} must be an array of non-empty strings")
    for key in ("email", "phone"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            raise HTTPException(422, "email and phone must be strings when present")


def _validate_profile_fields(value: dict[str, Any]) -> None:
    _validate_profile_identity(value)
    _validate_profile_lists(value)


def _validated_evidence_item(item: Any, source_texts: list[str]) -> dict[str, Any]:
    valid_fields = isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k].strip() for k in ("id", "text", "source"))
    if not valid_fields:
        raise HTTPException(422, "Every evidence item needs non-empty id, text, and source")
    if not any(item["text"].casefold() in source.casefold() for source in source_texts):
        raise HTTPException(422, "Evidence text must quote the supplied CV or fetched GitHub evidence")
    return {"id": item["id"], "text": item["text"], "source": item["source"], "confirmed": False}


def _clean_profile_evidence(evidence: Any, source_texts: list[str]) -> list[dict[str, Any]]:
    if not isinstance(evidence, list) or not evidence:
        raise HTTPException(422, "Profile must include source evidence")
    seen: set[str] = set()
    cleaned = []
    for item in evidence:
        normalized = _validated_evidence_item(item, source_texts)
        if normalized["id"] in seen:
            raise HTTPException(422, "Evidence IDs must be unique")
        seen.add(normalized["id"])
        cleaned.append(normalized)
    return cleaned


def _validate_profile_claims(value: dict[str, Any], evidence: list[dict[str, Any]]) -> None:
    fields = ("skills", "experience", "projects", "education")
    claims = [value["name"], *(claim for key in fields for claim in value[key])]
    claims.extend(value[k] for k in ("email", "phone") if value.get(k))
    quotes = [item["text"].casefold() for item in evidence]
    if any(not any(claim.casefold() in quote for quote in quotes) for claim in claims):
        raise HTTPException(422, "Every profile fact must be directly supported by a supplied evidence quote")


def _profile_output(value: dict[str, Any], source_texts: list[str]) -> dict[str, Any]:
    _validate_profile_fields(value)
    evidence = _clean_profile_evidence(value["evidence"], source_texts)
    _validate_profile_claims(value, evidence)
    result = {"name": value["name"].strip(), **{k: value[k] for k in ("skills", "experience", "projects", "education")}, "evidence": evidence}
    result.update({k: value[k] for k in ("email", "phone") if value.get(k)})
    return result


def _validate_resume_contact(value: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    contact = value.get("contact")
    if not isinstance(contact, list) or any(not isinstance(item, str) for item in contact):
        raise HTTPException(422, "Resume contact must be an array of strings")
    allowed = {profile[k] for k in ("email", "phone") if profile.get(k)}
    if any(item not in allowed for item in contact):
        raise HTTPException(422, "Resume contact can only include contact values in the profile")
    return contact


def _validate_resume_item(item: Any, valid_ids: set[str]) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
        raise HTTPException(422, "Resume items require non-empty text")
    evidence_ids = item.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(i, str) or i not in valid_ids for i in evidence_ids):
        raise HTTPException(422, "Each resume item must cite existing profile evidence IDs")


def _validate_resume_section(section: Any, valid_ids: set[str]) -> None:
    if not isinstance(section, dict) or not isinstance(section.get("heading"), str) or not section["heading"].strip() or not isinstance(section.get("items"), list):
        raise HTTPException(422, "Each resume section requires a heading and items")
    for item in section["items"]:
        _validate_resume_item(item, valid_ids)


def _resume_output(value: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value.get("name"), str) or value["name"] != profile["name"]:
        raise HTTPException(422, "Resume name must exactly match the completed profile")
    contact = _validate_resume_contact(value, profile)
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        raise HTTPException(422, "Resume requires at least one section")
    valid_ids = {item["id"] for item in profile["evidence"]}
    for section in sections:
        _validate_resume_section(section, valid_ids)
    return {"name": value["name"], "contact": contact, "sections": sections}

def _render_pdf(task_id: str, resume: dict[str, Any]) -> dict[str, str]:
    from io import BytesIO
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=LETTER, title=resume["name"])
    styles = getSampleStyleSheet()
    story = [Paragraph(escape(resume["name"]), styles["Title"])]
    if resume["contact"]:
        story.extend([Paragraph(escape(" | ".join(resume["contact"])), styles["Normal"]), Spacer(1, 8)])
    for section in resume["sections"]:
        story.append(Paragraph(escape(section["heading"]), styles["Heading2"]))
        story.extend(Paragraph(escape(item["text"]), styles["BodyText"]) for item in section["items"])
        story.append(Spacer(1, 6))
    doc.build(story)
    directory = DATA_DIR / "artifacts" / task_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / "resume.pdf"
    destination.write_bytes(output.getvalue())
    return {"name": "resume.pdf", "media_type": "application/pdf"}


def _begin_browser(task_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row["status"] != "queued":
            return None
        conn.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (now(), task_id))
        return json.loads(row["input_json"])


def _visible_page_text(result: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(result, dict) or result.get("browser_visible") is not True:
        raise ValueError("Browser integration did not report a visible headed browser session")
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Browser returned no readable visible page text")
    return result, text


def _visible_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Browser returned invalid artifact metadata")
    artifacts = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("media_type"), str):
            artifacts.append(item)
    if not any(item["name"] == "browser.png" and item["media_type"] == "image/png" for item in artifacts):
        raise ValueError("Visible browser screenshot browser.png was not returned")
    return artifacts


def _visible_links(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [{"text": item["text"][:500], "url": item["url"][:2048]} for item in value[:200]
            if isinstance(item, dict) and isinstance(item.get("text"), str) and _valid_http_url(item.get("url"))]


def _normalize_browser_result(result: Any, requested_url: str) -> dict[str, Any]:
    page, text = _visible_page_text(result)
    artifacts = _visible_artifacts(page.get("artifacts", []))
    return {"url": page.get("url", requested_url), "title": str(page.get("title", ""))[:1000], "text": text[:60000],
            "links": _visible_links(page.get("links", [])), "artifacts": artifacts, "browser_visible": True}
def _save_browser_result(task_id: str, directory: Path, result: dict[str, Any]) -> None:
    with _db() as conn:
        current = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not current or current["status"] == "cancelled":
            return
        registered = [_register_artifact(conn, task_id, item["name"], item["media_type"], directory / item["name"]) for item in result["artifacts"]]
        result["artifacts"] = registered
        conn.execute("UPDATE tasks SET status='completed',result_json=?,updated_at=? WHERE id=? AND status='running'",
                     (json.dumps(result, ensure_ascii=False), now(), task_id))


def _fail_browser_task(task_id: str, error: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=? AND status!='cancelled'", (error[:4000], now(), task_id))


def _execute_browser(task_id: str, session: Any) -> None:
    payload = _begin_browser(task_id)
    if payload is None:
        return
    try:
        directory = DATA_DIR / "artifacts" / task_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = _normalize_browser_result(session.fetch(payload["url"], directory), payload["url"])
        _save_browser_result(task_id, directory, result)
    except Exception as exc:
        _fail_browser_task(task_id, str(exc))
BROWSER_QUEUE: queue.Queue[str | None] = queue.Queue()


def _browser_worker() -> None:
    try:
        from .browser import BrowserSession
        browser_import_error = None
    except Exception as exc:
        BrowserSession = None
        browser_import_error = f"Visible browser integration unavailable: {exc}"
    session = None
    while True:
        task_id = BROWSER_QUEUE.get()
        if task_id is None:
            break
        try:
            if browser_import_error:
                raise RuntimeError(browser_import_error)
            if session is None:
                session = BrowserSession(DATA_DIR)
            _execute_browser(task_id, session)
        except Exception as exc:
            with _db() as conn:
                conn.execute("UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=? AND status!='cancelled'", (str(exc)[:4000], now(), task_id))
    if session is not None:
        session.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _initialize()
    with _db() as conn:
        pending = conn.execute("SELECT id FROM tasks WHERE kind='browser' AND status='queued'").fetchall()
    thread = threading.Thread(target=_browser_worker, name="find-a-job-browser", daemon=True)
    thread.start()
    for item in pending:
        BROWSER_QUEUE.put(item["id"])
    yield
    BROWSER_QUEUE.put(None)
    thread.join(timeout=8)


app = FastAPI(title="find-a-job local service", lifespan=lifespan)


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0].strip("[]").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return JSONResponse(status_code=400, content={"detail": "Only localhost Host headers are accepted"})
    origin = request.headers.get("origin")
    if origin:
        parts = urlsplit(origin)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"} or parts.port not in {None, 8765}:
            return JSONResponse(status_code=403, content={"detail": "Only same-origin localhost requests are accepted"})
    return await call_next(request)


def require_token(request: Request) -> None:
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    try:
        expected = TOKEN_FILE.read_text().strip()
    except OSError:
        raise HTTPException(503, "Local session is not initialized")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Missing or invalid local session token")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/harness/heartbeat")
def harness_heartbeat(_: None = Depends(require_token)):
    _touch()
    with _db() as conn:
        last = conn.execute("SELECT value FROM metadata WHERE key='last_seen'").fetchone()[0]
    return {"connected": True, "last_seen": last}


@app.get("/", include_in_schema=False)
def dashboard():
    if not WEB_FILE.is_file():
        raise HTTPException(503, "Dashboard assets are not installed")
    return FileResponse(WEB_FILE, media_type="text/html")


@app.get("/api/state")
def state(_: None = Depends(require_token)):
    with _db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        tasks = []
        for row in rows:
            task_input = json.loads(row["input_json"])
            if row["kind"] == "profile" and task_input.get("intake"):
                _intake_source_snapshot(task_input)
                conn.execute("UPDATE tasks SET input_json=? WHERE id=?", (json.dumps(task_input, ensure_ascii=False), row["id"]))
                row = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            tasks.append(_task(row))
        last = conn.execute("SELECT value FROM metadata WHERE key='last_seen'").fetchone()[0]
    connected = bool(last and datetime.now(UTC) - datetime.fromisoformat(last) < timedelta(seconds=30))
    return {"tasks": tasks, "harness": {"connected": connected, "last_seen": last or None}, "disclaimer": DISCLAIMER}


@app.post("/api/tasks")
def create_task(body: TaskCreate, _: None = Depends(require_token)):
    return _create_task(body.kind, body.title, body.input)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, _: None = Depends(require_token)):
    with _db() as conn:
        conn.execute("UPDATE tasks SET status='cancelled',worker_id=NULL,error=NULL,updated_at=? WHERE id=? AND status NOT IN ('completed','failed','cancelled')", (now(), task_id))
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    if row["status"] == "cancelled" and row["kind"] == "browser":
        BROWSER_QUEUE.put(task_id)
    return _task(row)


async def _uploaded_source(upload: UploadFile, kind: str, directory: Path) -> tuple[dict[str, Any], int]:
    label = Path((upload.filename or "upload").replace("\\", "/")).name[:200]
    source = {"id": uuid.uuid4().hex, "kind": kind, "label": label, "format": Path(label).suffix.lower().lstrip("."), "status": "failed", "text": ""}
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        source["error"] = "File exceeds 10 MiB"
        return source, len(data)
    original = directory / f"{source['id']}{Path(label).suffix.lower()}"
    original.write_bytes(data)
    os.chmod(original, 0o600)
    try:
        if not data:
            raise ValueError("File is empty")
        source.update(status="ready", text=extract_source(data, Path(label).suffix))
    except Exception as exc:
        source["error"] = str(exc)[:1000]
    return source, len(data)


@app.post("/api/intakes")
async def create_intake(
    resume_files: list[UploadFile] = File(default=[]),
    catalog_files: list[UploadFile] = File(default=[]),
    github_urls: str = Form(default="[]"),
    notes: str = Form(default=""),
    title: str = Form(default="Candidate intake"),
    _: None = Depends(require_token),
):
    try:
        urls = json.loads(github_urls)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "github_urls must be a JSON array") from exc
    if not isinstance(urls, list) or any(not isinstance(url, str) or not _github_url(url) for url in urls):
        raise HTTPException(422, "github_urls must contain only public github.com/<owner> or github.com/<owner>/<repo> URLs")
    count = len(resume_files) + len(catalog_files) + len(urls) + bool(notes.strip())
    if count > MAX_SOURCES:
        raise HTTPException(413, "An intake may contain at most 25 sources")
    sources: list[dict[str, Any]] = []
    total = 0
    directory = DATA_DIR / "uploads" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for kind, files in (("resume", resume_files), ("catalog", catalog_files)):
        for upload in files:
            source, size = await _uploaded_source(upload, kind, directory)
            total += size
            if total > MAX_INTAKE:
                raise HTTPException(413, "Intake upload total exceeds 50 MiB")
            sources.append(source)
    if notes.strip():
        if len(notes) > 100_000:
            raise HTTPException(413, "Notes exceed 100000 characters")
        sources.append({"id": uuid.uuid4().hex, "kind": "notes", "label": "Candidate-entered notes", "format": "text", "status": "ready", "text": notes.strip()})
    if sum(len(source.get("text", "")) for source in sources if source["status"] == "ready") > MAX_SOURCE_TEXT:
        raise HTTPException(413, "Intake extracted text exceeds the 1 MB aggregate limit")
    for url in urls:
        sources.append({"id": uuid.uuid4().hex, "kind": "github", "label": url, "format": "html", "status": "pending", "text": "", "url": url})
    if not any(source["kind"] in {"resume", "notes"} and source["status"] == "ready" and source["text"].strip() for source in sources):
        visible_sources = [{key: value for key, value in source.items() if key != "text"} for source in sources]
        raise HTTPException(422, {"message": "At least one readable resume or non-empty notes source is required", "sources": visible_sources})
    return _create_intake(title.strip()[:240] or "Candidate intake", sources)


@app.post("/api/intakes/{task_id}/review")
def review_intake(task_id: str, body: dict[str, Any], _: None = Depends(require_token)):
    result_value = body.get("result")
    if not isinstance(result_value, dict):
        raise HTTPException(422, "Review requires a full profile result object")
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row["kind"] != "profile" or not json.loads(row["input_json"]).get("intake"):
            raise HTTPException(404, "Intake task not found")
        if row["status"] != "completed":
            raise HTTPException(409, "Only a completed intake can be reviewed")
        input_data = json.loads(row["input_json"])
        sources = _intake_source_snapshot(input_data)
        result = _validate_intake_result(result_value, sources)
        conn.execute("UPDATE tasks SET input_json=?,result_json=?,updated_at=? WHERE id=? AND status='completed'",
                     (json.dumps(input_data, ensure_ascii=False), json.dumps(result, ensure_ascii=False), now(), task_id))
        saved = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.commit()
    return _task(saved)


@app.post("/api/intakes/{task_id}/approve")
def approve_intake(task_id: str, body: dict[str, Any], _: None = Depends(require_token)):
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row["kind"] != "profile" or not json.loads(row["input_json"]).get("intake"):
            raise HTTPException(404, "Intake task not found")
        if row["status"] != "completed":
            raise HTTPException(409, "Only a completed intake can be approved")
        input_data, result = json.loads(row["input_json"]), json.loads(row["result_json"] or "{}")
        sources = _intake_source_snapshot(input_data)
        if any(source["status"] == "pending" for source in sources):
            raise HTTPException(409, "Wait for all GitHub sources to finish")
        result = _validate_intake_result(result, sources)
        failed_ids = [source["id"] for source in sources if source["status"] == "failed"]
        if failed_ids and body.get("acknowledge_source_errors") is not True:
            raise HTTPException(422, {"message": "Explicitly acknowledge failed source exclusions", "source_ids": failed_ids})
        result.update(review_status="approved", reviewed_at=now(), acknowledged_source_errors=failed_ids)
        conn.execute("UPDATE tasks SET input_json=?,result_json=?,updated_at=? WHERE id=? AND status='completed'",
                     (json.dumps(input_data, ensure_ascii=False), json.dumps(result, ensure_ascii=False), now(), task_id))
        saved = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.commit()
    return _task(saved)
PROFILE_SCHEMA = {"name": "string", "email": "string optional", "phone": "string optional", "skills": ["string"], "experience": ["string"], "projects": ["string"], "education": ["string"], "evidence": [{"id": "unique string", "text": "verbatim quote", "source": "source document id for intake"}]}
RESUME_SCHEMA = {"name": "string from profile", "contact": ["profile contact strings only"], "sections": [{"heading": "string", "items": [{"text": "string", "evidence_ids": ["existing profile evidence ids"]}]}]}


def _claim_context(row: sqlite3.Row, model: str | None) -> dict[str, Any]:
    task = _task(row)
    task["output_schema"] = PROFILE_SCHEMA if row["kind"] == "profile" else RESUME_SCHEMA
    task["requested_model"] = model or "host-managed/unverified"
    if row["kind"] == "profile":
        if task["input"].get("intake"):
            sources = _intake_source_snapshot(task["input"])
            task["input"]["sources"] = sources
            task["input"]["source_documents"] = [{"id": item["id"], "kind": item["kind"], "label": item["label"], "text": item.get("text", ""), "status": item["status"], **({"error": item["error"]} if item.get("error") else {}), **({"url": item["url"]} if item.get("url") else {})} for item in sources]
            task["input"]["source_evidence"] = [item["text"] for item in sources if item["status"] == "ready"]
        else:
            _, source_texts = _source_texts(task["input"])
            task["input"]["source_evidence"] = source_texts
    elif row["kind"] == "resume":
        ids = task["input"]
        with _db() as conn:
            profile = conn.execute("SELECT result_json FROM tasks WHERE id=?", (ids["profile_task_id"],)).fetchone()
            job = conn.execute("SELECT result_json FROM tasks WHERE id=?", (ids["job_task_id"],)).fetchone()
        task["input"] = {"profile": json.loads(profile[0]), "job_description": json.loads(job[0])}
    return task


@app.post("/api/harness/claim")
def claim(body: ClaimRequest, _: None = Depends(require_token)):
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute("SELECT * FROM tasks WHERE kind IN ('profile','resume') AND status='waiting_for_harness' ORDER BY created_at").fetchall()
        row = None
        for candidate in candidates:
            data = json.loads(candidate["input_json"])
            github_ids = ([source["github_task_id"] for source in data.get("sources", []) if source.get("github_task_id")] if candidate["kind"] == "profile" and data.get("intake") else ([data["github_task_id"]] if candidate["kind"] == "profile" and data.get("github_task_id") else []))
            if any((github := conn.execute("SELECT status FROM tasks WHERE id=?", (github_id,)).fetchone()) and github["status"] not in {"completed", "failed", "cancelled"} for github_id in github_ids):
                continue
            row = candidate
            break
        if not row:
            conn.commit()
            _touch()
            return {"task": None}
        conn.execute("UPDATE tasks SET status='running',worker_id=?,updated_at=? WHERE id=? AND status='waiting_for_harness'", (body.worker_id, now(), row["id"]))
        claimed = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
        conn.commit()
    _touch()
    return {"task": _claim_context(claimed, body.model)}


@app.post("/api/harness/tasks/{task_id}/complete")
def complete(task_id: str, body: CompleteRequest, _: None = Depends(require_token)):
    with _db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        if row["status"] != "running" or row["worker_id"] != body.worker_id:
            raise HTTPException(409, "Task is not running under this worker_id")
        if row["kind"] == "profile":
            context = _claim_context(row, None)["input"]
            if context.get("intake"):
                result = _validate_intake_result(body.result, context["sources"])
            else:
                result = _profile_output(body.result, context["source_evidence"])
            artifacts = []
        elif row["kind"] == "resume":
            context = _claim_context(row, None)["input"]
            result = _resume_output(body.result, context["profile"])
            artifacts = [_render_pdf(task_id, result)]
            for item in artifacts:
                _register_artifact(conn, task_id, item["name"], item["media_type"], DATA_DIR / "artifacts" / task_id / item["name"])
            result["artifacts"] = artifacts
        else:
            raise HTTPException(409, "Only model tasks can be completed through the harness")
        changed = conn.execute("UPDATE tasks SET status='completed',result_json=?,error=NULL,worker_id=NULL,updated_at=? WHERE id=? AND status='running' AND worker_id=?",
                               (json.dumps(result, ensure_ascii=False), now(), task_id, body.worker_id)).rowcount
        if changed != 1:
            raise HTTPException(409, "Task changed state before completion")
        saved = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    _touch()
    return _task(saved)


@app.post("/api/harness/tasks/{task_id}/fail")
def fail(task_id: str, body: FailRequest, _: None = Depends(require_token)):
    with _db() as conn:
        changed = conn.execute("UPDATE tasks SET status='failed',error=?,worker_id=NULL,updated_at=? WHERE id=? AND status='running' AND worker_id=?",
                               (body.error, now(), task_id, body.worker_id)).rowcount
        if not changed:
            raise HTTPException(409, "Task is not running under this worker_id")
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    _touch()
    return _task(row)


@app.get("/api/artifacts/{task_id}/{name}")
def artifact(task_id: str, name: str, _: None = Depends(require_token)):
    if Path(name).name != name:
        raise HTTPException(404, "Artifact not found")
    with _db() as conn:
        row = conn.execute("SELECT path,media_type FROM artifacts WHERE task_id=? AND name=?", (task_id, name)).fetchone()
    if not row:
        raise HTTPException(404, "Artifact not found")
    path = Path(row["path"]).resolve()
    if DATA_DIR not in path.parents or not path.is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(path, media_type=row["media_type"], filename=name)

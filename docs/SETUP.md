# Local setup and operation

## Requirements

- Linux/macOS with Python 3.12; this repository supports the local app only on Python 3.12.
- `uv` installed separately.
- `DISPLAY` and a desktop session for headed Playwright Chromium. The browser path fails explicitly instead of switching to headless.
- Chromium shared libraries available on the host. `uv run playwright install chromium` downloads the browser but does not install privileged OS packages.

## Install

From the repository root:

```sh
uv sync --frozen
uv run playwright install chromium
uv run find-a-job doctor
```

`uv sync --frozen` installs the pinned project dependencies into the root `.venv`. The `.venv` is ignored. The Playwright browser is installed in its user cache. No sudo is required by these commands. If Chromium reports missing system libraries, use the OS package manager for the explicitly named libraries after reviewing them; no automatic privileged installation is performed.

An optional variable template is provided in `.env.example`; the application does not load dotenv files automatically. Export or source the value in the shell before running the CLI.

Set `FIND_A_JOB_DATA_DIR` before launch to choose another private application-data directory. Default: `~/.local/share/find-a-job`. It contains SQLite task state, a mode-0600 session token, uploaded source text, and browser/PDF artifacts. Directory permissions are restricted to the current user. Back up or remove this directory only when you intend to manage that candidate's data.

`find-a-job doctor` verifies the supported Python version, imports, `DISPLAY`, and installed Chromium executable path; it does not launch Chromium or test external integrations.

## Run

```sh
uv run find-a-job serve --open
```

`serve --open` runs in the foreground. When a harness must keep processing tasks, start it in the harness's persistent background process/session and keep it alive. The command opens the dashboard. If no browser opens, run `uv run find-a-job open` after the session is initialized.

The service binds to `127.0.0.1:8765`, disables HTTP access logging, and serves the browser UI at `/`. Health is available at `GET /api/health`; it returns only `{ "status": "ok" }`. Every other API route requires `Authorization: Bearer <session-token>`. The UI receives its token in `/#token=...`, stores the token in session storage, and removes the fragment immediately. Keep that URL private. The server accepts only localhost Host and same-origin localhost Origin values and has no wildcard CORS policy.

## Host/model bridge

The service does not invoke a model provider. The host uses its selected model to process claimed task input and return a structured result.

```sh
uv run find-a-job claim --worker-id YOUR_HARNESS --model YOUR_SELECTED_MODEL --wait
uv run find-a-job complete --worker-id YOUR_HARNESS --task-id TASK_ID --result-file /private/result.json
```

`claim` returns `{ "task": null }` when idle. With `--wait`, it polls until it claims one task, prints that task's exact input and output schema, then exits. After handling the task, the host must call `claim --wait` again. The service has no universal push API and does not start a model worker; the host must keep its selected-model claim loop active. The skill asks the harness to manage that loop rather than asking the user to issue every claim and completion command.

The worker ID must match for completion or failure. Profile output requires source-quoted facts; the service stores evidence as `confirmed: false`. Resume items must cite existing profile evidence IDs. Invalid output receives a 4xx response and remains available to the claiming worker for correction. REST callers use the same bearer token and schemas; service OpenAPI is available at `/docs` on localhost.

Harness connectivity uses a 30-second heartbeat window. `claim`, `complete`, and `fail` update it. During longer model work, run `uv run find-a-job heartbeat` periodically.

## Boundaries and status

- Intake supports multiple PDF, DOCX, ODT, XLSX, PPTX, RTF, Markdown, UTF-8 text, CSV/TSV, JSON/YAML, HTML/XML, and common source-code files; each source remains distinct. Scanned PDFs need OCR. See Candidate intake API below.
- Browser tasks run through a visible headed Playwright Chromium session. The 2026-10-20 smoke check loaded `https://example.com`, captured `browser.png` and `page.txt`, and rejected `http://127.0.0.1/` before navigation. Chromium used normal system certificate validation; no certificate bypass was enabled and no TLS error occurred.
- The dashboard and CLI read the same task store. The smoke check observed task status updates, a loaded screenshot preview, stable selection/focus across polling, and one URL revocation when changing the selected preview. The PDF artifact was text-extracted and visually rendered from the generated PDF; the embedded PDF viewer remained black in the hidden dashboard browser capture, so verify that preview in the target desktop browser.
- The PDF was generated from synthetic profile evidence and the archived example.com page. That page is documentation content, not a job description; the smoke resume contains no job-specific requirements.
- `find-a-job doctor` reported Python 3.12.12, `DISPLAY=:1`, installed dependencies, and installed Playwright Chromium. A clean environment installation was not rerun.
- The host-driven CLI path was exercised with `openai-codex/gpt-6-luna`. No second harness/model combination or standalone provider adapter was exercised. No paid service calls or privileged installs were made.
- Browser research does not bypass login/access restrictions or submit forms. The request gate rejects private/local destinations and non-web schemes for top-level, redirect, and subresource requests. This smoke exercised only a top-level loopback URL; it did not exercise redirect or subresource blocking.

> Output quality depends on the model you select, the source evidence and your configuration. Different models may produce different extraction, matching and resume-writing results. Review generated content for factual accuracy and suitability before use. The pipeline does not guarantee ATS acceptance, interviews or employment.
### Candidate intake API

`POST /api/intakes` accepts authenticated `multipart/form-data` fields: repeated `resume_files` (one or more readable resume sources unless `notes` is supplied), repeated optional `catalog_files`, `github_urls` as a JSON array string, optional `notes`, and optional `title`. A maximum of 25 sources, 10 MiB per file, 50 MiB total upload bytes, 100000 extracted characters per document, and 1,000,000 aggregate extracted characters are accepted. Originals are retained under the private data directory; source text, safe labels, IDs, extraction status and errors are persisted in the task input.

Supported suffixes: PDF (selectable text; page labels retained), DOCX (paragraphs and table cells), ODT, XLSX (cell values), PPTX (slide text), RTF, `.md`/`.markdown`, TXT, JSON, YAML/YML, CSV/TSV, HTML/HTM, XML, PY, JS, TS, Java, SQL, TOML, and INI. Scanned PDFs need OCR. DOC files and other binary formats are unsupported; export them to a supported format. Office ZIP containers are capped at 40 MiB expanded size.

GitHub URLs must be HTTPS `github.com/<owner>` or `github.com/<owner>/<repo>` URLs without credentials, query, fragment or port. Only explicitly selected URLs are fetched in the visible browser. GitHub retrievals run before profile claims and persist success/failure on the linked source.

Profile claim input includes `input.intake`, `input.sources`, and `input.source_documents` with source IDs, labels, text, status and errors. For intake results, each evidence item uses `source` as a source document ID; its quote must occur in that exact document. Completed extraction is still pending review. `POST /api/intakes/{id}/review` accepts `{ "result": <full profile result> }` and revalidates it; `POST /api/intakes/{id}/approve` accepts `{ "acknowledge_source_errors": true|false }`. Approving with failed sources requires explicit acknowledgment; those source IDs are recorded as exclusions. Resume task creation rejects an unapproved intake profile. `/api/uploads` is retired.

`POST /api/tasks` rejects new `kind: "profile"` requests with an actionable instruction to use `/api/intakes`; this prevents text-only profiles from bypassing source identity, provenance review, and approval. Reopening an approved intake through `/review` saves the correction as pending and invalidates approval; approve it again before resume creation.

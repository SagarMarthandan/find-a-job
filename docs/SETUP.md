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

Set `FIND_A_JOB_DATA_DIR` before launch to choose another private application-data directory. Default: `~/.local/share/find-a-job`. It contains SQLite task state, a mode-0600 session token, uploaded originals, source snapshots, and browser/PDF artifacts. Successful extraction and approved-context snapshots are cached in `cache/cache.sqlite3` under a mode-0700 cache directory; the database is mode 0600. These files are local plaintext protected by filesystem permissions, not encryption. Back up or remove this directory only when you intend to manage that candidate's data.

`find-a-job doctor` verifies the supported Python version, imports, `DISPLAY`, and installed Chromium executable path; it does not launch Chromium or test external integrations.

## Offline approved context

The context command reads an existing completed and approved intake directly from local task state. It works while the dashboard, service, and browser are stopped:

```sh
uv run find-a-job context --intake-id INTAKE_ID
uv run find-a-job context --intake-id INTAKE_ID --refresh
```

The JSON contains the exact approved profile, complete source snapshots, source references, and a profile revision. It is candidate data; do not send it to logs or save it in an unprotected location. The command does not start a server or browser, call a model/provider, change approval, or refetch GitHub/browser sources. `--refresh` rebuilds from the current local snapshot only.

File extraction results are reused only for matching upload bytes, normalized suffix, and extraction/parser version. Empty or failed extractions are not cached. Reuse never shares source/intake IDs, approval, filenames, or model output. Verification on 2026-10-21 passed: `.venv/bin/python -m unittest discover -s tests` (22 tests) and a synthetic local smoke using a transient `python -m find_a_job.cli serve` process without `--open`. Identical multipart uploads produced extraction miss/hit, fresh IDs, and no inherited approval; source-quoted profile approval succeeded; authenticated context returned miss/hit and rejected unauthenticated access with 401; correction and reapproval changed the revision; two offline CLI subprocesses returned persistent hits and the same revision after the server stopped; empty extraction stayed uncached; and a second workspace could not read the first intake or reuse its extraction. The suite covered corrupt-cache rebuild and stale-resume rejection. The smoke used no browser tasks, external/provider calls, or real candidate data, and removed its temporary workspace.

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
- Cache/context regressions, including corrupt-cache rebuild and stale-resume rejection, passed in the 49-test suite. The intake/context synthetic HTTP/CLI smoke used no browser tasks or external/provider calls.
- Stage 2 was exercised on 2026-10-21 with a synthetic approved intake, host claim/complete loop and the rendered dashboard. Role suggestions completed, same-model cache reuse and different-model isolation were observed, and a custom role plus filters were saved. A live visible-browser Arbeitnow search discovered 69 links, captured two detail pages with `browser.png`/`page.txt`, then a synthetic assessment completed with a deterministic `review` decision because required experience was unknown. Screenshots rendered in the dashboard. The earlier `openai-codex/gpt-6-luna` run is retained as historical evidence; current repository development policy permits only `opencode-go/deepseek-v4.1-flash` as the execution worker.
- LinkedIn, Indeed, XING, Stepstone, Wellfound, EU Remote and ATS were capability/URL-checked only. They are not claimed as live-verified. Indeed uses a per-selected-country domain; unknown selected countries are reported instead of silently using the wrong domain. Source host allowlists reject impostor suffixes.
- Stage 2 tests covered stale role/config revisions, model cache identity, unknown fields, duplicate normalized URLs, cancelled search tasks, seen/applied history isolation, all-sources-blocked failure, partial source failure, parseable experience/date/language evidence and remote-region unknown eligibility. The live smoke used no logins, paid services, provider calls or real candidate data.
- Stage 2 correction smoke on 2026-10-21 used a fresh temporary workspace (`/tmp/find-a-job-search-smoke-NTajHW1b`) and a synthetic approved intake. The API completed and approved a source-quoted synthetic profile, completed one host-role task, and the actual dashboard edited the suggested role title to `Platform Engineer`, kept the suggestion id with `custom: false`, and saved configuration revision 1. Generated Arbeitnow listing URLs were `https://www.arbeitnow.com/?search=Platform+Engineer`, `...search=platform+engineer` and `...search=python`. No live job source was browsed in this correction smoke. The workspace and screenshots were retained for parent review, with the service stopped after verification.
- Stage 2 correction tests added 10 focused regressions (49 total): suggested-title edit plus source URLs, exact city evidence and Berlin/Paris state change, York/New York and Kiel/Kielce collisions, partial country/city unknown, Europe/worldwide remote unknown, core values checked against their own quotes, range years anchored to the minimum required bound, optional years text rejected, zero-experience filtering, non-finite numbers, same-day timestamp invariance, date-only/weekly/hour precision, future dates and fetched-at timezone normalization.
- Stage 2 final smoke on 2026-10-21 passed all 49 tests in 8.252 s. It exercised the actual CLI and dashboard with a synthetic approved intake: role suggestions completed, a suggested role title was edited, and filters were saved; saving did not start browsing. The live headed Arbeitnow run discovered 70 listing links, captured 10 detail pages and 24 artifacts, and produced 3 assessed jobs (0 match, 2 review, 1 excluded); all 12 screenshots loaded in the dashboard. Smoke evidence was retained under `/tmp/find-a-job-smoke2-V0LVpVJo` and used synthetic candidate data only.
- Known unresolved Stage 2 parser false negatives from that smoke: an explicit `5+ years` requirement under a multiline `Requirements` heading was not recognized, and compact posting-age labels (`1w`, `3w`, `1mo`) were treated as unknown. Those assessments therefore stayed in `review`/unknown instead of being evaluated. No code or test change was made for these findings, so they remain a known limitation rather than a passing check.
- Known Stage 2 limits: configured radius is always reported as unknown because the adapters expose no distance evidence; listing adapters do not paginate beyond the configured per-source detail cap; live source coverage is still Arbeitnow only. LinkedIn, Indeed, XING, Stepstone, Wellfound, EU Remote and ATS remain URL/capability-checked only, not live-verified. Date-only source dates are interpreted as UTC calendar dates anchored to `fetched_at`; a timestamp that straddles the requested window becomes `review` under the default policy rather than passing.

> Output quality depends on the model you select, the source evidence and your configuration. Different models may produce different extraction, matching and resume-writing results. Review generated content for factual accuracy and suitability before use. The pipeline does not guarantee ATS acceptance, interviews or employment.
### Candidate intake API

`POST /api/intakes` accepts authenticated `multipart/form-data` fields: repeated `resume_files` (one or more readable resume sources unless `notes` is supplied), repeated optional `catalog_files`, `github_urls` as a JSON array string, optional `notes`, and optional `title`. A maximum of 25 sources, 10 MiB per file, 50 MiB total upload bytes, 100000 extracted characters per document, and 1,000,000 aggregate extracted characters are accepted. Originals are retained under the private data directory; source text, safe labels, IDs, extraction status and errors are persisted in the task input.

Supported suffixes: PDF (selectable text; page labels retained), DOCX (paragraphs and table cells), ODT, XLSX (cell values), PPTX (slide text), RTF, `.md`/`.markdown`, TXT, JSON, YAML/YML, CSV/TSV, HTML/HTM, XML, PY, JS, TS, Java, SQL, TOML, and INI. Scanned PDFs need OCR. DOC files and other binary formats are unsupported; export them to a supported format. Office ZIP containers are capped at 40 MiB expanded size.

GitHub URLs must be HTTPS `github.com/<owner>` or `github.com/<owner>/<repo>` URLs without credentials, query, fragment or port. Only explicitly selected URLs are fetched in the visible browser. GitHub retrievals run before profile claims and persist success/failure on the linked source.

Profile claim input includes `input.intake`, `input.sources`, and `input.source_documents` with source IDs, labels, text, status and errors. For intake results, each evidence item uses `source` as a source document ID; its quote must occur in that exact document. Completed extraction is still pending review. `POST /api/intakes/{id}/review` accepts `{ "result": <full profile result> }` and revalidates it; `POST /api/intakes/{id}/approve` accepts `{ "acknowledge_source_errors": true|false }`. Approving with failed sources requires explicit acknowledgment; those source IDs are recorded as exclusions. Resume task creation rejects an unapproved intake profile. `GET /api/intakes/{id}/context?refresh=false` returns the current approved profile and full source snapshots using the same review guard. `/api/uploads` is retired.

`POST /api/tasks` rejects new `kind: "profile"` requests with an actionable instruction to use `/api/intakes`; this prevents text-only profiles from bypassing source identity, provenance review, and approval. Reopening an approved intake through `/review` saves the correction as pending and invalidates approval; approve it again before resume creation.

Resume claims include the saved profile revision and its source context. If the profile is corrected while a resume task is queued or running, the backend rechecks approval/revision and rejects stale work rather than completing it against an outdated profile.

### Role discovery and job search API

All routes require the same bearer token and localhost guards. Role and search tasks use the existing `claim`/`complete`/`fail` lifecycle.

- `GET /api/search/sources` returns the eight source descriptors with `native_filters`, `post_filters` and notes. The catalog does not promise that a live source will be accessible.
- `POST /api/intakes/{id}/roles` accepts `{ "refresh": false }`. It requires an approved intake and queues a `roles` task bound to the current profile revision. The claim includes `profile`, full source evidence, `prompt` and the role output schema. Results require unique non-empty ids/titles, search terms and existing evidence IDs. Cached results are keyed by intake, revision, claimed model and prompt/schema version; `refresh: true` or a different/unknown model does not reuse them.
- `GET /api/intakes/{id}/search` returns the latest role task bound to the current approval, the saved config, `stale` for a stale role task and `config_stale` for a config tied to an older profile revision.
- `PUT /api/intakes/{id}/search` validates and persists a complete config bound to the current role task and profile revision. Defaults are unconstrained: null experience/posting age/dates, empty filter arrays, `unknown_policy: "review"`, no history exclusion and 10 detail pages per source. `sources` must contain at least one supported ID. `radius_km` requires a city. `languages[].level` is the maximum required proficiency the candidate accepts, not a fluency claim. Unknown/extra config fields are rejected. A suggested role is identified by its id; its title and search aliases are candidate-editable, and both feed the source queries. A non-custom role id must belong to the current role task, so editing does not bypass suggestion ownership.
- `POST /api/intakes/{id}/search/run` accepts `{}`. It requires a current saved config and queues a browser search task with an immutable config snapshot. The browser worker visits native public search URLs, captures listing/detail pages and screenshots, then either marks the task failed when every selected source is `error`/`blocked`, or stores `input.acquisition` and waits for the host. Readable pages with no links are not turned into a fake successful zero-result search.
- Search claims include the approved profile, config snapshot, captured pages and the assessment schema. The host returns `{ "jobs": [JobAssessment] }`. Each URL must match a captured detail page; title, company, location and every non-null requirement need a quote that occurs on that exact page. The title/company/location values must themselves occur in their field-specific quote after case/whitespace normalization. Numeric experience, posting dates and language levels are checked against parseable quote text. Ranges anchor to the minimum required bound; optional/nice-to-have text does not establish a required number. Posting age is evaluated from quote precision: a date-only or relative-day quote spans a day, an hours-ago quote spans its hour bucket, and an arbitrary timestamp on the quote's day cannot change eligibility. Future posting dates fail. Relative dates anchor to acquisition time. Duplicate normalized URLs merge roles/sources/evidence.
- The backend, not the model, decides `match`, `review` or `excluded`. Countries and cities are separate constraints: a configured city must match the location, and with both configured a missing country remains unknown rather than passing. Matching uses word/phrase boundaries plus a start-of-place rule, so `York` does not match `New York` and `Kiel` does not match `Kielce`. Remote worldwide/Europe without an explicit matching country remains unknown. Unsupported radius remains unknown. Unknown requirements follow `unknown_policy`; failures and unknown reasons are retained.
- `GET /api/intakes/{id}/history` returns applied and previously seen URLs for that intake only. `POST /api/intakes/{id}/application-status` updates local `applied`/`not_applied` metadata; it never submits an application. `exclude_seen` uses previous completed runs, not the current run.
- Search completion and role completion recheck approval/revision. Cancellation is not overwritten by late completion. Saving new filters creates a new revision; an already running search keeps its bound snapshot.

The dashboard's Stage 2 panel exposes these controls. Saving does not start browsing; the Start search button requires a saved current config. Source capability notes show which filters are native and which are post-checked or unknown. Captured screenshots are served through the authenticated artifact route.

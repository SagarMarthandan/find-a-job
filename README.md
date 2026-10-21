# find-a-job

Local-first candidate intake, evidence-grounded role discovery and visible-browser job search, job-description research and resume generation. The application runs as an authenticated localhost dashboard with a portable, host-driven CLI bridge; it does not require OMP, nested model credentials, or a particular model provider. Start with multiple resume/catalog sources, candidate notes, and selected public GitHub URLs; review and approve the consolidated source-grounded profile before later workflow stages.

## Quick start

```sh
uv sync --frozen
uv run playwright install chromium
uv run find-a-job doctor
uv run find-a-job serve --open
```

Requires Python 3.12, `uv`, and a graphical desktop (`DISPLAY`) for visible browser research. Setup and data handling: [docs/SETUP.md](docs/SETUP.md). Portable harness instructions: [SKILL.md](SKILL.md).

Application data (SQLite tasks, uploaded content, artifacts, and private session token) is stored outside the repository under `~/.local/share/find-a-job` by default. Override with `FIND_A_JOB_DATA_DIR`. Keep the fragment-bearing dashboard URL private.

## Persistent extraction and approved inference context

Successful non-empty file extractions are cached at `cache/cache.sqlite3` under the configured data directory. Cache keys include the uploaded bytes, normalized suffix, and extraction/parser version. Failed and empty extractions are not cached. Reusing extracted text does not reuse an intake ID, source ID, filename, approval, or model output; every upload remains a new source requiring its own reviewed profile.

For a saved profile, run `uv run find-a-job context --intake-id INTAKE_ID`. It returns the exact currently approved profile, complete saved source text, source references, and profile revision. The command works with the dashboard, service, and browser stopped; it does not call a model or refetch remote sources. `--refresh` rebuilds context from the current local snapshot only.

Context output can contain full candidate evidence. Treat terminal output and redirected files as sensitive. Task, source, and cache data remain local plaintext protected by filesystem permissions, not encryption. Verification on 2026-10-21 passed: `.venv/bin/python -m unittest discover -s tests` (49 tests) plus a synthetic local HTTP/CLI/harness loop and a live visible-browser Arbeitnow search. The intake/context smoke used `python -m find_a_job.cli serve` without `--open`; identical multipart uploads produced extraction miss/hit, fresh IDs, and no copied approval. A host completed and approved a source-quoted synthetic profile. The authenticated context endpoint returned miss/hit, rejected an unauthenticated request with 401, and returned complete catalog fields and hashes. Correction and reapproval changed the revision. Two offline CLI subprocesses returned persistent hits with a stable revision after the service stopped. Empty extraction remained uncached, and a second workspace could not read the first intake or reuse its extraction. Stage 2 exercised role suggestions, same-model cache reuse, different-model cache isolation, custom-role and filter persistence, browser acquisition, source failures, deterministic tri-state decisions, history, cancellation, stale revisions, and the dashboard. The live search captured two Arbeitnow detail pages with screenshots; other adapters were URL-checked only and remain live-unverified. The smoke used no provider calls, paid services, logins, or real candidate data; it stopped the server and browser. A focused correction smoke on 2026-10-21 used a fresh workspace and a synthetic approved profile: the dashboard edited the suggested title to `Platform Engineer`, saved it as revision 1, and generated Arbeitnow listing URLs containing `Platform+Engineer` and `python`. That smoke did not browse a live job source. Known limits: radius remains an explicit unknown because no adapter provides distance evidence; adapters do not paginate listing results beyond the configured detail cap; only Arbeitnow was live-verified in the earlier run, and the other seven adapters remain live-unverified.

Candidate intake is the first workflow. Stage 2 adds candidate-scoped role suggestions and visible-browser job discovery: approve an intake, request roles from its approved profile and evidence, select or add roles and filters, save the search configuration, then start a fresh browser search. Search never starts from merely saving settings. The legacy `Jobscraper/` launcher remains separate and is not invoked by the unified search path.

The latest Stage 2 smoke (2026-10-21) passed all 49 tests in 8.252 s and exercised a synthetic approved intake through the CLI and dashboard, including a suggested-role title edit and saved filters. Saving filters did not start browsing. A live headed Arbeitnow run then discovered 70 listing links, captured 10 detail pages and 24 artifacts, and assessed 3 jobs (0 match, 2 review, 1 excluded); all 12 screenshots loaded in the dashboard. Known unresolved parser gaps: an explicit `5+ years` requirement under a multiline `Requirements` heading and compact age labels such as `1w`, `3w` and `1mo` were not recognized, so those cases remained unknown/review. The other seven source adapters, configured radius and listing pagination remain unverified as described in `docs/SETUP.md`.

Intake accepts up to 25 sources, with a 10 MiB per-file limit, 50 MiB total upload limit, 100000 extracted characters per document, and 1 MB aggregate extracted text. Office ZIP expansion is capped at 40 MiB. Supported formats: PDF, DOCX, ODT, XLSX, PPTX, RTF, MD/Markdown, TXT, JSON, YAML/YML, CSV/TSV, HTML/HTM, XML, PY, JS, TS, Java, SQL, TOML, and INI. Scanned PDFs need OCR; DOC and other binary files are unsupported and remain visible as failed sources.

The integrated local smoke check passed on 2026-10-20: headed Playwright loaded `https://example.com`, the CLI completed synthetic profile and resume tasks with the current host-selected model, and the backend generated a review-only PDF. The example page is not a job description. A capable harness must keep the service in a persistent background session and repeat `claim --wait`; the service has no universal push API. Other harness/model combinations and standalone model adapters remain unverified.

The intake integration smoke created one task with 19 identified sources: 17 extracted successfully across PDF, Markdown, DOCX (including table cells), XLSX, JSON, YAML, RTF, ODT, PPTX, HTML, TXT, CSV/TSV, XML, PY and INI; scanned PDF and legacy DOC were retained as actionable failures. The visible browser captured `https://github.com/octocat/Hello-World`. Host-selected model `openai-codex/gpt-6-luna` produced a synthetic profile; wrong-source evidence and an invented skill were rejected. UI correction, failed-source acknowledgment, approval, correction-driven approval invalidation, resume gating, and persistence across service restart passed. Clean-environment/model portability remains unverified.

> Output quality depends on the model you select, the source evidence and your configuration. Different models may produce different extraction, matching and resume-writing results. Review generated content for factual accuracy and suitability before use. The pipeline does not guarantee ATS acceptance, interviews or employment.

Legacy `Jobscraper/` and `llm-cv/` trees are preserved. Do not publish those directories or delete personal data without an explicit scoped decision.

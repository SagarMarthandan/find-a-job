# find-a-job

Local-first candidate intake, job-description research and evidence-grounded resume generation. The application runs as an authenticated localhost dashboard with a portable, host-driven CLI bridge; it does not require OMP, nested model credentials, or a particular model provider. Start with multiple resume/catalog sources, candidate notes, and selected public GitHub URLs; review and approve the consolidated source-grounded profile before later workflow stages.

## Quick start

```sh
uv sync --frozen
uv run playwright install chromium
uv run find-a-job doctor
uv run find-a-job serve --open
```

Requires Python 3.12, `uv`, and a graphical desktop (`DISPLAY`) for visible browser research. Setup and data handling: [docs/SETUP.md](docs/SETUP.md). Portable harness instructions: [SKILL.md](SKILL.md).

Application data (SQLite tasks, uploaded content, artifacts, and private session token) is stored outside the repository under `~/.local/share/find-a-job` by default. Override with `FIND_A_JOB_DATA_DIR`. Keep the fragment-bearing dashboard URL private.

Candidate intake is the first workflow; job search and `Jobscraper/` work are deferred. Upload all resume and project-catalog sources together, optionally add notes and selected public GitHub URLs, then review, correct, and explicitly approve the consolidated profile before later stages.

Intake accepts up to 25 sources, with a 10 MiB per-file limit, 50 MiB total upload limit, 100000 extracted characters per document, and 1 MB aggregate extracted text. Office ZIP expansion is capped at 40 MiB. Supported formats: PDF, DOCX, ODT, XLSX, PPTX, RTF, MD/Markdown, TXT, JSON, YAML/YML, CSV/TSV, HTML/HTM, XML, PY, JS, TS, Java, SQL, TOML, and INI. Scanned PDFs need OCR; DOC and other binary files are unsupported and remain visible as failed sources.

The integrated local smoke check passed on 2026-10-20: headed Playwright loaded `https://example.com`, the CLI completed synthetic profile and resume tasks with the current host-selected model, and the backend generated a review-only PDF. The example page is not a job description. A capable harness must keep the service in a persistent background session and repeat `claim --wait`; the service has no universal push API. Other harness/model combinations and standalone model adapters remain unverified.

The intake integration smoke created one task with 19 identified sources: 17 extracted successfully across PDF, Markdown, DOCX (including table cells), XLSX, JSON, YAML, RTF, ODT, PPTX, HTML, TXT, CSV/TSV, XML, PY and INI; scanned PDF and legacy DOC were retained as actionable failures. The visible browser captured `https://github.com/octocat/Hello-World`. Host-selected model `openai-codex/gpt-6-luna` produced a synthetic profile; wrong-source evidence and an invented skill were rejected. UI correction, failed-source acknowledgment, approval, correction-driven approval invalidation, resume gating, and persistence across service restart passed. Clean-environment/model portability remains unverified.

> Output quality depends on the model you select, the source evidence and your configuration. Different models may produce different extraction, matching and resume-writing results. Review generated content for factual accuracy and suitability before use. The pipeline does not guarantee ATS acceptance, interviews or employment.

Legacy `Jobscraper/` and `llm-cv/` trees are preserved. Do not publish those directories or delete personal data without an explicit scoped decision.

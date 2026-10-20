# Changelog

## Unreleased

- Added a localhost-only FastAPI service with private session-token authentication, SQLite task state, uploads, harness claim/complete/fail routes, evidence validation, and review-only PDF output.
- Added the `find-a-job` CLI and a discoverable portable `SKILL.md` with YAML metadata, persistent service startup, and host-side claim-loop instructions.
- Added pinned Python packaging and setup/diagnostic documentation. Setup commands remain separate from paid service connectivity checks.
- Aligned headed Playwright screenshot names with the artifact registry (`browser.png`) and added dashboard task-detail stability, focus restoration, harness connection status, and blob URL cleanup.
- Added behavioral tests for wrong-owner completion, late completion after cancellation, source-evidence validation, and private URL refusal.
- Made the candidate-source intake the first workflow: one task preserves every resume, catalog, notes and selected GitHub source identity; extracts PDF, Markdown/TXT, DOCX tables, ODT, XLSX, PPTX, RTF, JSON/YAML, CSV/TSV, HTML/XML and configured source-code text; and retains unsupported/scanned inputs with errors. Limits: 25 sources, 10 MiB/file, 50 MiB/request, 100000 characters/document, 1 MB aggregate text and 40 MiB expanded Office ZIP.
- Rejected new generic `kind: "profile"` task creation in favor of `/api/intakes`; enforced source-specific evidence quotes, pending-source gating, explicit failed-source acknowledgment, correction-driven approval invalidation and the downstream resume gate. The dashboard supports editing an already approved intake and requires reapproval after changes.
- Measured the previous intake extractor dispatcher at CC 37 and split extraction into named format handlers (dispatcher CC 6; highest helper CC 8).
- Intake smoke on 2026-10-20 used 19 synthetic sources and a real visible-browser capture of `https://github.com/octocat/Hello-World`; 17 sources extracted and scanned PDF/DOC failures remained visible. Selected-model consolidation, wrong-source/invented-claim rejection, UI correction/approval/exclusions, approval invalidation, resume gate, and persistence after restart were exercised. All nine unittest cases passed.
- Fixed harness claims failing with HTTP 500 when the queue was empty or every intake was waiting on GitHub. Added a regression covering both states; all ten tests passed, and the restarted live service returned `{"task": null}` for an empty queue.
- Verified the integrated local flow on 2026-10-20: visible Chromium fetched `https://example.com`; synthetic profile and resume tasks completed through the current host-selected model and CLI; the PDF artifact passed text extraction and visual rendering. The example page is not a job description.
- The dashboard screenshot preview and live task state passed. The PDF iframe appeared black in the hidden dashboard browser capture; verify it in the target desktop browser. No clean-environment installation, second harness/model, standalone model adapter, or cross-harness portability run was performed. The service has no universal push API; the host must keep polling claims.

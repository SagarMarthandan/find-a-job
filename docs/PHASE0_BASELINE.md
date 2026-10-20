# Phase 0 baseline proof

Scope: reachable, local baseline checks only. No application code was implemented; no legacy source or personal files were changed. No live job search, model-backed resume pipeline, external fetch, paid call, install, test suite, build, lint, or formatter was run. Temporary synthetic PDF artifacts were removed after inspection.

## Inventory observed

| Area | Observed baseline |
|---|---|
| Unified environment | No root `.venv`, `pyproject.toml`, or `uv.lock` was present. Existing environments are `llm-cv/.venv` and `Jobscraper/.venv`; neither is a reproducible root setup. |
| Local Python packages | `llm-cv/.venv`: PyYAML 6.0.3, ReportLab 5.0.0, pypdf 6.15.0, requests 2.34.2, Beautiful Soup 4.15.0, tqdm 4.70.0; openpyxl missing. `Jobscraper/.venv`: PyYAML 6.0.1, ReportLab 5.0.0, requests 2.34.2, openpyxl 3.1.5, Beautiful Soup 4.12.3, tqdm 4.70.0; pypdf missing. These are distinct legacy environments, not a clean-install result. |
| System tools | `pdflatex`, `pdftotext`, `pdfinfo`, `pdftoppm`, `tinyfish`, `omp`, `uv`, `node`, and `npm` resolved on `PATH`. Only `pdftotext`, `pdfinfo`, and `pdftoppm` were exercised. `pdflatex` was present but not exercised. |
| Search helpers | `Jobscraper/job_identity.py::normalize_job_url`, `location_policy.py::is_germany_location` and `is_hamburg_or_kiel`, and `staffing_filter.py::is_staffing_company` are importable deterministically. `apify_job_search.py` was deliberately not imported: module scope creates its absolute `/home/sagar/Skills/Jobscraper/Job Search` directory. |
| Personal/local coupling | Search code and docs contain absolute `/home/sagar/...` paths, including `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files`, `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio`, and `/home/sagar/Documents/applications_tracker.csv`. Resume configuration defaults application storage to `/home/sagar/Applications` and contains personal contact, photo, signature, city, and location fallback values. The render smoke supplied synthetic `contact_info` and `photo: null`; it did not use the model pipeline or personal artifacts. |
| Judgment/model coupling | `Jobscraper/verify_jobs.py` documents OMP typed `judge_batch` judgment; its standalone CLI cannot perform that judgment. `llm-cv` documents isolated OMP CLI calls using OMP's configured default model, without a pipeline model/provider selection. Neither contract is a portable host-driven task I/O interface. |
| URL-fetch fallback | `llm-cv/00_jd_fetch.md` documents TinyFish primary, Firecrawl only on TinyFish failure/unusable content, then Jina, then manual JD paste. Firecrawl is described as a launcher-mounted MCP capability and its fallback orchestration is documentation/agent-launcher-only; no standalone Firecrawl client or API route was exercised. Jina is the documented final fetch backup, not exercised. |
| Search credential/config risk | Legacy Indeed GraphQL integration embeds an API key in `Jobscraper/apify_job_search.py`; the value is intentionally not reproduced here. No search request was made. |
| Browser stack | `Jobscraper/package.json` and `package-lock.json` declare Playwright, playwright-extra, and puppeteer-extra-plugin-stealth. Legacy changelog says the Playwright Indeed fallback was removed; no active browser launch or retained use was verified. Node/npm were installed, but the browser stack was not installed/verified or exercised in this check. |
| Existing manifests | Legacy Python dependency declarations/README inventories do not agree: the resume setup names PyYAML, ReportLab, pypdf, tqdm; the search README names requests, openpyxl, Beautiful Soup, tqdm. The installed environments confirm these are incomplete as a combined application inventory. |

Installed, authenticated, and exercised are separate states: local Python packages and listed binaries were observed installed/resolvable; TinyFish/OMP authentication was not checked; no external service or model was exercised. The current session's Firecrawl MCP documentation/capability does not prove a deployed application's access or authentication.

## Baseline proof results

### Deterministic helpers — passed

Imported the three standalone helper modules from `Jobscraper/` without importing the search orchestrator. Thirteen assertions passed:

- URL normalization removes tracking parameters, retains a non-tracking query, canonicalizes Indeed `jk` and LinkedIn numeric posting IDs.
- Germany eligibility accepts Berlin and a mixed foreign/German location, rejects empty/unknown, Paris/France, and explicitly negated Germany.
- Student-city matching accepts Kiel and rejects Kielce.
- Staffing matching accepts Hays and rejects a synthetic direct employer.

These establish behavior of existing deterministic helpers, not a live search path.

### Resume renderer — passed with fixture correction

Actual command shape exercised in an isolated unique `/tmp/phase0-baseline-*` directory:

```sh
llm-cv/.venv/bin/python llm-cv/yaml_to_pdf.py <synthetic_resume.yaml> <synthetic_resume.pdf>
pdftotext <synthetic_resume.pdf> <synthetic_resume.txt>
pdfinfo <synthetic_resume.pdf>
pdftoppm -png -f 1 -singlefile -r 120 <synthetic_resume.pdf> <layout-prefix>
```

The first renderer invocation exited nonzero because the synthetic fixture supplied spoken languages as a list of objects, while the existing reportfallback renderer expects a list of strings. The fixture was corrected to the documented-by-implementation string shape; no source change was made. The rerun succeeded through the actual `yaml_to_pdf.py` CLI using `render_mode: reportfallback`, `resume_style: us`, explicit synthetic contact fields, and `contact_info.photo: null`.

Observed after correction: PDF rendered, `pdftotext` succeeded, `pdfinfo` reported one page, and `pdftoppm` rasterized the page for visual inspection. Extracted text contained the synthetic name, email, phone, GitHub, employer, project, experience dates (`01/2022 – 03/2024`), and education dates (`Sep 2018 – Jun 2021`). The output contained none of the configured personal default name, email, phone, or GitHub values. The rendered page showed legible content in one page; the remaining page area was whitespace. Temporary input, PDF, text, and raster were deleted.

## Not demonstrated / blockers

- Plan Phase 0 acceptance is **not complete**: no portable structured task input/output exchange exists to exercise without OMP, and neither representative live search nor model-backed resume path was run. The parent launched approved workers with explicit `openai-codex/gpt-6-luna` and `opencode-go/deepseek-v4.1-flash` model arguments; the shipping worker reported a successful harmless command (`worker-command-ok`). The launch arguments and successful execution are observed, but independent server-side identity attestation is unavailable. No live-service smoke was substituted for missing portable contracts.
- TinyFish and OMP executables being present does not establish authentication. No credentials or provider calls were checked. No external scraping was attempted.
- Firecrawl's documented failure-only fallback is mounted-MCP/launcher-oriented; standalone setup, connection, authentication, and capability verification remain absent/unverified. The fallback chain remains TinyFish → Firecrawl → Jina → manual JD; it was not exercised.
- Root environment/lockfile, consolidated dependency inventory, supported setup and integration diagnostics are absent. Browser package declarations do not prove browser binaries/runtime availability.
- Existing personal defaults, static profile/catalog dependencies, absolute paths, and embedded Indeed key remain legacy issues for later migration. No personal data was copied into this report.

## Next implementation contract

Before integrated feature work, define one portable, candidate-scoped structured profile/evidence and task input/output contract. Keep deterministic URL identity, configurable eligibility, storage, and rendering separate from model tasks. Provide both host-driven execution and configured standalone adapters without OMP as a core requirement; expose explicit unknown/failure states. Replace personal/default-path and static catalog dependencies with reviewed synthetic/real candidate-scoped inputs, migrate callers, and retain the conservative TinyFish → Firecrawl → Jina → manual fetch order through explicit adapters. Build a root pinned environment and verify it from a clean setup only after dependencies and permissions are agreed. Preserve legacy source and personal artifacts until integrated acceptance and explicit data-preservation decisions are met.

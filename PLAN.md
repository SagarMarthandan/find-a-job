# Unified job-finding application

Status: Stages 1 and 2 are implemented and verified against synthetic candidates. Candidate intake, source-grounded profile approval, role suggestions and visible-browser job search now share one task store and CLI/API. Resume generation remains the later stage. This plan remains the broader proposed design.

## Current priority and deferred scope

The first workflow is candidate intake from uploaded resumes, all supplied project catalogs, candidate-entered facts, and explicitly selected public GitHub URLs. Consolidate those sources into one evidence-linked profile, validate source-specific quotes, support correction and explicit approval, and gate later stages on approval. Keep every source's identity and failure visible.

Stage 2 uses that approved profile to suggest evidence-cited roles, then persists candidate-selected roles and filters per intake. A saved configuration drives actual visible-browser acquisition from the eight native source adapters. Role and search results remain candidate/intake-scoped; no legacy personal role, country, age or experience defaults apply.

Supported intake formats are PDF, DOCX, ODT, XLSX, PPTX, RTF, Markdown/TXT, JSON/YAML, CSV/TSV, HTML/XML, and the configured UTF-8 text suffixes PY, JS, TS, Java, SQL, TOML, and INI. Limits: 25 sources, 10 MiB per file, 50 MiB total upload bytes, 100000 extracted characters per document, 1 MB aggregate extracted text, and 40 MiB expanded Office ZIP content. Scanned PDFs require OCR; unsupported sources remain attached as failed entries.

Do not delete or rewrite the legacy `Jobscraper/` or `llm-cv/` trees until the wider migration acceptance in Phase 4. The unified search path does not invoke the legacy launcher or import its personal catalogs.

## Goal

Build one application where a candidate uploads a CV, connects selected GitHub evidence, reviews extracted facts and search preferences, finds suitable jobs, and generates a truthful tailored resume from the same candidate profile.

Deliver the unified pipeline as a harness-agnostic, model-agnostic skill as well as an application. A user chooses the harness, provider and model; the runtime must not require the repository's development models.

Remove the application's dependency on Sagar's bundled resume, project catalog, contact details, city mappings, photos, signatures, and absolute filesystem paths. Preserve originals until an explicit migration and deletion decision. User-uploaded personal data remains necessary; store it outside the source tree.

## Current implementation and reuse

| Area | Observed implementation | Proposed treatment |
|---|---|---|
| Search | Legacy `Jobscraper/apify_job_search.py`; unified `src/find_a_job/job_sources.py` now implements eight visible-browser adapters | Unified adapters reuse native public search URLs and evidence-preserving detail capture; legacy API/Apify path is not invoked. Live verification is recorded per source |
| Search policies | Driver filters are candidate-configured (roles, experience, country/city, remote, dates, employment, languages, history) | Legacy data/AI, Germany-only, two-year and Hamburg/Kiel restrictions are not defaults; unknown remote/radius eligibility stays reviewable |
| Posting identity | Legacy `Jobscraper/job_identity.py`; pure unified `src/find_a_job/job_identity.py` | Reuse URL normalization and dedup identity; candidate application history remains intake-scoped |
| Verification | `Jobscraper/verify_jobs.py`; README says it requires OMP typed `judge_batch` and cannot complete through its standalone CLI | Introduce an explicit application judgment interface; resolve runtime dependency before claiming standalone operation |
| Resume workflow | `llm-cv/README.md` documents JD analysis, project selection, resume/cover-letter writing and PDF compilation | Reuse useful stages and renderers after removing candidate-specific inputs |
| Candidate data | `llm-cv/config.py` contains personal defaults, assets and city mappings | Read only the active candidate profile; no personal fallback defaults |
| Project selection | `llm-cv/run_pipeline.sh` passes `okf/project_catalog.yaml` to `extract_projects.py` | Generate project records from uploads, manual entries and GitHub evidence |
| Local coupling | `llm-cv/run_pipeline.sh` hardcodes skill and applications directories | Use application-managed storage and explicit artifact locations |
| Model selection | Resume README says calls currently use OMP's configured default model | Honor the runtime user's selected model through a portable interface; remove mandatory OMP/default-model coupling |

These are inspection findings, not live integration verification. Existing source availability, provider authentication, PDF toolchain and model identifiers still need runtime checks.

## Stage 2 observed implementation

- `roles.py` builds a versioned prompt from the approved profile and full source evidence. Role tasks are bound to the intake/profile revision; completion validates unique ids/titles, search terms, evidence IDs, rationale and gaps. The cache key includes intake, revision, claimed model and prompt/schema version; refresh and other models bypass it.
- `search.py` validates the complete configuration with unconstrained defaults, rejects unknown fields/source IDs, and evaluates archived assessments deterministically. Filter outcomes are tri-state. Numeric experience, posting dates and language levels require field-specific quotes that occur on the exact detail page; core title/company/location values must occur in their own quotes, years ranges anchor to the minimum required bound, and date-only/relative posting quotes use precision intervals rather than an invented hour. Cities constrain the selected city, countries and cities are separate constraints, and matching uses word/phrase boundaries plus a start-of-place rule (`York` ≠ `New York`, `Kiel` ≠ `Kielce`). Duplicate normalized URLs merge role/source/evidence.
- `job_sources.py` exports the fixed catalog and `run_search(session, config, artifact_dir, cancelled)`. It uses only the visible `BrowserSession`, native listing URLs, source-host allowlists (including per-country Indeed domains), bounded detail capture, flat artifact names and explicit `ok`/`empty`/`error`/`blocked` statuses.
- `api.py` persists role/config/search tasks, acquisition snapshots and candidate-scoped history, and rechecks approval/revision at claim and completion. All-source failure is a failed search, not an empty success. Captured screenshots render through the dashboard artifact route.
- Observed 2026-10-21: 49 tests passed after the Stage 2 correction. A synthetic approved intake produced roles, cached the same-model result, isolated a different model, saved a custom role plus filters, and ran a live visible-browser Arbeitnow search (69 links discovered, two detail pages captured with screenshots). A synthetic host assessment completed and was deterministically `review` because experience was unknown. The dashboard rendered roles, config revision, source status, captured screenshots and results. The correction smoke used a fresh workspace: the dashboard edited the suggested role title to `Platform Engineer`, saved it under the suggestion id as revision 1, and the generated Arbeitnow listing URLs contained the edited title and aliases. This correction smoke did not browse a live job source.
- Final smoke 2026-10-21: all 49 tests passed (8.252 s). The actual CLI and dashboard completed role suggestions, edited a suggested role title and saved filters for a synthetic approved intake, with no browser navigation before Start. A live headed Arbeitnow run discovered 70 listing links, captured 10 detail pages and 24 artifacts, and assessed 3 jobs as 0 `match`, 2 `review`, 1 `excluded`; all 12 screenshots loaded. The other seven adapters were not live-verified.
- Known unresolved parser false negatives: the live smoke did not recognize an explicit `5+ years` requirement under a multiline `Requirements` heading, and treated compact posting-age labels (`1w`, `3w`, `1mo`) as unknown. These findings are documented but not fixed; they reduce evidence coverage rather than proving the requirement absent.
- Known Stage 2 limits: configured radius remains unknown because adapters expose no distance evidence; listing adapters do not paginate beyond the configured detail cap; live verification is limited to Arbeitnow. The other seven adapters remain URL/capability-checked only.
- Live-unverified adapters: LinkedIn, Indeed, XING, Stepstone, Wellfound, EU Remote, ATS. Their builders and host/path checks were exercised; no access success is claimed. Login/rate-limit/captcha responses remain blocked/error states.

## Proposed product boundary

The skill starts or reuses the localhost dashboard and keeps the host-driven task bridge active. The skill, CLI and web interface share the same workflow, schemas and SQLite state. Direct CLI access remains available, but skill invocation must open the dashboard rather than offering a CLI-only default. Keep records candidate-scoped so another profile cannot inherit facts or application history. Do not add hosted multi-user accounts until deployment requirements justify authentication and tenant isolation.

Application stack: Python, FastAPI, SQLite and a vanilla HTML/JavaScript dashboard. Run visible Playwright browser research on a dedicated worker thread; the host-selected model handles queued language tasks and ReportLab renders reviewable PDFs. No microservices or vector database.

Initial screens:

1. Profile: CV upload, GitHub URL, optional project/work-experience inputs, evidence review and corrections.
2. Search preferences: role suggestions, locations, remote eligibility, seniority, language, freshness and source selection.
3. Jobs: search progress, source failures, eligibility, match reasons, gaps, shortlist and application status.
4. Application: archived JD, selected evidence, tailored resume preview and PDF download. Retain cover-letter support if its existing path can use the same evidence contract.

Submitting applications, contacting employers and changing external accounts are outside this scope. Generating a resume must not mark an application as submitted.

## Repository consolidation

The user authorizes restructuring and dissolving `Jobscraper/` and `llm-cv/` into one integrated codebase. Their current folder boundaries are not product requirements. Prefer one Python package with modules for candidate profiles, job discovery, matching and document generation, rather than two legacy packages or launchers.

Target layout, to create during implementation:

```text
find-a-job/
  AGENTS.md                  # repository development policy only
  PLAN.md
  SKILL.md                   # portable end-user skill entrypoint
  pyproject.toml
  uv.lock
  .venv/                     # generated locally, ignored
  src/find_a_job/
    profile/                 # ingestion, review and evidence
    jobs/                    # sources, identity and search policies
    matching/                # eligibility and evidence-backed assessment
    documents/               # tailoring and renderers
    integrations/            # model, fetch and optional harness adapters
    workflow.py              # shared orchestration/state transitions
    cli.py                   # skill/CLI entrypoint
    web/                     # dashboard opened by skill invocation
  resources/                 # portable prompts and non-personal templates
  config/                    # secret-free integration examples
  tests/
  docs/
```

Use one dependency environment, shared schemas, configuration system and workflow. Add submodules only as their implementations require them; do not create empty architecture scaffolds. Keep user data outside this tree.

During migration, map retained legacy code, tests, prompts and templates to the new modules. Inspect hidden and ignored files before retiring folders so personal artifacts, credentials or application history are not lost. Preserve relevant licenses/attribution and useful documentation; discard obsolete instructions after updating their callers. Migrate imports, scripts, resource paths and installation instructions together. Do not keep old folder names as permanent compatibility entrypoints.

Remove superseded source files and the old directories after the integrated paths pass acceptance checks and protected data has been preserved. Folder-consolidation permission covers code restructuring and removal of obsolete code; it does not authorize destruction of personal source documents, generated applications, credentials or unrelated files.

## Shared data contract

Use explicit schemas and persistent IDs rather than passing model-generated prose between stages.

| Record | Essential content |
|---|---|
| CandidateProfile | Contact information, education, dated work experience, skills, languages, projects, preferences, revision |
| Evidence | Source kind, upload/repository reference, source location, extraction time, claim and confirmation status |
| Project | Description, candidate contribution, technologies, dates, repository URL and evidence references |
| SearchPreferences | Approved role families, regions, remote scope, experience policy, languages, freshness, sources |
| JobPosting | Source ID, canonical URL, company/title, location, JD snapshot, posting/fetch time and availability |
| MatchAssessment | Profile revision, JD revision, eligibility decision, evidence-backed matches, missing requirements and uncertainty |
| Application | Candidate/job identity, selected evidence, generated artifact revisions and manually maintained submission status |
| TaskRun | Stage, status, input revisions, errors, provider/model identity and artifact references |

Keep extracted suggestions separate from confirmed facts. A candidate correction takes precedence for future runs; retain provenance and mark conflicting source evidence for review. Freeze profile and JD revisions for each generation so later edits do not silently alter old applications.

## Workflow

### 1. Ingest and confirm the candidate profile

- Accept PDF and DOCX CVs initially. Detect unreadable/scanned documents and request a readable file or explicit OCR support; do not generate from empty extraction.
- Extract contact details, skills, education, experience and projects into the shared schema.
- Read public GitHub profile and selected public repositories through the API. Handle pagination, rate limits and inaccessible repositories explicitly. Private repositories require a later, explicit authorization decision.
- Use READMEs, repository metadata and selected relevant files as evidence. Repository presence, forks and contribution counts do not prove authorship, professional experience, proficiency or impact.
- Let the candidate add missing work experience, project contributions and corrections. Confirm claims before resume use.
- Derive search suggestions from the reviewed CV plus GitHub evidence. Missing GitHub evidence does not erase confirmed CV skills; failed GitHub retrieval remains visible.

### 2. Search using the reviewed profile

- Suggest role families and search terms from confirmed skills and experience; let the candidate approve or edit them before scraping.
- Pass one preferences object into source fetchers, eligibility checks and verification. Remove hardcoded candidate role, country and city assumptions throughout those paths.
- Normalize jobs, deduplicate source results and archive full descriptions before final assessment.
- Apply explicit hard constraints separately from relevance ranking. Unknown location, language or experience evidence must appear as uncertain, not as an invented pass.
- Show evidence-backed match reasons and skill gaps. Never present a heuristic score as a hiring probability.
- Report partial source failures separately from a successful search with zero matches. Respect source terms, rate limits and credential requirements.

### 3. Generate a tailored resume

- Select a discovered job or supply a JD directly; both routes use the same archived-JD contract.
- Rank projects and work experience from the candidate profile against the JD. Build the generation context from selected evidence, not a static personal catalog.
- Tailor wording and ordering without inventing technologies, employers, dates, metrics, ownership, language ability or experience.
- Represent missing requirements as gaps. Do not add hidden keywords or unsupported claims to increase an ATS score.
- Review generated structured content, render a PDF with the existing suitable renderer, and check extracted text and visible layout.
- Preserve the JD, evidence selection, profile revision and output together. ATS reports describe the application's heuristic checks, not an employer's private score.

## Development model policy and runtime portability

### Repository development only

Development orchestration and planning belong to OpenAI-GPT-Sol-6.1. Only OpencodeGo-Deepseek-Flash-v4.1 (`opencode-go/deepseek-v4.1-flash`) executes scripts, implementation and delegated checks under `AGENTS.md`; GPT-Luna-6 is not a fallback. Verify the exact provider-qualified development worker ID and availability before implementation. Development launchers enforce this policy; they must not export its model allowlist into the delivered skill or application.

### Delivered skill and application

- Ship a discoverable `SKILL.md` with portable instructions, relative resource paths, setup requirements, CLI examples and artifact contracts. Keep harness-specific installation metadata in separate adapters; do not ship repository-development `AGENTS.md` as end-user skill instructions.
- Let users choose any provider/model through their harness or configured model client, including local models. No mandatory named model, subscription tier, OMP installation, subagent facility or private harness API.
- Support host-driven execution: the selected harness model performs language tasks using the documented schemas and CLI stages. Do not require a second provider API key or nested model launcher when the host already supplies model execution.
- Support standalone CLI/web execution through a configured model-client adapter. Users choose its provider, endpoint and model. Document adapter requirements; do not claim every provider protocol works without an adapter.
- Separate deterministic parsing, filtering, storage and rendering from language-model work. Use a narrow structured task contract for extraction, judgment and writing. Validate responses in application code; native JSON mode, tool calling and reasoning controls are optional capabilities, not universal prerequisites.
- Replace mandatory OMP `judge_batch` and CLI calls with the shared task contract. An OMP adapter may use batching, but a sequential host-driven path must produce the same artifact schema.
- Discover fetch/judgment capabilities by contract, not fixed tool names or session-specific URIs. TinyFish CLI, Firecrawl MCP/API and other configured clients remain integration adapters; a harness without MCP can use the supported API/CLI route.
- Preserve the selected model. Do not silently switch providers/models, escalate to a frontier model, or change privacy/cost boundaries. Record requested and observed model identity where the host exposes it; otherwise mark identity as host-managed/unverified.
- Check required capabilities before execution. A harness must read skill instructions and invoke the documented CLI/tools or exchange task input/output files. Report missing capabilities and setup steps. A chat-only environment cannot execute local tools merely because a skill file exists.
- Models may differ in context limits and schema reliability. Bound task inputs to configured limits and report invalid/unsupported responses without inventing successful results. Portability does not guarantee acceptable output from every model.

### Required output-quality disclaimer

Include this disclaimer in `SKILL.md`, setup documentation and the application before the first model-backed run:

> Output quality depends on the model you select, the source evidence and your configuration. Different models may produce different extraction, matching and resume-writing results. Review generated content for factual accuracy and suitability before use. The pipeline does not guarantee ATS acceptance, interviews or employment.

### Portability acceptance

Exercise the same synthetic candidate and representative JD end to end in at least two distinct harnesses and with at least two user-selected models, including one outside the development-model allowlist. Check schema-valid artifacts, evidence provenance, search preferences and a rendered resume, not identical wording. Include a host-driven run without nested model credentials and a standalone adapter run without OMP. Exercise an unavailable optional capability and an invalid model response; both must report actionable states without silent model switching.

Publish a tested compatibility matrix with harness/model versions, capabilities and observed outcomes. Treat untested combinations as unverified, not incompatible by default and not proven supported. During repository development, approved workers launch these scenarios; runtime model choices are test subjects, not development-worker substitutions.

## Dependency and environment deliverable

The application deliverable includes a reproducible environment, service integrations and setup verification, not just application source. Implement this during Phase 0 and extend it with each feature. The implementation note below distinguishes installed/exercised components from outstanding integrations.

Implementation note (2026-10-20): the local integration smoke now covers authenticated API/CLI task sharing, visible Playwright navigation and screenshot capture, synthetic profile evidence, host-generated resume content, PDF rendering, private URL rejection, wrong-owner rejection, and cancellation races. The permanent `unittest` suite passed four focused security/state tests. `find-a-job doctor` reported Python 3.12.12, `DISPLAY=:1`, dependencies, and Chromium installed. This run exercised one host-driven model (`openai-codex/gpt-6-luna`); it did not test a clean environment setup, a second harness/model, a standalone provider adapter, or the portability matrix. The embedded PDF viewer appeared black in a hidden-browser screenshot; the generated PDF itself passed text extraction and local visual rendering. The service has no universal push API; the host must keep polling claims.

### Local environment

- Create one root `.venv` for the unified application. Do not commit or copy existing virtual environments. Select a supported Python version after checking retained dependencies; the existing resume README requires Python 3.10+.
- Maintain a root `pyproject.toml` and committed `uv.lock`, including development dependencies. A clean checkout must support `uv sync --frozen` and execution through `uv run`; document a single supported setup path.
- Separate required packages from optional source/browser/OCR integrations. Record supported Python, Node and operating-system versions. Use a Node lockfile and `npm ci` only if retained integrations require Node.
- Provide an idempotent setup command, an environment-variable template containing names but no secrets, and a diagnostic command that checks required packages, binaries, credentials, provider connectivity and enabled integrations. Installation and paid connectivity checks must be separate.
- Document Linux system dependencies and any privileged install steps before execution. Store uploads, caches, SQLite records and artifacts in configurable application-data directories outside tracked source.

### Dependency inventory

| Layer | Required or conditional components | Delivery requirement |
|---|---|---|
| Existing Python pipelines | `pyyaml`, `reportlab`, `pypdf`; search README also lists `requests`, `openpyxl`, `beautifulsoup4`, `tqdm` | Reconcile actual imports with manifests, pin resolved versions and remove unused packages |
| Proposed application | Shared schema validation, upload/DOCX parsing; FastAPI, ASGI server and templates for the web extra | Separate core/skill dependencies from web-only packages and pin compatible versions |
| PDF toolchain | TeX Live/`pdflatex` for retained LaTeX output; existing README lists latex-base, latex-recommended, latex-extra, fonts-recommended and lang-german | Verify compilation and required fonts/packages; include text-extraction tools only when the retained audit needs them |
| Browser integrations | Existing `Jobscraper/package.json` declares Playwright, playwright-extra and puppeteer-extra-plugin-stealth | Audit usage before retaining; pin supported versions, install required browser binaries/system libraries, and verify launch. Stealth plugins are not permission to bypass access controls |
| Model execution | Host-provided model execution or a configured standalone provider adapter; optional OMP adapter | No mandatory OMP or development-model allowlist at runtime; document capabilities, credentials and tested combinations |
| TinyFish | CLI, supported installation method/version and authentication | Include primary JD-fetch setup and a same-posting extraction smoke check |
| Firecrawl | Mounted `firecrawl_scrape` MCP capability or a documented supported API integration | Include client/server connection configuration, authentication, transport and capability checks; a Python virtual environment alone cannot provide a mounted MCP |
| Jina Reader | HTTP endpoint; optional `JINA_API_KEY` in the current workflow | Preserve as the explicit failure-only backup after Firecrawl; disclose whether content leaves the machine |
| GitHub | Public API access; optional token for rate limits, private access only after approval | Document scopes, rate limits and credential storage; GitHub MCP is optional unless implementation actually uses it |
| Other job sources | Credentials or API subscriptions only for enabled integrations that require them | Maintain a source-by-source requirements matrix; do not require Apify merely because legacy filenames mention it |
| Development verification | Existing behavioral test dependencies and browser verification tooling | Lock development tools separately; do not install unrelated agent plugins or MCP servers |

The existing Python requirements file lists only three packages, while the READMEs describe additional dependencies. Treat neither as a complete installation specification until the import and external-command inventory is finished.

### MCPs, plugins and standalone execution

- Track each integration's purpose, required/optional status, version, install source, configuration location, credential names, permissions, cost/limits and verification command.
- TinyFish is currently a CLI dependency. Firecrawl is currently a launcher-mounted MCP dependency. They are not interchangeable packages and need separate setup instructions.
- The user's visible-browser requirement supersedes TinyFish-first fetching for the dashboard workflow: visit pages in headed Chromium and synchronize captured text/screenshots with the harness. Do not silently substitute API scraping if the browser fails. TinyFish → Firecrawl → Jina remains the legacy fetch policy and a potential separately selected integration mode, not the current dashboard execution path.
- The standalone app must have access to its own configured fetch/judgment clients. It must not assume access to this chat session's MCP tools. Implement and verify a supported API/CLI route as well as MCP integration so lack of MCP support does not lock the skill to one harness.
- Supply configuration templates for supported harness adapters and MCP transports. Keep the core skill independent of those templates and secrets outside tracked configuration; never auto-grant broad filesystem, shell or private-repository access.
- Optional integrations can be disabled without breaking unrelated features. If an enabled required capability is missing, show an actionable error rather than treating it as an empty search.
- Obsidian/vault synchronization remains optional and must not require the developer's personal vault or agent plugins.

### Environment acceptance

An approved development worker must demonstrate setup from a clean environment: dependency resolution from lockfiles, package imports, application startup, actual visible browser launch, synthetic CV parsing, database persistence, PDF rendering/text inspection and a user-selected runtime model call. Verify the development worker identity separately. TinyFish/Firecrawl authentication and fallback checks remain required before claiming those optional integration paths work. Complete the portability acceptance matrix before release.

Record observed versions and outcomes, including missing credentials, unavailable MCP capabilities and provider failures. Do not describe a capability as working merely because a CLI or MCP server is installed. Obtain authorization before privileged installs, permission grants, personal-data disclosure or chargeable external checks.

## Migration phases and acceptance gates

### Phase 0: establish runtime and baseline

Inventory entrypoints, personal-data references, external services, prompts, renderers and existing tests. Confirm development worker availability, identify and replace OMP-only runtime contracts, define the portable skill/CLI task interface, and check PDF dependencies. Keep original personal artifacts untouched.

Acceptance: documented dependency and capability map; approved development worker successfully executes a harmless command; portable task input/output exchange exercised without OMP; one representative existing search and resume path exercised with external failures reported honestly.

### Phase 1: candidate profile and evidence

Implement upload parsing, public GitHub ingestion, review/edit UI, provenance and profile persistence.

Acceptance: two synthetic candidates produce distinct profiles with no Sagar defaults; corrections override suggestions; unreadable uploads and unavailable GitHub data produce actionable states; secrets and uploads stay out of version control.

### Phase 2: profile-driven search

Implemented for the unified package: shared role/filter config, eight visible-browser source adapters, immutable acquisition snapshots, evidence validation, deterministic eligibility and candidate-scoped history. Live verification currently covers Arbeitnow only.

Acceptance: materially different candidate profiles produce different approved search terms; an explicit preference change changes eligibility; cross-profile history does not suppress another candidate's results; partial outages and duplicate postings remain visible and correct. Remaining: exercise the other seven adapters against live public pages and record their observed access limits.

### Phase 3: evidence-driven resume generation

Replace catalog/base-resume dependencies in prompts, selection, configuration and rendering. Integrate JD analysis and document artifacts into the application.

Acceptance: generate and visually inspect a real PDF from a synthetic profile and a representative JD; extracted text preserves contact details and dates; every factual generated claim has supporting evidence; unsupported requirements remain gaps; no personal fallback assets or hardcoded local paths are required.

### Phase 4: integrated application and cutover

Connect upload, review, search, shortlist and generation through the shared skill/CLI core and web UI. Persist long-running task outcomes and candidate-scoped application history. Complete repository consolidation, migrate every retained caller/resource/test to the unified package, then remove superseded code, old launchers and the legacy directories once protected data is preserved.

Acceptance: complete the browser workflow with two synthetic candidates and the portability acceptance scenarios through the packaged skill; restart the app and inspect persisted results; verify failure recovery without duplicate artifacts; run relevant existing behavioral regressions. Verify the quality disclaimer in skill/setup documentation and the first-run UI. Archive or delete original personal files only with explicit approval of the exact files. Update setup, compatibility and operational documentation after the verified cutover.

## Privacy and external-content boundaries

Keep uploads and generated artifacts outside the repository. Explain which CV/JD/repository content leaves the machine for model providers; obtain consent before processing real personal data. Avoid logging CV bodies, tokens or private repository content. Provide an explicit local data-deletion action with confirmation.

Treat CVs, JDs and repository text as untrusted content, never agent instructions. Validate upload size/type and isolate document rendering from arbitrary commands and unrestricted local-file access. Use least-privilege credentials stored outside application records. Fetch URLs with boundaries against private-network and local-file access.

## Decisions for discussion

Recommended defaults, not user-approved commitments:

1. Portable skill and CLI core, plus local-first single-user web UI using Python/FastAPI/SQLite.
2. PDF/DOCX upload plus public GitHub; optional manual facts for missing experience or projects.
3. Germany as an editable initial preference, not a permanent eligibility rule.
4. Review candidate facts and search preferences before the first search; review a tailored resume before export/use.
5. Reuse working integrations and renderers; replace candidate-specific contracts rather than wrapping two unchanged skill launchers.
6. Resolve exact development worker IDs and delegation mechanism before implementation. Keep runtime model/harness selection independent and verify the portability matrix before release.

Next discussion: agree on deployment/UI, GitHub access scope, search geography and document outputs. Then begin Phase 0 with the approved worker models.

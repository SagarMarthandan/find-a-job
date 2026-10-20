# Unified job-finding application

Status: proposed design for discussion. No application code or personal files change in this planning step.

## Goal

Build one application where a candidate uploads a CV, connects a GitHub profile, reviews extracted facts and search preferences, finds suitable jobs, and generates a truthful tailored resume from the same candidate profile.

Deliver the unified pipeline as a harness-agnostic, model-agnostic skill as well as an application. A user chooses the harness, provider and model; the runtime must not require the repository's development models.

Remove the application's dependency on Sagar's bundled resume, project catalog, contact details, city mappings, photos, signatures, and absolute filesystem paths. Preserve originals until an explicit migration and deletion decision. User-uploaded personal data remains necessary; store it outside the source tree.

## Current implementation and reuse

| Area | Observed implementation | Proposed treatment |
|---|---|---|
| Search | `Jobscraper/apify_job_search.py`; README documents eight sources | Reuse source integrations behind a shared search interface; verify each integration before enabling it |
| Search policies | README and `ARCHITECTURE.md` document data/AI roles, Germany eligibility, two-year ceiling and Hamburg/Kiel student restrictions | Replace candidate-specific policies with reviewed profile preferences; preserve conservative handling of unknown eligibility |
| Posting identity | `Jobscraper/job_identity.py`, documented in architecture | Reuse URL normalization and dedup logic; separate posting identity from candidate application history |
| Verification | `Jobscraper/verify_jobs.py`; README says it requires OMP typed `judge_batch` and cannot complete through its standalone CLI | Introduce an explicit application judgment interface; resolve runtime dependency before claiming standalone operation |
| Resume workflow | `llm-cv/README.md` documents JD analysis, project selection, resume/cover-letter writing and PDF compilation | Reuse useful stages and renderers after removing candidate-specific inputs |
| Candidate data | `llm-cv/config.py` contains personal defaults, assets and city mappings | Read only the active candidate profile; no personal fallback defaults |
| Project selection | `llm-cv/run_pipeline.sh` passes `okf/project_catalog.yaml` to `extract_projects.py` | Generate project records from uploads, manual entries and GitHub evidence |
| Local coupling | `llm-cv/run_pipeline.sh` hardcodes skill and applications directories | Use application-managed storage and explicit artifact locations |
| Model selection | Resume README says calls currently use OMP's configured default model | Honor the runtime user's selected model through a portable interface; remove mandatory OMP/default-model coupling |

These are inspection findings, not live integration verification. Existing source availability, provider authentication, PDF toolchain and model identifiers still need runtime checks.

## Proposed product boundary

Build a portable skill and shared Python core first. The skill, CLI and local-first web application use the same workflow and schemas. The CLI path must not require the web server. Keep records candidate-scoped so another profile cannot inherit facts or application history. Do not add hosted multi-user accounts until deployment requirements justify authentication and tenant isolation.

Recommended application stack: Python services, FastAPI, SQLite, and a small server-rendered UI. Keep existing Python integrations where practical. Use one durable background worker for long searches and PDF generation; persist task state in SQLite. Keep web-only dependencies separate from the skill/CLI installation. No microservices or vector database initially.

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
    web/                     # optional application interface
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

Development orchestration and planning belong to OpenAI-GPT-Sol-6.1. OpenAI-GPT-Luna-6 or OpencodeGo-Deepseek-Flash-v4.1 execute scripts, implementation and delegated checks under `AGENTS.md`. Verify exact provider-qualified development worker IDs and availability before implementation. Development launchers enforce this policy; they must not export its model allowlist into the delivered skill or application.

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

The application deliverable includes a reproducible environment, service integrations and setup verification, not just application source. Implement this during Phase 0 and extend it with each feature. This section specifies future setup work; it does not claim anything is installed.

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
- Preserve the current URL-fetch policy unless deliberately changed: TinyFish first, Firecrawl only after failure or unusable content, then Jina, then manual JD input. Pasted JDs bypass fetching.
- The standalone app must have access to its own configured fetch/judgment clients. It must not assume access to this chat session's MCP tools. Implement and verify a supported API/CLI route as well as MCP integration so lack of MCP support does not lock the skill to one harness.
- Supply configuration templates for supported harness adapters and MCP transports. Keep the core skill independent of those templates and secrets outside tracked configuration; never auto-grant broad filesystem, shell or private-repository access.
- Optional integrations can be disabled without breaking unrelated features. If an enabled required capability is missing, show an actionable error rather than treating it as an empty search.
- Obsidian/vault synchronization remains optional and must not require the developer's personal vault or agent plugins.

### Environment acceptance

An approved development worker must demonstrate setup from a clean environment: dependency resolution from lockfiles, package imports, skill/CLI execution without the web extra, application startup, browser launch where required, synthetic CV parsing, database persistence, PDF rendering/text inspection and a user-selected runtime model call. Verify the development worker identity separately. Verify TinyFish authentication and same-URL extraction, then exercise the configured Firecrawl path independently with an authorized public page. Exercise fallback selection through a controlled primary failure without changing the requested posting. Complete the portability acceptance matrix before release.

Record observed versions and outcomes, including missing credentials, unavailable MCP capabilities and provider failures. Do not describe a capability as working merely because a CLI or MCP server is installed. Obtain authorization before privileged installs, permission grants, personal-data disclosure or chargeable external checks.

## Migration phases and acceptance gates

### Phase 0: establish runtime and baseline

Inventory entrypoints, personal-data references, external services, prompts, renderers and existing tests. Confirm development worker availability, identify and replace OMP-only runtime contracts, define the portable skill/CLI task interface, and check PDF dependencies. Keep original personal artifacts untouched.

Acceptance: documented dependency and capability map; approved development worker successfully executes a harmless command; portable task input/output exchange exercised without OMP; one representative existing search and resume path exercised with external failures reported honestly.

### Phase 1: candidate profile and evidence

Implement upload parsing, public GitHub ingestion, review/edit UI, provenance and profile persistence.

Acceptance: two synthetic candidates produce distinct profiles with no Sagar defaults; corrections override suggestions; unreadable uploads and unavailable GitHub data produce actionable states; secrets and uploads stay out of version control.

### Phase 2: profile-driven search

Pass the shared preferences contract through fetchers and verification. Replace hardcoded role/location/experience policies and import useful posting-identity behavior.

Acceptance: materially different candidate profiles produce different approved search terms; an explicit preference change changes eligibility; cross-profile history does not suppress another candidate's results; partial outages and duplicate postings remain visible and correct.

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

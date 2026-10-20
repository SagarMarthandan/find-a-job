# find-a-job

**Status: planning only.** This repository contains a proposed design for a unified, portable job-search and resume-generation application. No integrated application has been implemented or verified yet.

The proposal is in [PLAN.md](PLAN.md). The intended pipeline will use one candidate-scoped profile and evidence model for both job search and resume generation. It is planned to support a portable skill and CLI as well as an optional web application, with user-selected runtime harnesses and models.

The legacy `Jobscraper/` and `llm-cv/` trees remain in the working repository for inspection and later migration. They contain candidate-specific data and/or credentials; do not publish them as-is. Original personal files must be preserved and kept outside tracked source during implementation.

See [AGENTS.md](AGENTS.md) for repository development and privacy requirements. Setup, runtime behavior, integrations and compatibility are future work; this README makes no claim that they are available or tested.

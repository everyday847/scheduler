# Single Orchestrator Seam

All callers that turn a scheduling *request* into a solved (or feasibility-checked) **Schedule** go through one module, `parafrost_scheduler.orchestrator` — `solve_schedule`, `check_schedule_feasibility`, `solve_schedule_progressive`, and the `build_*_config_from_request` helpers. The CLI (`schedule.py`), the web app (`scheduler.web_app`), and conflict diagnosis (`scheduler.conflict_diagnosis`) call into it rather than each assembling config and invoking the solver their own way.

## Why

These entry points previously duplicated request→config→solve wiring (`solver_bridge` carried most of it, web and diagnosis re-derived pieces), so behavior could diverge by caller. Funneling them through one seam means config assembly and solver invocation happen exactly once, in one place.

## Status / scope

The orchestrator is **application plumbing, not a domain concept** — deliberately absent from `CONTEXT.md`. It carries no scheduling meaning a coordinator would reason about; it is the boundary between "a request arrived" and "the solver ran." Recorded here only because a reader will otherwise wonder why three unrelated callers route through a single module.

This ADR documents the seam established by the `refactor/deepen-architecture` merge, which also renamed the package `new_approach/src/parafrost_scheduler` → `src/parafrost_scheduler` (run layout is now `PYTHONPATH=src`).

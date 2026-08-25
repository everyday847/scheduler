# Declarative Scheduling Rules Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `optimize_schedule` apply YAML-declared Standing Rules and Annual Rules instead of keeping all policy inline.

**Architecture:** Rule YAML is parsed into `SemanticConstraint` values, then a Z3 rule applicator maps supported rule kinds to existing solver helper functions. `service.solve_schedule` supplies standing config plus annual request rules to `optimize_schedule`; solver invariants remain hardcoded.

**Tech Stack:** Python, PyYAML, pytest, Z3.

---

## Chunk 1: Rule Parsing

### Task 1: Parse Generic Standing Rules

**Files:**
- Modify: `src/scheduler/standing_rules.py`
- Test: `tests/test_standing_rules.py`
- Modify: `config/standing/stanford-fellowship.yaml`

- [ ] Write failing tests for `rules:` entries with `kind`, `active`, `strength`, `fellow_groups`, and params.
- [ ] Run the targeted standing-rule tests and confirm the new tests fail.
- [ ] Add generic parsing while preserving current grouped config compatibility only if needed by existing YAML.
- [ ] Convert `config/standing/stanford-fellowship.yaml` to explicit `rules:`.
- [ ] Run tests and commit.

### Task 2: Parse Annual Rule Lists

**Files:**
- Modify: `src/scheduler/annual_rules.py`
- Test: `tests/test_annual_rules.py`
- Modify: `config/annual/example-2025-2026.yaml`

- [ ] Write failing tests for annual `rules:` with active/inactive named assignments and vacation request policy.
- [ ] Run targeted annual-rule tests and confirm failure.
- [ ] Add parsing from annual YAML to `SemanticConstraint`.
- [ ] Convert the example annual YAML to declarative rule entries.
- [ ] Run tests and commit.

## Chunk 2: Rule Application

### Task 3: Apply Declarative Rules To Z3

**Files:**
- Create: `src/scheduler/rule_application.py`
- Test: `tests/test_rule_application.py`
- Modify: `src/scheduler/main.py`

- [ ] Write failing tests with fake optimizer functions showing supported kinds dispatch correctly and inactive rules are skipped.
- [ ] Run targeted tests and confirm failure.
- [ ] Implement the rule applicator and selector resolution.
- [ ] Run tests and commit.

### Task 4: Wire Config Into Scheduling Service

**Files:**
- Modify: `src/scheduler/service.py`
- Test: `tests/test_scheduler_service.py`
- Modify: `src/scheduler/main.py`
- Modify: `docs/tutorials/command-line-scheduling.md`

- [ ] Write failing tests showing `solve_schedule` loads standing YAML and forwards annual constraints.
- [ ] Run targeted tests and confirm failure.
- [ ] Wire standing config loading and annual parsing into `solve_schedule`.
- [ ] Replace migrated inline calls in `optimize_schedule` with declarative rule application.
- [ ] Run full Python tests and commit.

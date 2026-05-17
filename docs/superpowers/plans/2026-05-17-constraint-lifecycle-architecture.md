# Constraint Lifecycle Architecture Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first implementation slice of the constraint lifecycle architecture without changing current solver behavior.

**Architecture:** Add semantic constraints as a new tested layer alongside the existing Z3 solver. Keep `main.py` intact initially, then provide a small Z3 Adapter that can compile a subset of semantic constraints for future migration.

**Tech Stack:** Python 3.12, dataclasses, enum, pytest, z3-solver.

---

## File Structure

- Create `src/scheduler/semantic_constraints.py`: domain-level constraint records, lifecycle, strength, selectors, week spans, and shift sets.
- Create `src/scheduler/standing_rules.py`: YAML-shaped Standing Rule config parsing and semantic constraint generation.
- Create `src/scheduler/annual_rules.py`: Annual Rule builders for vacation requests and named assignments.
- Create `src/scheduler/z3_adapter.py`: adapter that compiles the initial semantic constraints into Z3.
- Create `tests/test_semantic_constraints.py`: tests for the semantic model.
- Create `tests/test_standing_rules.py`: tests for Standing Rule configuration.
- Create `tests/test_annual_rules.py`: tests for Annual Rule builders.
- Create `tests/test_z3_adapter.py`: tests for initial Z3 compilation.

## Chunk 1: Semantic Constraint Model

### Task 1: Add Semantic Constraint Records

**Files:**
- Create: `src/scheduler/semantic_constraints.py`
- Test: `tests/test_semantic_constraints.py`

- [ ] **Step 1: Write failing tests**

Test lifecycle, strength, week span validation, fellow selector creation, shift set creation, and a semantic constraint record that preserves metadata.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_semantic_constraints.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement minimal semantic model**

Add enums and frozen dataclasses for `ConstraintLifecycle`, `ConstraintStrength`, `FellowSelector`, `WeekSpan`, `ShiftSet`, and `SemanticConstraint`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_semantic_constraints.py -q`

- [ ] **Step 5: Commit**

Commit message: `Add semantic constraint model`

## Chunk 2: Standing Rule Config

### Task 2: Add Standing Rule Configuration Shape

**Files:**
- Create: `src/scheduler/standing_rules.py`
- Test: `tests/test_standing_rules.py`

- [ ] **Step 1: Write failing tests**

Test block rule parsing, max-consecutive rule parsing, and Swing deficit minimization config.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_standing_rules.py -q`

- [ ] **Step 3: Implement minimal config parser**

Parse dictionaries into semantic constraints with `ConstraintLifecycle.STANDING_RULE`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_standing_rules.py -q`

- [ ] **Step 5: Commit**

Commit message: `Add standing rule config model`

## Chunk 3: Annual Rule Builders

### Task 3: Add Annual Rule Builders

**Files:**
- Create: `src/scheduler/annual_rules.py`
- Test: `tests/test_annual_rules.py`

- [ ] **Step 1: Write failing tests**

Test vacation request conversion, hard request threshold behavior, soft elective fallback behavior, hard named assignment, and soft named assignment.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_annual_rules.py -q`

- [ ] **Step 3: Implement minimal builders**

Return `SemanticConstraint` records with `ConstraintLifecycle.ANNUAL_RULE`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_annual_rules.py -q`

- [ ] **Step 5: Commit**

Commit message: `Add annual rule builders`

## Chunk 4: Initial Z3 Adapter

### Task 4: Compile Initial Semantic Constraints

**Files:**
- Create: `src/scheduler/z3_adapter.py`
- Test: `tests/test_z3_adapter.py`

- [ ] **Step 1: Write failing tests**

Test compilation for `at_most_one_shift_per_week`, hard assignment, soft assignment, all-or-none block constraints, and minimization objective wiring.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_z3_adapter.py -q`

- [ ] **Step 3: Implement minimal adapter**

Add `Z3ScheduleAdapter` that creates variables for a supplied small problem and compiles supported semantic constraint kinds.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_z3_adapter.py -q`

- [ ] **Step 5: Commit**

Commit message: `Add initial Z3 semantic adapter`

## Chunk 5: Verification

### Task 5: Run Focused And Existing Tests

**Files:**
- Read only unless failures require fixes.

- [ ] **Step 1: Run new tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_semantic_constraints.py tests/test_standing_rules.py tests/test_annual_rules.py tests/test_z3_adapter.py -q`

- [ ] **Step 2: Run existing service tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest tests/test_scheduler_service.py -q`

- [ ] **Step 3: Fix regressions if any**

Use TDD for behavior changes. For pure import or packaging fixes, make the smallest correction and rerun.

- [ ] **Step 4: Commit any verification fixes**

Commit only if verification required code changes.

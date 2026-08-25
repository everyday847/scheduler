# Constraint Lifecycle Architecture Design

## Goal

Build a semantic constraint architecture that separates **Solver Invariants**, **Standing Rules**, and **Annual Rules** before constraints are compiled to Z3.

## Architecture

The scheduler should stop treating raw Z3 expressions as the authoring interface for scheduling policy. User-facing surfaces such as Streamlit, React, command line arguments, and future natural-language parsing should produce **Annual Rules**. Deliberate program configuration should define **Standing Rules**. **Solver Invariants** should stay behind the solver seam because they make the representation coherent.

The first implementation should add new modules alongside the existing solver, preserving behavior while creating a deeper interface:

- A semantic constraint model with lifecycle, strength, fellow selectors, week spans, shift sets, and typed constraint records.
- Standing Rule configuration objects that can be loaded from YAML-shaped dictionaries later.
- Annual Rule builders for current user-facing requests such as vacation requests and named assignments.
- A Z3 Adapter that compiles a narrow initial subset of semantic constraints into the current variable representation.

## Design Choices

- Do not rewrite `optimize_schedule` first. Add the semantic layer and adapter in parallel, then migrate rules incrementally.
- Treat **Annual Rules** as the only ordinary user-facing lifecycle.
- Treat **Standing Rules** as deliberate configuration, eventually YAML-backed.
- Treat the CCM/MICU fellowship as an **External Coverage Pool**. The individual CCM placeholder identities are interchangeable, so solver symmetry-breaking may pin them to blocks without changing the schedule semantics that matter.
- Model the Swing deficit as a soft minimization preference rather than an exposed annual count.
- Model NCC block strictness as a tiered solve strategy: hard four-week blocks first, then fallback to soft four-week and hard two-week blocks.

## Testing

Use TDD for each new layer:

- Semantic model tests should verify validation, lifecycle classification, and helper constructors without Z3.
- Standing Rule tests should verify configuration parsing and generated semantic constraints.
- Annual Rule tests should verify vacation request thresholds and named assignments.
- Z3 Adapter tests should compile small constraints against a tiny in-memory problem, inspect solver behavior, and avoid full 52-week schedule solves.

## Non-Goals

- Do not migrate every existing `main.py` constraint in this pass.
- Do not add natural-language parsing yet.
- Do not change the frontend UI yet.
- Do not change the produced schedule unless a later migration explicitly tests that behavior.

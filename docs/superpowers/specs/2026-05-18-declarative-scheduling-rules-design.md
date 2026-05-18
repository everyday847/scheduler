# Declarative Scheduling Rules Design

## Goal

Move fellowship policy out of `optimize_schedule` and into YAML-backed **Standing Rules** and **Annual Rules**, while keeping **Solver Invariants** in Python.

## Architecture

`optimize_schedule` remains responsible for creating Z3 variables, applying solver invariants, running the optimizer, and extracting schedules. It should not decide which fellowship policy is active for a particular solve.

Rule YAML is parsed into `SemanticConstraint` objects. A Z3 rule applicator owns the mapping from semantic rule kinds to existing Z3 helper functions in `src/scheduler/main.py`. That gives the YAML a declarative shape without pretending that arbitrary natural language or arbitrary YAML can become solver code automatically.

## Rule Sources

- **Solver Invariants** are always applied by code and are not configurable.
- **Standing Rules** are loaded from `config/standing/stanford-fellowship.yaml` by default.
- **Annual Rules** are loaded from the annual request YAML.
- Vacation request policy remains an Annual Rule because coordinators should be able to sweep hard request counts.

## YAML Capabilities

Every YAML rule can include:

- `name`: stable identifier for diagnostics and configuration sweeps.
- `kind`: semantic rule kind.
- `active`: defaults to `true`; `false` skips the rule.
- `strength`: `hard`, `soft`, or `minimize`, depending on the rule kind.
- `fellow_groups` or `fellows`: the target cohort.
- `shifts`, `week`, `weeks`, and rule-specific parameters.

The first supported kinds should cover existing behavior rather than inventing a broad DSL:

- `all_or_none_block`
- `max_consecutive`
- `ncc_coverage`
- `full_assignment`
- `specific_assignment`
- `vacation_request_policy`

Unsupported kinds should fail loudly.

## Migration

The first implementation slice should make `optimize_schedule` consume parsed standing and annual constraints, then migrate the already-overlapping rules. Later slices can move service totals, coverage exceptions, ISC, NH annual policy, and supervision rules into declarative rule kinds.

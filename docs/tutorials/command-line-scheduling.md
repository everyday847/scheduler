# Command Line Scheduling

This tutorial shows the current command line workflow and where configuration belongs while the semantic constraint architecture is being built out.

## Current State

`src/scheduler/main.py` is still the legacy Z3 solver. It has not yet been fully migrated to consume the semantic **Constraint** model in `src/scheduler/semantic_constraints.py`.

The new architecture currently provides:

- `src/scheduler/semantic_constraints.py` for lifecycle-aware **Constraints**.
- `src/scheduler/standing_rules.py` for YAML-shaped **Standing Rule** configuration.
- `src/scheduler/annual_rules.py` for **Annual Rule** builders.
- `src/scheduler/z3_adapter.py` for the first small semantic-to-Z3 adapter.
- `src/scheduler/cli.py` for a practical YAML command line entry point.

The CLI still routes solving through `src/scheduler/service.py`, which calls the legacy `optimize_schedule` in `src/scheduler/main.py`. That keeps the current scheduler usable while rules are migrated incrementally.

## Where Configuration Lives

Use this split:

- **Standing Rules**: `config/standing/stanford-fellowship.yaml`
- **Annual Rules**: `config/annual/example-2025-2026.yaml`

Standing Rules describe how the fellowship program normally works. Examples include four-week NCC blocks, maximum consecutive ICU stretches, and Swing deficit minimization.

Annual Rules describe one schedule year. Examples include the fellow list, vacation requests, ABPN assignments, Lia availability, NH service, and named supervision exceptions.

## Generate An Annual Request

Write the built-in seed request as YAML:

```bash
uv run scheduler default-request --output config/annual/my-2025-2026.yaml
```

You can also print it to stdout:

```bash
uv run scheduler default-request
```

## Edit Annual Rules

Open `config/annual/my-2025-2026.yaml`.

The annual YAML defines the vocabulary used by the solver and by constraints:

```yaml
shifts:
  - NCC1
  - NCC2
  - Swing
  - MICU
  - Vac
fellow_groups:
  NCC_JR:
    - NCC Raya
  NCC_SR:
    - NCC David
  STROKE:
    - Stroke Gabi
  CCM:
    - CCM Ariana
fellow_week_pairs:
  NCC Raya: [21, 37]
```

There are no privileged public keys like `jr_fellows` or `lia`. If a group exists for a year, define it under `fellow_groups`. If the YAML has no `LIA` group, the request has no Lia fellow. Vacation requests must reference fellows declared in the same YAML file.

The `annual_rules` section is tutorial-forward: it documents the intended semantic home for year-specific policy, but `src/scheduler/main.py` does not consume every entry yet.

```yaml
annual_rules:
  vacation_request_policy:
    hard_request_count: 3
```

For example, a coordinator can try honoring each fellow's top five vacation/elective requests. If that does not solve, change `hard_request_count` to `4` or `3` in this section as the semantic architecture is wired through. The YAML request itself is now authoritative for who is present and which fellows have vacation requests.

## Solve A Schedule

Run:

```bash
uv run scheduler solve --request config/annual/my-2025-2026.yaml --output optimized_schedule.xlsx
```

The output is an Excel workbook with per-fellow and per-shift sheets.

## Standing Rule Configuration

Standing Rules belong in `config/standing/stanford-fellowship.yaml`.

Example:

```yaml
block_rules:
  - name: ncc_four_week_blocks
    fellow_groups: [NCC_JR, NCC_SR]
    shifts: [NCC1, NCC2, Swing]
    block_size: 4
    strength: hard
```

This file is intentionally separate from annual requests. A fellowship coordinator should not need to edit it during ordinary schedule tuning.

## What Should Move Next

The next migration step is to make `src/scheduler/main.py` assemble constraints from:

1. Solver invariants owned by the Z3 adapter.
2. Standing Rules loaded from `config/standing/stanford-fellowship.yaml`.
3. Annual Rules loaded from a request YAML file.

Until then, treat the YAML examples as the public workflow and the legacy solver as the execution engine.

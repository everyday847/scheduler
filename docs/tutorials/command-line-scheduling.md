# Command Line Scheduling

This tutorial shows the command line workflow and where configuration belongs.

## Current State

The scheduler now uses YAML to declare the scheduling vocabulary and the policy rules that are safe to tune. The main pieces are:

- `src/scheduler/semantic_constraints.py` for lifecycle-aware **Constraints**.
- `src/scheduler/standing_rules.py` for **Standing Rule** YAML.
- `src/scheduler/annual_rules.py` for **Annual Rule** YAML.
- `src/scheduler/rule_application.py` for applying semantic rules to the Z3 optimizer.
- `src/scheduler/cli.py` for the YAML command line entry point.

`src/scheduler/main.py` still owns the Z3 helper functions and model extraction, but recurring and year-specific policy is now routed through semantic rules before it reaches those helpers.

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

```yaml
annual_rules:
  rules:
    - name: vacation_requests
      kind: vacation_request_policy
      active: true
      hard_request_count: 3

    - name: first_week_senior_on_stroke
      kind: specific_assignment
      active: true
      fellow_groups: [NCC_SR]
      week: 1
      shift: Stroke
      strength: hard
```

For example, a coordinator can try honoring each fellow's top five vacation/elective requests. If that does not solve, change `hard_request_count` to `4` or `3`. Rules can also be disabled with `active: false`; supported assignment-like rules can be relaxed with `strength: soft`.

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
rules:
  - name: ncc_four_week_blocks
    kind: all_or_none_block
    active: true
    fellow_groups: [NCC_JR, NCC_SR]
    shifts: [NCC1, NCC2, Swing]
    block_size: 4
    strength: soft
```

Service expectations can also be defined as data instead of named Python macros:

```yaml
rules:
  - name: ccm_service_profile
    kind: service_profile
    active: true
    strength: hard
    fellow_groups: [CCM]
    zero_shifts: [NS, MICU, SICU, Anaesthesia, Stroke, Clinic/Elective, Telestroke/Clinic, Elec, SCVMC Rehab, NIR, Vac, ISC]
    totals:
      - shifts: [NCC1, NCC2]
        relation: exactly
        weeks: 3
      - shifts: [Swing]
        relation: exactly
        weeks: 1
    active_blocks:
      - name: ccm_ncc_block
        block_size: 4
        trigger_shifts: [NCC1, NCC2, Swing]
        counts:
          - shifts: [NCC1, NCC2, Swing]
            relation: exactly
            weeks: 4
          - shifts: [Swing]
            relation: exactly
            weeks: 1
```

This file is intentionally separate from annual requests. A fellowship coordinator should not need to edit it during ordinary schedule tuning.

## Configuration Sweeps

Because rules are declarative YAML, you can create variants by copying an annual file and changing a small number of values:

- `active: false` to disable a rule.
- `strength: soft` to relax a supported hard preference.
- `hard_request_count` to sweep vacation request strictness.
- `fellow_groups` to represent which cohorts are present this year.

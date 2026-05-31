# Tutorial: Configuring and running the schedule solver

This tutorial walks through setting up a new academic year, configuring the
constraints, and running the solver to produce a complete schedule.

## 1. Configuration overview

The solver reads two YAML files:

- **Annual config** — who's in the program this year and what shifts exist.
- **Standing config** — the fellowship's scheduling rules (block structure,
  shift limits, service profiles).

The annual config changes every year. The standing config changes rarely.

```
config/
  annual/
    my-2025-2026.yaml          # This year's fellows and shifts
    small-2025-2026.yaml       # Test config (fewer fellows)
  standing/
    stanford-fellowship.yaml   # Fellowship scheduling rules
```

## 2. Writing the annual config

The annual config declares the fellows, their groups, and the available shifts:

```yaml
fellow_groups:
  NCC_JR:
    - NCC Alaric
    - NCC Bertram
    - NCC Carrot
  NCC_SR:
    - NCC Durga
    - NCC Eos
    - NCC Fi
  STROKE:
    - Stroke Gabi
    - Stroke Jeff
    - Stroke Victoria
    - Stroke Parshva
  CCM:
    - CCM Generic
  NH:
    - NH Adam
    - NH Barry

shifts:
  - SICU
  - MSICU             # Medical-Surgical ICU (formerly "MICU")
  - NS
  - Anaesthesia
  - NCC1
  - NCC2
  - Swing
  - Elec
  - Vac
  - Stroke
  - Telestroke/Clinic
  - Clinic/Elective
  - SCVMC Rehab
  - NIR
  - ISC
```

**Fellow groups** determine which standing rules apply to whom. The group names
(`NCC_JR`, `NCC_SR`, `STROKE`, `CCM`, `NH`) must match those referenced in the
standing config. If a group isn't present this year, omit it — any rules
referencing it will be silently skipped.

**Shifts** are the complete set of weekly rotations. Every shift that appears in
a standing rule must be listed here.

## 3. Understanding the standing rules

The standing config (`config/standing/stanford-fellowship.yaml`) encodes the
fellowship program's scheduling policies. Each rule has:

```yaml
- name: descriptive_name       # Human-readable identifier
  kind: rule_type              # Which encoder handles it
  active: true                 # Set false to disable
  strength: hard               # hard = must satisfy, soft = penalized if violated
  fellow_groups: [NCC_JR]      # Who this applies to
  # ... rule-specific parameters
```

### Rule kinds reference

#### `full_assignment`
Every fellow in scope must be assigned exactly one shift per week (no gaps).

```yaml
- name: full_assignment_for_owned_schedules
  kind: full_assignment
  active: true
  strength: hard
  fellow_groups: [NCC_JR, NCC_SR, STROKE]
```

#### `ncc_coverage`
Staffing constraints for the NCC teams each week: minimum/maximum fellows on
NCC1, NCC2, and Swing.

```yaml
- name: ncc_coverage
  kind: ncc_coverage
  strength: hard
  fellow_groups: [NCC_JR, NCC_SR, STROKE, CCM, NH]
  max_ncc_fellows: 3              # At most 3 on NCC1+NCC2 combined
  max_ncc_plus_swing_fellows: 4   # At most 4 on NCC1+NCC2+Swing
  swing_deficit: 2                # Allow up to 2 weeks with no Swing fellow
```

#### `max_consecutive`
No fellow has more than K consecutive weeks on any shift in the given set.

```yaml
- name: core_icu
  kind: max_consecutive
  strength: hard
  fellow_groups: [NCC_JR, NCC_SR, STROKE, NH]
  shifts: [NCC1, NCC2, Swing, SICU, MSICU, Stroke]
  weeks: 8                        # At most 8 consecutive
```

#### `all_or_none_block`
A shift must be assigned in complete blocks of K weeks — either all K weeks or
none within a block boundary.

```yaml
- name: msicu_four_week_blocks
  kind: all_or_none_block
  strength: hard
  fellow_groups: [NCC_JR, NCC_SR]
  shifts: [MSICU]
  block_size: 4
```

#### `block_shift_set_choice`
Within each block, if a fellow does any NCC, they must commit to one team for
the full block. The `choices` list specifies which shift combinations are valid.

```yaml
- name: ncc_two_week_block_choice
  kind: block_shift_set_choice
  strength: hard
  fellow_groups: [NCC_JR, NCC_SR, CCM]
  block_size: 2
  choices:
    - [NCC1, Swing]      # Team 1: NCC1 + Swing weeks
    - [NCC2, Swing]      # Team 2: NCC2 + Swing weeks
  allow_none: true       # Fellow can also do zero NCC in a block
```

#### `service_profile`
The main workhorse for per-group scheduling targets. Combines several
sub-constraints:

```yaml
- name: ncc_jr_service_profile
  kind: service_profile
  strength: soft                 # Soft = penalized but not required
  fellow_groups: [NCC_JR]
  zero_shifts:                   # Shifts this group never does
    - NS
    - Stroke
  totals:                        # Year-long shift count targets
    - shifts: [MSICU]
      relation: exactly
      weeks: 20
    - shifts: [Swing, NCC1, NCC2]
      relation: exactly
      weeks: 12
  window_totals:                 # Targets within a specific window
    - shifts: [MSICU]
      relation: exactly
      weeks: 4
      window: [0, 4]            # Weeks 0-3 (first block)
  active_blocks:                 # Conditional block constraints
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

#### `jr_ncc_before_swing`
Junior fellows must accumulate N weeks of NCC1/NCC2 before their first Swing
week.

```yaml
- name: junior_ncc_before_swing
  kind: jr_ncc_before_swing
  strength: hard
  fellow_groups: [NCC_JR]
  ncc_weeks: 4
```

#### `ncc_stroke_oversight`
At least one fellow from the scope must be on NCC1 or NCC2 each week. Ensures
continuous NCC oversight.

#### `comparable_half_year_distribution`
MSICU and NCC+Swing counts shouldn't differ by more than 4 between the first
and second halves of the year. Prevents front/back-loading.

#### `minimize_uncovered_shift_weeks`
Minimizes weeks where no one is assigned to a given shift (typically Swing).
This uses strength `minimize`, which is encoded as a soft penalty per uncovered
week.

## 4. Hard vs soft constraints

The solver has two constraint strengths:

- **Hard** — must be satisfied. If no valid schedule exists under the hard
  constraints, the solver reports INFEASIBLE.
- **Soft** — violations incur a weighted penalty. The solver minimizes the total
  weighted penalty via linear scan.

The `strength` field in the YAML controls this. A practical workflow:

1. Start with structural rules as `hard` (full_assignment, ncc_coverage,
   max_consecutive, block structures).
2. Set service profiles and preferences as `soft`.
3. If the solver is INFEASIBLE, soften the most restrictive hard rule.
4. If the penalty is too high, consider whether a soft rule should be tightened.

## 5. Night call configuration

Night call parameters are currently specified in code
(`NightSolverConfig` in `scheduler/night_call_solver.py`):

```python
NightSolverConfig(
    total_nights={
        "Cindy Wong": 20,
        "Alex Hanson": 20,
        "Aditya Srivatsan": 60,
        # ...
    },
    friday_nights={
        "Raya Aliakbar": 4,
        "Aditya Srivatsan": 8,
        # ...
    },
    ccm_fellows=frozenset({...}),
    holiday_dates=(...),
)
```

The full schedule solver (`--solver schedule`) encodes these as hard constraints:
exact total night counts per fellow, exact Friday night counts, no 3 consecutive
nights, and service-based blocking (fellows on SICU/Vac/NS can't do nights).

Night policy criteria control how night/service conflicts are handled:

| Criterion | Meaning | Default |
|-----------|---------|---------|
| `anaesthesia` | Anaesthesia fellow shouldn't work weekday nights | hard |
| `clinic` | Clinic fellow shouldn't work weekday nights | soft |
| `stroke` | Stroke fellow shouldn't work weekday nights | soft (weight 5) |
| `friday_weekend_ncc1` | Friday night + Weekend NCC1 same fellow | hard |
| `sunday_following` | Sunday night followed by non-preferred service | hard |

Pass `--hard` to control which criteria are hard-enforced:

```bash
--hard anaesthesia,friday_weekend_ncc1,sunday_following
```

## 6. Weekend call configuration

Weekend call parameters are in `WeekendSolverConfig`:

- **NCC totals** per fellow (total weekend NCC1 + NCC2 assignments for the year)
- **Stroke totals** per fellow
- **Stroke cohort** bounds (min/max per cohort member, total)
- **Spacing** — no two consecutive weekend assignments, at most 2 in any 4-week
  window

The full solver links weekend eligibility to weekly shifts dynamically: fellows
on SICU or Vac can't do weekend call that week.

## 7. Running the solver

All commands assume you're in the `new_approach/` directory with
`PYTHONPATH=../src:src`.

### Full schedule (recommended)

Jointly solves weekly shifts + weekends + nights:

```bash
PYTHONPATH=../src:src uv run python -m parafrost_scheduler.cli \
  --solver schedule \
  --annual-config ../config/annual/my-2025-2026.yaml \
  --standing-config ../config/standing/stanford-fellowship.yaml \
  --hard anaesthesia,friday_weekend_ncc1,sunday_following \
  schedule_output.csv
```

Options:
- `--max-soft N` — cap the search at penalty N (skip optimization below this)
- `--no-optimize` — find any feasible solution without minimizing penalty
- `--stroke-weight 5` — weight for stroke criterion in night policy
- `--roundingsat-path /path/to/roundingsat` — custom solver binary location

### Weekend + night only (from existing CSV)

If you already have weekly assignments in a CSV and just want weekend + night
optimization:

```bash
PYTHONPATH=../src:src uv run python -m parafrost_scheduler.cli \
  --solver joint \
  --weekend-csv ../weekend_call.csv \
  --hard anaesthesia,friday_weekend_ncc1,sunday_following \
  night_output.csv
```

This writes two files: `night_output.csv` (night assignments) and
`night_output.weekend.csv` (weekend assignments).

### Night only (from CSV with weekends)

If the CSV already has weekend columns, solve just the night assignments:

```bash
PYTHONPATH=../src:src uv run python -m parafrost_scheduler.cli \
  --solver roundingsat \
  --hard anaesthesia,friday_weekend_ncc1,sunday_following \
  input_with_weekends.csv night_output.csv
```

## 8. Reading the output

The solver prints progress during optimization:

```
Formula: 20472 vars, 76249 constraints, 5312 soft indicators
Soft penalty upper bound: 33932
Feasible at bound=33932 (0.5s)
Starting coarse scan (step=100)...
  SAT at 33832
  SAT at 33732
  ...
  SAT at 732
  Timeout at 532
Starting fine scan (532..627, step=5)...
  SAT at 627
  Timeout at 622
Optimal soft penalty: 627
```

Each SAT probe takes under a second for feasible bounds. When the solver hits
a timeout or UNSAT, it's near the optimum — the true minimum is between the
last SAT bound and the first failure.

The output CSV contains one row per week with columns for each fellow (their
weekly shift), followed by weekend roles and night roles.

## 9. Tuning tips

**Problem is INFEASIBLE:**
- Check which hard constraints are over-constrained. Common culprits:
  `service_profile` totals that don't add up to the number of weeks, or
  `ncc_coverage` limits that conflict with the number of available fellows.
- Try setting the most rigid `service_profile` to `strength: soft`.

**Penalty is too high:**
- Look at which soft constraints dominate the penalty. The solver reports the
  total but not the breakdown (yet). You can check by temporarily making
  specific soft rules inactive.
- Increase weights on the most important criteria. The default weekly soft
  weight is 10 and night policy weights are 1-5.

**Solver is too slow:**
- Increase `coarse_step` (default 5) to scan faster with less precision.
- Reduce `fine_timeout` to skip probes that take too long.
- For very large instances, increase `coarse_timeout` to give harder probes more
  time.

**Testing configuration changes:**
- Use `small-2025-2026.yaml` with `num_weeks=4` for fast iteration.
- The small config omits STROKE fellows and several shifts, so some rules won't
  fire. The solver handles this gracefully (rules referencing missing groups or
  shifts are skipped).

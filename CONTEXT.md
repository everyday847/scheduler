# Scheduler Context

This repo models a fellowship schedule as domain rules that are eventually encoded for a solver. The language here keeps scheduling policy separate from solver mechanics.

## Language

**Schedule**:
A 52-week assignment of fellows to shifts.

**Fellow**:
A clinician who may be assigned to one shift in a week.

**Fellow Group**:
A named cohort declared by Annual YAML and used by Constraints, such as junior NCC, senior NCC, Stroke, CCM, NH, or Lia. The legal group names for a Schedule come from that Schedule's Annual YAML.

**Shift**:
A named weekly assignment option, such as NCC1, NCC2, Swing, MICU, Stroke, or Vacation.

**Block**:
A contiguous span of weeks treated as a scheduling unit.

**Constraint**:
A rule that restricts which schedules are valid or preferred.
_Avoid_: ad hoc Z3 expression

**Solver Invariant**:
A constraint required to make the schedule representation coherent for optimization, independent of fellowship policy.
_Avoid_: basic rule, fundamental rule

**Standing Rule**:
A constraint expected to persist across schedule years because it reflects fellowship policy or rotation structure.
_Avoid_: permanent rule

**Annual Rule**:
A constraint specific to one schedule year, fellow, date, request, or exception.
_Avoid_: one-off rule, contingent rule

**External Coverage Pool**:
A set of interchangeable outside fellows who contribute coverage blocks without needing individualized schedule semantics.
_Avoid_: named fellow cohort

**Management Mode**:
How a **Fellow**'s weekly schedule is determined. A fellow is **Imported** when their weekly schedule is frozen exactly as supplied by an external workbook (empty weeks stay empty; their per-fellow weekly **Constraints** are skipped because the schedule is managed elsewhere), or **Managed** when the solver assigns their weekly schedule and **Annual Rules** may pin specific weeks (vacation, exam, conference). Nights and weekends are solver-assigned for both modes. The mode is set from provenance (a workbook import makes a fellow Imported) and resolved in exactly one place, never re-inferred per call site.
_Avoid_: locked fellow, frozen fellow (as ad hoc, unscoped terms)

## Relationships

- A **Schedule** assigns each **Fellow** to zero or one **Shift** per week unless a specific **Constraint** allows an exception.
- A **Fellow Group** selects cohorts of **Fellows** for **Standing Rules** and **Annual Rules**.
- An **External Coverage Pool** may be represented by placeholder **Fellows** when the individual identities do not matter to the **Schedule**.
- A **Block** contains one or more consecutive weeks.
- Each **Fellow** has a **Management Mode** (Imported or Managed) that governs how their weekly schedule is produced.
- A **Solver Invariant** is independent of **Fellow Group** policy.
- A **Standing Rule** may mention **Fellow Groups**, **Shifts**, and **Blocks**.
- An **Annual Rule** may mention specific **Fellows**, dates, vacation requests, supervision requirements, or exam weeks.

## Example Dialogue

> **Dev:** "Is 'people cannot be in two places at once' an NCC policy?"
> **Domain expert:** "No. That is a **Solver Invariant**. NCC four-week block structure is a **Standing Rule**, and Adam needing Victoria supervision this year is an **Annual Rule**."
>
> **Dev:** "Do we care which CCM fellow covers a month?"
> **Domain expert:** "No. CCM is an **External Coverage Pool** for our purposes: someone covers a consecutive block, but identities are interchangeable."

## Flagged Ambiguities

- "Rule" and "constraint" are often used interchangeably in code. Use **Constraint** for the general concept, then classify it as a **Solver Invariant**, **Standing Rule**, or **Annual Rule** when discussing where it belongs.

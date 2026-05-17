# Scheduler Context

This repo models a fellowship schedule as domain rules that are eventually encoded for a solver. The language here keeps scheduling policy separate from solver mechanics.

## Language

**Schedule**:
A 52-week assignment of fellows to shifts.

**Fellow**:
A clinician who may be assigned to one shift in a week.

**Fellow Type**:
A program role used to group fellows for rules, such as junior NCC, senior NCC, Stroke, CCM, NH, or Lia.

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

## Relationships

- A **Schedule** assigns each **Fellow** to zero or one **Shift** per week unless a specific **Constraint** allows an exception.
- A **Fellow Type** selects groups of **Fellows** for **Standing Rules** and **Annual Rules**.
- An **External Coverage Pool** may be represented by placeholder **Fellows** when the individual identities do not matter to the **Schedule**.
- A **Block** contains one or more consecutive weeks.
- A **Solver Invariant** is independent of **Fellow Type** policy.
- A **Standing Rule** may mention **Fellow Types**, **Shifts**, and **Blocks**.
- An **Annual Rule** may mention specific **Fellows**, dates, vacation requests, supervision requirements, or exam weeks.

## Example Dialogue

> **Dev:** "Is 'people cannot be in two places at once' an NCC policy?"
> **Domain expert:** "No. That is a **Solver Invariant**. NCC four-week block structure is a **Standing Rule**, and Adam needing Victoria supervision this year is an **Annual Rule**."
>
> **Dev:** "Do we care which CCM fellow covers a month?"
> **Domain expert:** "No. CCM is an **External Coverage Pool** for our purposes: someone covers a consecutive block, but identities are interchangeable."

## Flagged Ambiguities

- "Rule" and "constraint" are often used interchangeably in code. Use **Constraint** for the general concept, then classify it as a **Solver Invariant**, **Standing Rule**, or **Annual Rule** when discussing where it belongs.

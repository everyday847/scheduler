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
A named weekly assignment option, such as NCC1, NCC2, Swing, MICU, Stroke, or Vacation. A Shift carries **Shift Attributes** that drive scheduling policy.

**Shift Attribute**:
A named, institution-contingent property of a **Shift** that selects an already-implemented scheduling behavior — for example, that a shift blocks its holder from weekend call, blocks certain weekday nights, or makes the following week non-preferred for a Sunday-night holder. The *presence* of an attribute on a shift is a static fact (a **Standing Rule**); its enforcement **Strength** is a separately-tunable dial with a Standing default that a run may override. An attribute may carry parameters (e.g. which nights it blocks).
_Avoid_: hardcoded shift-name set, blocked-shift list

**Strength**:
How forcefully a **Constraint** (including a **Shift Attribute**'s behavior) is enforced: **hard** (must hold) or **soft** with a weight (a penalty the solver minimizes). Strength is orthogonal to a constraint's tier — a static **Shift Attribute** may be soft, and a transient **Annual Rule** may be hard. Strength carries a Standing default and is overridable per run.
_Avoid_: priority, importance

**Block**:
A contiguous span of weeks treated as a scheduling unit.

**Constraint**:
A rule that restricts which schedules are valid or preferred.
_Avoid_: ad hoc Z3 expression

**Criterion**:
A function over a **Schedule**'s assignments whose value distinguishes schedules: under a hard **Strength** some values are strictly forbidden, under a soft **Strength** some values are penalized relative to others. A Criterion may judge a single assignment or a relationship among several (e.g. a night relative to the following week's shift, or to a weekend role the same fellow holds). Evaluating a Criterion is distinct from presenting it to humans.
_Avoid_: red cell, violation (a violation is a forbidden/penalized *value* of a Criterion, not the Criterion itself)

**Solver Invariant**:
A constraint required to make the schedule representation coherent for optimization, independent of fellowship policy.
_Avoid_: basic rule, fundamental rule

**Standing Rule**:
A constraint expected to persist across schedule years because it reflects fellowship policy or rotation structure.
_Avoid_: permanent rule

**Annual Rule**:
A constraint specific to one schedule year, fellow, date, request, or exception.
_Avoid_: one-off rule, contingent rule

**Supervision**:
A **Constraint** that when a *supervisee* **Fellow** is on a supervised shift-set in a given week, at least one qualifying *supervisor* **Fellow** must be on that same shift-set that week. Supervisee and supervisor roles are named per rule (selectors), not global fellow properties — a fellow may be a supervisee on one shift-set and irrelevant on another. Optionally windowed; its **Strength** and tier (**Standing** when structural and all-year, **Annual** when windowed and roster-specific) follow from its parameters, not from the relationship itself.
_Avoid_: pairing (the relationship is asymmetric — a supervisor is required when a supervisee is present, not vice versa)

**External Coverage Pool**:
A set of interchangeable outside fellows who contribute coverage blocks without needing individualized schedule semantics.
_Avoid_: named fellow cohort

**Management Mode**:
How a **Fellow**'s weekly schedule is determined. A fellow is **Imported** when their weekly schedule is frozen exactly as supplied by an external workbook (empty weeks stay empty; their per-fellow weekly **Constraints** are skipped because the schedule is managed elsewhere), or **Managed** when the solver assigns their weekly schedule and **Annual Rules** may pin specific weeks (vacation, exam, conference). Nights and weekends are solver-assigned for both modes. The mode is set from provenance (a workbook import makes a fellow Imported) and resolved in exactly one place, never re-inferred per call site.
_Avoid_: locked fellow, frozen fellow (as ad hoc, unscoped terms)

## Relationships

- A **Schedule** assigns each **Fellow** to zero or one **Shift** per week unless a specific **Constraint** allows an exception.
- A **Shift** carries zero or more **Shift Attributes**; the set of attribute *types* is a fixed, code-defined vocabulary, while which shifts carry which attributes (and at what **Strength**) is configuration.
- A **Shift Attribute**'s presence is a **Standing Rule**; its **Strength** has a Standing default and may be overridden transiently per run.
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
- "Violation" / "red cell" were used to mean a **Criterion**. Resolved: a Criterion is the function; a *violation* is a forbidden or penalized value of it. Presentation (e.g. red Excel cells) consumes evaluated Criterion values and is a separate concern from the Criterion's meaning — current display is coupled to what spreadsheets support, which is an implementation limitation, not part of the domain.
- A constraint's **tier** (Solver Invariant / Standing Rule / Annual Rule — where it lives, how often it changes) is orthogonal to its **Strength** (hard, or soft-with-weight). Do not conflate "rarely configured" with "hard."

# Scheduler Context

This repo models a fellowship schedule as domain rules that are eventually encoded for a solver. The language here keeps scheduling policy separate from solver mechanics.

## Language

**Schedule**:
A full year (≈52 weeks) of assignments across three layers: each **Fellow**'s **Weekly Assignment**, **Weekend Role**, and **Night** coverage. The three layers occupy disjoint time slots (weekday-days, weekend-days, nights), so a fellow may simultaneously hold a Weekly Assignment, a Weekend Role, and one or more Nights in the same week without conflict.
_Avoid_: a 52-week fellow→shift assignment (understates the three layers)

**Weekly Assignment**:
The Mon–Fri service a **Fellow** holds in a week — exactly one **Shift** (NCC1, MICU, Vac, …), or none. The layer **Management Mode** governs.

**Weekend Role**:
A weekend-specific coverage assignment for a week: Weekend NCC1, Weekend NCC2, or Weekend Stroke. Distinct from the weekday **Shift** of the same name — "Weekend NCC1" the role is not "NCC1" the Weekly Assignment, and a fellow may hold one without the other. Solver-assigned in both **Management Modes**.
_Avoid_: conflating Weekend NCC1 (role) with NCC1 (shift)

**Night**:
A single day's overnight call assignment for a **Fellow** (one fellow per night). Solver-assigned in both **Management Modes**. Whether a Night is permitted or penalized depends on the holder's **Weekly Assignment** (its **Shift Attributes**) and **Weekend Role** that week — these relationships are **Criteria**.

**Fellow**:
A clinician who may hold a **Weekly Assignment**, a **Weekend Role**, and one or more **Nights** in a given week.

**Fellow Group**:
A named cohort declared by Annual YAML and used by Constraints, such as junior NCC, senior NCC, Stroke, CCM, NH, or Lia. The legal group names for a Schedule come from that Schedule's Annual YAML.

**Shift**:
A named **Weekly Assignment** option, such as NCC1, NCC2, Swing, MICU, Stroke, or Vacation. A Shift carries **Shift Attributes** that drive scheduling policy (including how it permits or penalizes **Night** and **Weekend Role** assignments). A Shift is a weekday-service concept; it is not a **Weekend Role**.

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
A source of coverage whose individual people don't matter to the **Schedule**, only the quantity of service it supplies and the cadence it supplies it in. A pool is represented by one or more placeholder **Fellow** columns, each a **Capacity Bucket** — a collapse of several real people, so a column may legitimately hold more service (weeks, **Weekend Roles**) than any one person could. The pool's total load divides *proportionately* across its buckets; differing per-bucket loads reflect capacity, not individual preference. Collapsing many people into few buckets is itself the symmetry break (fewer **Fellows** to permute). Pooling is independent of **Management Mode**: a pool is generically **Managed** (the solver arranges its blocks) and may *later* become **Imported** if external parties commit fixed coverage.
_Avoid_: named fellow cohort, treating a bucket as a real person

**Capacity Bucket**:
A placeholder **Fellow** column standing for part of an **External Coverage Pool**. Distinguished from other buckets by its coverage capacity and its **Block Cadence**, not by identity. (CCM is two "core" buckets at ~4–5-week blocks — two because they sometimes cover simultaneously — and one "elective" bucket at 2-week blocks.)

**Block Cadence**:
The **Block** length in which a **Capacity Bucket** (or a rotation generally) delivers its service — e.g. 4-week core CCM blocks vs. 2-week elective CCM blocks.

**Management Mode**:
How a **Fellow**'s **Weekly Assignment** layer is determined. A fellow is **Imported** when their Weekly Assignments are frozen exactly as supplied by an external workbook (empty weeks stay empty; their per-fellow weekly **Constraints** are skipped because the layer is managed elsewhere), or **Managed** when the solver assigns their Weekly Assignments and **Annual Rules** may pin specific weeks (vacation, exam, conference). **Weekend Roles** and **Nights** are solver-assigned for both modes — Management Mode governs only the Weekly Assignment layer. The mode is set from provenance (a workbook import makes a fellow Imported) and resolved in exactly one place, never re-inferred per call site.
_Avoid_: locked fellow, frozen fellow (as ad hoc, unscoped terms)

## Relationships

- A **Schedule** has three layers per week: a **Weekly Assignment** (zero or one **Shift**), zero or one **Weekend Role**, and zero or more **Nights** per **Fellow**.
- The three layers are independent in time, so a **Fellow** may hold all three in one week; **Criteria** express the desirable/forbidden *relationships* across layers (e.g. a **Night** relative to that week's **Shift** or **Weekend Role**).
- **Management Mode** governs only the **Weekly Assignment** layer; **Weekend Roles** and **Nights** are solver-assigned for every fellow.
- A **Shift** carries zero or more **Shift Attributes**; the set of attribute *types* is a fixed, code-defined vocabulary, while which shifts carry which attributes (and at what **Strength**) is configuration.
- A **Shift Attribute**'s presence is a **Standing Rule**; its **Strength** has a Standing default and may be overridden transiently per run.
- A **Fellow Group** selects cohorts of **Fellows** for **Standing Rules** and **Annual Rules**.
- An **External Coverage Pool** is represented by one or more **Capacity Buckets** (placeholder **Fellows**); the pool's load divides proportionately across them and each bucket has its own **Block Cadence**.
- A **Capacity Bucket** may hold more service than any single real person, because it collapses several; its differing load is capacity, not preference.
- Pooling is orthogonal to **Management Mode**: a pool is generically **Managed** and may later become **Imported** when external coverage is committed.
- A **Block** contains one or more consecutive weeks; a **Block Cadence** is the block length a bucket or rotation uses.
- Each **Fellow** has a **Management Mode** (Imported or Managed) that governs how their weekly schedule is produced.
- A **Solver Invariant** is independent of **Fellow Group** policy.
- A **Standing Rule** may mention **Fellow Groups**, **Shifts**, and **Blocks**.
- An **Annual Rule** may mention specific **Fellows**, dates, vacation requests, supervision requirements, or exam weeks.

## Example Dialogue

> **Dev:** "Is 'people cannot be in two places at once' an NCC policy?"
> **Domain expert:** "No. That is a **Solver Invariant**. NCC four-week block structure is a **Standing Rule**, and Adam needing Victoria supervision this year is an **Annual Rule**."
>
> **Dev:** "Do we care which CCM fellow covers a month?"
> **Domain expert:** "No. CCM is an **External Coverage Pool**: someone covers a consecutive block, but identities are interchangeable."
>
> **Dev:** "Then why does one CCM column do 22 weekends and another do 4? Aren't those just unfair?"
> **Domain expert:** "They aren't people — they're **Capacity Buckets**. One bucket collapses several real CCM fellows, so it can carry far more service than a human could. The 22 is proportionate to that bucket's weeks. What distinguishes the buckets is **Block Cadence** — the two core buckets run 4-week blocks, the elective bucket runs 2-week blocks — not who they are."
>
> **Dev:** "Raya is on **Shift** Stroke, holds the **Weekend Role** Weekend Stroke, and takes Sunday **Night** — all the same week. Is that a conflict?"
> **Domain expert:** "No. Those are three layers in disjoint time. Whether the Sunday Night is good is a **Criterion** relating the Night to her Weekend Role — and we decided the weekend-Stroke holder taking Sunday is fine, so it's not penalized."
>
> **Dev:** "Helena must have a senior with her on early Stroke weeks. Is that a **Shift Attribute**?"
> **Domain expert:** "No — a Shift Attribute is about one Shift alone. This is **Supervision**: a relationship between **Fellows** on the same Shift. It's an **Annual Rule** because the roster changes yearly, and it's windowed to early weeks."

## Flagged Ambiguities

- "Rule" and "constraint" are often used interchangeably in code. Use **Constraint** for the general concept, then classify it as a **Solver Invariant**, **Standing Rule**, or **Annual Rule** when discussing where it belongs.
- "Violation" / "red cell" were used to mean a **Criterion**. Resolved: a Criterion is the function; a *violation* is a forbidden or penalized value of it. Presentation (e.g. red Excel cells) consumes evaluated Criterion values and is a separate concern from the Criterion's meaning — current display is coupled to what spreadsheets support, which is an implementation limitation, not part of the domain.
- A constraint's **tier** (Solver Invariant / Standing Rule / Annual Rule — where it lives, how often it changes) is orthogonal to its **Strength** (hard, or soft-with-weight). Do not conflate "rarely configured" with "hard."

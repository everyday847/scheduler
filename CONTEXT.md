# Scheduler Context

This repo models a fellowship schedule as domain rules that are eventually encoded for a solver. The language here keeps scheduling policy separate from solver mechanics.

This glossary is the **tool's** institution-agnostic language. It defines the *kinds* of things the scheduler reasons about (Fellow Groups, Shifts, service requirements, …), not the specific groups, shifts, or people of any one fellowship. A concrete instance (which groups and shifts exist, their counts and cadences, who supervises whom) lives in `config/` — `config/standing/` for program structure, `config/annual/` for a given year — which is the authoritative source for instance facts. Where a definition shows a concrete name, it is a clearly-marked illustration (`e.g.`), never part of the definition.

## Language

**Schedule**:
A full year (≈52 weeks) of assignments across three layers: each **Fellow**'s **Weekly Assignment**, **Weekend Role**, and **Night** coverage. The three layers occupy disjoint time slots (weekday-days, weekend-days, nights), so a fellow may simultaneously hold a Weekly Assignment, a Weekend Role, and one or more Nights in the same week without conflict.
_Avoid_: a 52-week fellow→shift assignment (understates the three layers)

**Weekly Assignment**:
The Mon–Fri service a **Fellow** holds in a week — exactly one **Shift** (e.g. NCC1, MICU, Vac), or none. The layer **Management Mode** governs.

**Weekend Role**:
A weekend-specific coverage assignment for a week, drawn from an instance's defined weekend roles (e.g. Stanford: Weekend NCC1, Weekend NCC2, Weekend Stroke). Distinct from the weekday **Shift** of the same name — a "Weekend NCC1" role is not the "NCC1" Weekly Assignment, and a fellow may hold one without the other. Solver-assigned in both **Management Modes**.
_Avoid_: conflating Weekend NCC1 (role) with NCC1 (shift)

**Night**:
A single day's overnight call assignment for a **Fellow** (one fellow per night). Solver-assigned in both **Management Modes**. Whether a Night is permitted or penalized depends on the holder's **Weekly Assignment** (its **Shift Attributes**) and **Weekend Role** that week — these relationships are **Criteria**.

**Fellow**:
A clinician who may hold a **Weekly Assignment**, a **Weekend Role**, and one or more **Nights** in a given week.

**Fellow Group**:
A named cohort of **Fellows** declared by a Schedule's config and referenced by **Constraints**. The legal group names for a Schedule come from that Schedule's config — they are instance data, not fixed by the tool. (e.g. Stanford 2026 has junior/senior NCC, Stroke, CCM, NH.)

**Shift**:
A named **Weekly Assignment** option, such as NCC1, NCC2, Swing, MICU, Stroke, or Vacation. A Shift carries **Shift Attributes** that drive scheduling policy (including how it permits or penalizes **Night** and **Weekend Role** assignments). A Shift is a weekday-service concept; it is not a **Weekend Role**.

**Shift Attribute**:
A named, institution-contingent property of a **Shift** that selects an already-implemented scheduling behavior — for example, that a shift blocks its holder from weekend call, blocks certain weekday nights, makes the following week non-preferred for a Sunday-night holder, or tolerates being uncovered some weeks (its **Coverage** floor is softly minimized rather than hard). The *presence* of an attribute on a shift is a static fact (a **Standing Rule**); its enforcement **Strength** is a separately-tunable dial with a Standing default that a run may override. An attribute may carry parameters (e.g. which nights it blocks).
_Avoid_: hardcoded shift-name set, blocked-shift list

**Strength**:
How forcefully a **Constraint** (including a **Shift Attribute**'s behavior) is enforced: **hard** (must hold) or **soft** with a weight (a penalty the solver minimizes). Strength is orthogonal to a constraint's tier — a static **Shift Attribute** may be soft, and a transient **Annual Rule** may be hard. Strength carries a Standing default and is overridable per run.
_Avoid_: priority, importance

**Bounded-Preference Policy**:
A composite **Strength** shape that pairs a HARD bound on a count with a SOFT penalty for acceptable-but-non-ideal outcomes inside that bound — giving the solver both a guarantee and a gradient. Recurs across unrelated constraints: a **Vacation Request** rank cutoff (top-N picks hard, remaining picks soft) and a per-fellow service-count band (hard `[target±tol]`, soft nudge toward the exact target inside the band) are the same shape. The soft part may be flat (each overage costs equally, as in vacation) or graded by deviation (as in the band).
_Avoid_: flat soft "exactly N" (no gradient — equal cost for off-by-1 and off-by-many)

**Vacation Request**:
A **Fellow**-supplied preferred "Vac" week, ranked per fellow. Honored under a **Bounded-Preference Policy**: a fellow's top-N requests are HARD pins, lower-ranked ones SOFT. The only fellow-originated input the scheduler models — there is no general "request" concept beyond vacation.
_Avoid_: request (as a general term — it has no project-specific meaning outside vacation)

**Coverage**:
The per-week staffing requirement for a **Shift**, expressed as a band over the eligible **Fellows**: a floor (at least N on the shift) and a ceiling (at most M). Whether an unmet floor is hard or best-effort is not a separate kind of Coverage — it is the **Strength** of the floor, which for a shift that tolerates gaps (a **Shift Attribute**) is softly minimized rather than required.
_Avoid_: separate "required" vs "best-effort" coverage concepts (it's one Coverage, differentiated by Strength)

**Block**:
A contiguous span of weeks treated as a scheduling unit.

**Home Service**:
The service a **Fellow Group** predominantly staffs and returns to across the year; away-from-home **Blocks** are defined relative to it. Which services are home — and how many a Schedule manages — is instance data, not fixed by the tool. (e.g. Stanford 2026 jointly manages two: NCC, and Stroke spanning Stroke / Telestroke-Clinic / Clinic-Elective.)

**Constraint**:
A rule that restricts which schedules are valid or preferred. A Constraint and a **Criterion** are the *same thing* differentiated by **Strength**: every rule has two co-located manifestations — it is *encoded* (compiled to pseudo-Boolean form for the solver) and *evaluated* (run against a concrete **Schedule** to yield a value) — and its Strength decides what an evaluated violation means (hard ⇒ the Schedule is invalid; soft ⇒ the Schedule is penalized). "Constraint" emphasizes the hard/structural reading; "Criterion" emphasizes the value-bearing reading; the underlying object is one. A rule's behavior is selected from a fixed **Rule Shape** vocabulary and parameterized by config — it is never hardcoded inline in the encoder.
_Avoid_: ad hoc Z3 expression; encoder-only rule (a rule with no evaluate manifestation)

**Criterion**:
A **Constraint** read as a function over a **Schedule**'s assignments whose value distinguishes schedules: under a hard **Strength** some values are strictly forbidden, under a soft **Strength** some values are penalized relative to others. A Criterion may judge a single assignment or a relationship among several (e.g. a night relative to the following week's shift, or to a weekend role the same fellow holds). Evaluation runs against *any* **Schedule**, not only solver output — including an **Imported** fellow's frozen weeks (whose weekly layer the encoder skips) and externally-supplied or hand-edited schedules fed back in for validation. Evaluating a Criterion is distinct from presenting it to humans.
_Avoid_: red cell, violation (a violation is a forbidden/penalized *value* of a Criterion, not the Criterion itself); treating evaluation as solver-output-only

**Rule Shape**:
A code-defined *archetype* of constraint behavior — a parameterized pattern such as "night before a gating service," "weekend-role holder on a given night," "following-week service is non-preferred," "weekend-role alignment," "per-fellow count band," or "block rotation." The set of shapes is a fixed vocabulary owned by code; configuration selects a shape and supplies its parameters (day-of-week filter, gating week offset, **Shift** set, **Weekend Role**, exemption, weight). A rule that fits an existing shape is pure config (no code); a genuinely new shape is one new code location carrying its manifestations and a contract test pinning them. This is the **declarative archetype** of [ADR-0003](docs/adr/0003-shift-attribute-policy.md): config may only select and parameterize behaviors code already knows how to encode, never invent new ones (so it is *not* an open expression language — [ADR-0005](docs/adr/0005-criterion-single-definition.md)).
A Rule Shape has **four co-located manifestations**: *encode* (to the **Constraint Sink**), *evaluate* (against a **Schedule**), a *contract test* pinning the two to agree, and a **Param Descriptor** (see below). All four live in one leaf module; the **Rule Shape Catalog** registers every shape's descriptor + converter as the single dispatch from a config dict to a **Constraint** ([ADR-0008](docs/adr/0008-rule-shape-param-descriptor.md)).
_Avoid_: hardcoded inline encoder logic; arbitrary config expression language; a separate "call rule" channel (see **Param Descriptor**)

**Param Descriptor**:
The declared shape of a **Rule Shape**'s configuration parameters — each param's name, type (shift-set, group-set, fellow, int, enum, week-range, …), allowed options, the **Schedule** layer it acts on (weekly / night / weekend), whether it is hard/soft-able, and its **presentation category**. Co-located in the Rule Shape's leaf as a fourth manifestation, it is the single source from which (a) the converter validates an incoming config dict and (b) the web app's rule editor renders fields — so the Python rule vocabulary and the editing UI cannot drift. The **presentation category** is display metadata only: "call" is **night + weekend** roles grouped for the fellowship director's "call" page, *not* a distinct kind of rule or a separate config channel. Every rule is a `rules:` entry; the call view is a filter over them.
_Avoid_: re-stating param names in the UI by hand; a `call_rules:` channel distinct from `rules:`; treating "call" as a Rule Shape distinction rather than a view

**Constraint Sink**:
The abstract emitter a **Rule**'s encode manifestation writes to — a small interface (`forbid` literals, add a `soft` penalty term, allocate a fresh variable) that the solver's pseudo-Boolean builder satisfies. Rules depend on the sink, not on the concrete builder, so the **Rule Shape** vocabulary carries no dependency on the solver package and the encoder becomes a generic interpreter that walks the rule registry and injects its builder as the sink.
_Avoid_: rules importing the concrete OPB builder directly

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
The idea that a less-experienced *supervisee* **Fellow** on a shift-set should have a qualifying *supervisor* **Fellow** alongside. Supervisee and supervisor roles are named per rule (selectors), not global fellow properties — a fellow may be a supervisee on one shift-set and irrelevant on another. The relationship is asymmetric: a supervisor is wanted *when a supervisee is present*, not vice versa. Supervision is **not one constraint shape** — it splits into two, by the supervised service's supply/demand (this split is the practical realization; the *idea* of supervision is the umbrella):

- **Mandatory qualified coverage ("must"):** on a high-supply service that is always staffed above one (many potential supervisors, ≥N on service every week — e.g. NCC), a supervisor's presence can be *required* — "≥1 qualifying fellow on the shift-set each week." The antecedent is effectively always-true (the service is always staffed), so it reads as an unconditional coverage floor. Hard, **Standing**, all-year. (Instance: the `ncc_stroke_oversight` weekly floor.)
- **Opportunistic windowed supervision ("may"):** on a low-supply service with few supervisors and rarely room for more than one fellow (e.g. Stroke), a supervisor's presence *cannot* be guaranteed — instead, during a **window**, dual-coverage is *permitted but capped so the second slot is the supervised one* (≤1 supervisor + ≤1 supervisee in-window; ≤1 total out). It rewards/permits the supervised pairings that happen to be feasible rather than forcing one. (Instance: the `dual_stroke_window` rule.)

_Avoid_: pairing (the relationship is asymmetric); collapsing the two shapes into one "supervision" constraint (their encodings differ — a coverage floor vs. a windowed cap — and conflating them obscures that "must" needs supply slack while "may" exists precisely because there is none)

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
- A **Constraint** and a **Criterion** are one object read two ways; which reading applies is its **Strength** (hard ⇒ validity, soft ⇒ penalty). The tier (**Solver Invariant** / **Standing Rule** / **Annual Rule**) is orthogonal to both.
- Every **Constraint** has two behavioral manifestations — encode (to the **Constraint Sink**) and evaluate (against a **Schedule**) — selected from the **Rule Shape** vocabulary; the two are co-located and pinned by a contract test ([ADR-0005](docs/adr/0005-criterion-single-definition.md)).
- A **Rule Shape** additionally carries a **Param Descriptor** (a fourth co-located manifestation); the **Rule Shape Catalog** registers each shape's descriptor + dict→**Constraint** converter as the one dispatch point, and serves descriptors to the web app's editor ([ADR-0008](docs/adr/0008-rule-shape-param-descriptor.md)).
- "Call" is a **Param Descriptor** presentation category (night + weekend), not a distinct rule channel: all rules are `rules:` entries; the editor's "call" page filters them.
- A **Rule Shape** is code; its selection and parameters are configuration (the **declarative archetype** of [ADR-0003](docs/adr/0003-shift-attribute-policy.md)). A **Shift Attribute** is the **Shift**-scoped special case of this same select-and-parameterize discipline.
- Evaluation of a **Constraint** runs against any **Schedule**, including an **Imported** fellow's frozen weeks and externally-supplied schedules, not only solver output.

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
> **Dev:** "Helena should have a senior with her on early Stroke weeks. Is that a **Shift Attribute**?"
> **Domain expert:** "No — a Shift Attribute is about one Shift alone. This is **Supervision**, and it's the *opportunistic* ('may') kind: Stroke is low-supply, so we can't *require* a senior is on with her — instead, during the early-weeks **window**, we permit dual-coverage and cap it so the second Stroke slot is a senior. Contrast NCC, where the service is always staffed by several, so there we *do* require a qualifying fellow every week — the 'must' kind. Same supervision idea, two different Rule Shapes, and which one applies is set by whether the service has supply slack."
>
> **Dev:** "Is the Helena one an Annual Rule, since the roster changes yearly?"
> **Domain expert:** "It's parameterized by this year's seniors, but we model it **Standing** — the *structure* (early-window opportunistic Stroke supervision) is how the program runs every year; only the names change. Tier follows the structure's permanence, not how often a selector's contents churn."
>
> **Dev:** "Someone hand-edited the workbook and gave Aditya two **Shifts** in one week. The solver would never do that — do we even check?"
> **Domain expert:** "We should, and that's the point of evaluation running against *any* **Schedule**. 'At most one Shift per week' is a hard **Constraint**; evaluating it on the edited Schedule yields a forbidden value, so the Schedule is invalid. The solver guarantees it on its own output, but an **Imported** or hand-edited Schedule has no such guarantee — same rule, evaluated rather than encoded."
>
> **Dev:** "So is 'night before a Stroke day is discouraged' a **Constraint** or a **Criterion**?"
> **Domain expert:** "Both — it's one rule. Read as a **Criterion** it's soft, so a violation is a penalty. If we hardened its **Strength** it would forbid the value outright. Either way it's the same **Rule Shape** — 'night before a gating service' — parameterized with the Stroke **Shift** and the dual-stroke exemption. None of that lives inline in the encoder; it's config selecting a shape."

## Flagged Ambiguities

- "Rule" and "constraint" are often used interchangeably in code. Use **Constraint** for the general concept, then classify it as a **Solver Invariant**, **Standing Rule**, or **Annual Rule** when discussing where it belongs.
- "Violation" / "red cell" were used to mean a **Criterion**. Resolved: a Criterion is the function; a *violation* is a forbidden or penalized value of it. Presentation (e.g. red Excel cells) consumes evaluated Criterion values and is a separate concern from the Criterion's meaning — current display is coupled to what spreadsheets support, which is an implementation limitation, not part of the domain.
- A constraint's **tier** (Solver Invariant / Standing Rule / Annual Rule — where it lives, how often it changes) is orthogonal to its **Strength** (hard, or soft-with-weight). Do not conflate "rarely configured" with "hard."
- "Rotation" is informal English for a **Block** spent *away* from a **Fellow**'s **Home Service** (e.g. an NCC fellow's MICU block), not a distinct modeled concept — a home-service block is structurally the same object. Use **Block** + **Home Service**; don't mint a "Rotation" type.

# External Coverage Pools as Capacity Buckets

An **External Coverage Pool** (e.g. CCM) is modeled as a small number of placeholder **Fellow** columns called **Capacity Buckets**, each a collapse of several real people. A bucket is sized by *capacity* — it may hold far more service (weeks, **Weekend Roles**) than any single human, and the pool's total load divides proportionately across its buckets. Buckets are distinguished by coverage capacity and **Block Cadence**, never by identity. This supersedes the CCM-specific reasoning in [ADR-0001](./0001-separate-constraint-lifecycles.md).

## Why this shape

The symmetry break *is* the pooling: collapsing N interchangeable people into a few buckets means far fewer **Fellows** to permute than simulating one fellow per covered block (the earlier convention). Because identities don't matter, the only facts the **Schedule** needs from a pool are how much service it supplies and in what block lengths.

A reader will otherwise be confused by the data — e.g. one CCM bucket carrying ~22 weekend-equivalents while another carries ~4. That is not an unfair assignment to a person; it is two buckets of different capacity. Recording this stops someone from "rebalancing" buckets as if they were individuals.

## Pooling is orthogonal to Management Mode

A pool is *generically* **Managed**: the solver arranges its blocks. It may *later* become **Imported** when external services commit fixed coverage (solve → communicate coverage needs → external parties commit → lock in → adjust). The heavy workbook-locking of CCM in recent solves is incidental history of one year's external coordination, **not** an intrinsic property of CCM or of coverage pools generally. Do not treat "CCM is exogenously fixed" as a standing assumption.

## Considered alternatives

- **One simulated fellow per covered block** (the original convention) — rejected: more fellows to permute, worse symmetry, and it implied per-person semantics that don't exist.
- **Model the real CCM people individually** — rejected: their identities are interchangeable to this Schedule; individual semantics would be fictional precision.

## Consequences / known gap

- **Block Cadence is per-bucket, but not yet fully modeled.** The two "core" CCM buckets run ~4–5-week blocks; the "elective" bucket runs 2-week blocks. The current `CCM NCC Block` rule is a single 4-week `block_rotation` over the whole CCM group, so the elective bucket's distinct 2-week cadence is **not expressed**. Flagged as a known limitation — likely addressed next year via a differentiated `Elective_CCM` role/bucket with its own cadence rather than patched into the shared rule.

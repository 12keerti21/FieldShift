# Decision log

Short, dated entries. Why we chose something, and what we rejected.

## 2026-11-12 — Project spine fixed
Framing: crop rotation as a **sequential decision under uncertainty**, not a
recommendation lookup. Every number shown must trace to an equation and a dataset.

## 2026-11-12 - Parameter tables built, ahead of any dataset work
27 crops and 19 pathogens, with a typed validating loader and 68 tests. Chosen
deliberately as the first build: the tables are region-light, cannot be
parallelised, and are what blocks the state simulator later.

Design calls made while building them:

- **Pathogen presence is tagged three ways** (`widespread` / `regional` /
  `quarantine`). Without this, Striga imposed a spurious 3-year interval on every
  cereal including the Asian rice-wheat belt where it does not occur. Unconfirmed
  `regional` organisms are reported but never block a rotation.
- **Severity and regulatory status are separate axes.** A quarantine pest is a
  legal matter; collapsing it into a severity score would have hidden that.
- **Weak-efficacy breaches are cautions, not rejections.** For a 500-host organism
  such as *Macrophomina* no practical rotation gives a clean break, so rejecting
  every breach would have eliminated rotations farmers already run.
- **Return-interval checking closes the cycle** (`cyclic=True` default). A sequence
  that reads clean left-to-right can still replant a host after one year on the
  next pass; this is the standard way a rotation checker gives a false pass.
- **One violation per crop pair.** A four-slot rotation pairs the same two crops
  repeatedly; the first draft emitted 12 findings for one rotation and buried the
  one that mattered. Now the tightest spacing in the cycle is reported once.
- **Deterministic tie-breaking** on pathogen ordering, so the "driving" pathogen
  shown to a farmer does not change between runs.
- **Rejected:** storing a `min_return_interval` on each crop. It is derived from
  the pathogen table instead, so the two cannot drift apart.

Rejected alternative for the models: DSSAT/APSIM. Too heavy to calibrate inside a
hackathon. Going with FAO-56 + RothC + RUSLE, which are published, auditable, and
testable against reference values.

## Open decisions
- [ ] Target region(s) — 2 to 3 contrasting zones, drives which datasets to pre-cache
- [ ] Team composition / who owns which layer
- [ ] Product name (repo is `rotation-dss`, placeholder)
- [ ] Frontend stack (deferred until engine API shape is known)

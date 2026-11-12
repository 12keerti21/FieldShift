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

## 2026-11-12 - Soil texture table added
12 USDA classes with hydraulic and erosion properties, a texture-triangle
classifier, and derived FAO-56 water quantities. 106 tests.

- **Measured fractions override class centroids.** Clay percent drives both the
  RothC CO2 partition and the RUSLE K factor, so discarding a measured value for a
  centroid would corrupt two models at once. `measured_fractions` records which
  source was used so an evidence card can state it.
- **The classifier normalises near-100 sums.** Gridded soil products routinely sum
  to 99 or 101 after their own rounding; rejecting those would throw away usable
  data. Sums far from 100 still raise.
- **Load-time self-check:** every class centroid must classify into its own class.
  A mismatch means the centroid or the classifier is wrong, and both fail silently.
- **Hydraulic properties stay at the centroid even when fractions are measured.**
  Deriving them from texture needs a pedotransfer function (Saxton-Rawls or
  similar), which is a separate decision and should not be smuggled in here.
- **Organic matter is deliberately not a soil parameter.** It enters through the
  RothC carbon state, so improving SOC can feed back into water holding and
  erodibility rather than the soil being treated as static - which is the whole
  point of simulating rotations over years.

## Open decisions
- [ ] Target region(s) — 2 to 3 contrasting zones, drives which datasets to pre-cache
- [ ] Team composition / who owns which layer
- [ ] Product name (repo is `rotation-dss`, placeholder)
- [ ] Frontend stack (deferred until engine API shape is known)

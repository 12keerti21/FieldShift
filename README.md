# rotation-dss  *(working name)*

A crop-rotation decision-support tool for the NASA Space Apps challenge on
soil health and farm resilience.

## The spine

> Crop rotation is not a recommendation problem — it is a **sequential decision
> under uncertainty**. We simulate the soil water / nitrogen / carbon state of a
> specific field forward through 20+ years of *actually observed* NASA weather,
> for every candidate rotation, and report **the bad years** — not the average year.

Non-negotiable: every number on screen traces to an equation and a dataset.
No language model makes a decision; at most it narrates structured model output.

## Planned layers

| Layer | Contents | Status |
|---|---|---|
| Parameters | crop traits, pathogen return intervals, soil texture classes | not started |
| Physical models | FAO-56 dual-Kc water balance, RothC soil carbon, RUSLE erosion | not started |
| State simulator | couples the above; carries soil state across seasons | not started |
| Optimizer | enumeration / DP over multi-year crop sequences | not started |
| Replay | each rotation run through 20–40 observed years → outcome distribution | not started |
| Honesty layer | evidence cards, resolution-mismatch confidence downgrade, guardrails | not started |
| Delivery | map UI, bare-soil × erosive-rain calendar, printable field plan | not started |

Build order and the cut line (what to drop if behind) live in `docs/DECISIONS.md`.

## Layout
```
src/rdss/        engine package  (agro/ models, data/ parameter tables, io/ dataset readers)
tests/           pytest, validated against published reference values where possible
data/raw/        NASA granules — gitignored, never committed
data/reference/  small committed lookup tables
docs/            decision log, method notes
notebooks/       exploration only; nothing authoritative lives here
web/             frontend (stack TBD)
```

## Setup
```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pytest
```

## Status
Foundation only. Datasets and the challenge Resources list are pending —
see `RESOURCES.md`. No dataset-specific code is written until that is filled in.

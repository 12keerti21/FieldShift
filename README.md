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
| Parameters | 27 crops, 19 soilborne pathogens, return-interval constraints | **done** (soil texture classes still to add) |
| Physical models | FAO-56 dual-Kc water balance, RothC soil carbon, RUSLE erosion | not started |
| State simulator | couples the above; carries soil state across seasons | not started |
| Optimizer | enumeration / DP over multi-year crop sequences | not started |
| Replay | each rotation run through 20–40 observed years → outcome distribution | not started |
| Honesty layer | evidence cards, resolution-mismatch confidence downgrade, guardrails | not started |
| Delivery | map UI, bare-soil × erosive-rain calendar, printable field plan | not started |

Build order and the cut line (what to drop if behind) live in `docs/DECISIONS.md`.

## Parameter tables (built)

`src/rdss/data/crops.json` - 27 crops spanning cereals, grain legumes, oilseeds,
fibre, tuber, green manures, cover crops, perennial forage, plus an explicit bare
fallow slot so the cost of a bare window can be quantified rather than assumed.
Each crop carries FAO-56 Kc values and stage lengths, rooting depth and depletion
fraction, thermal thresholds and GDD, N fixation and uptake, residue mass and C:N,
a RothC DPM/RPM ratio, erosion-relevant canopy cover, and trait scores.

`src/rdss/data/pathogens.json` - 19 soilborne pathogens, nematodes, parasitic
weeds and one allelopathy entry, each with its survival structure, persistence and
the return interval it demands.

Both tables are read through `rdss.data.load_registry()`, which validates them on
load and raises rather than letting bad agronomy through silently. Review the
derived constraints without reading code:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/show_constraints.py
```

Four design choices worth knowing before you edit these files:

- **No prices anywhere.** Every marketed crop sets `price_local_required: true`.
  Margin cannot be computed until local market data is supplied.
- **Rotation is not credited where it does nothing.** Cereal rusts and rice blast
  are in the table with `rotation_effective: false`, specifically so the tool can
  never claim a break-crop benefit against an airborne pathogen.
- **Precautionary, not prescriptive.** Patchily distributed organisms are tagged
  `regional`; they are reported but do not block a rotation until a local source
  confirms them. Quarantine pests are flagged on a separate axis, because that is
  a legal question for the plant protection authority rather than a modelling one.
- **The rotation cycle is closed by default.** The checker wraps the last slot back
  to the first, which is the only way to catch a crop that returns too soon on the
  next pass.

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

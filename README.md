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
| Parameters | 27 crops, 19 soilborne pathogens, 12 soil texture classes | **done** |
| Physical models | FAO-56 ET0 and dual-Kc water balance **done**; RothC, RUSLE pending | in progress |
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

`src/rdss/data/soils.json` - the 12 USDA texture classes with field capacity,
wilting point, saturation, readily evaporable water, conductivity, bulk density and
NRCS hydrologic group, plus derived FAO-56 quantities (TAW, RAW, TEW). The USDA
texture triangle classifier in `soils.py` is the path gridded soil products take,
since they give sand/silt/clay fractions rather than class names - and measured
fractions are kept rather than replaced by the class centroid, because clay percent
feeds the RothC carbon partition and the RUSLE K factor directly.

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

## The weather contract

`src/rdss/agro/climate.py` is the seam between NASA data and the science. Models
import from it and never touch a granule, a variable name or a product version, so
when the dataset layer changes only the adapters move. Two consequences worth
knowing:

- **Required variables are only tmax, tmin and precipitation** - the three no
  plausible source omits. Everything else is optional, because ET0 can be computed
  with documented substitutions, and which substitutions were used is recorded on
  the result rather than hidden.
- **The series must be gap-free daily, and that is enforced.** A water balance
  carries soil moisture from one day to the next, so a silently skipped day does
  not cost one day of accuracy - it corrupts every day after it. Non-finite values
  and out-of-range sentinels (`-9999`) are rejected here so they have to be handled
  in the adapter, where the fill method can be recorded.

## Why the dual crop coefficient

The water balance splits crop water use into transpiration through the canopy
(Kcb) and evaporation straight off the soil surface (Ke). That split is the reason
it is worth building rather than using a single Kc: **transpiration is water
converted into crop growth, soil evaporation is water simply lost.** The resulting
`evaporation_fraction` is the quantified cost of leaving ground uncovered, and it
is invisible to a single-coefficient model.

Illustrative output on synthetic semi-arid weather, 120 days from monsoon onset on
a clay loam (`scripts/show_water_balance.py`):

| | evaporated | run off | transpired |
|---|---|---|---|
| bare fallow | 206 mm | 213 mm | 0 mm |
| mungbean | 116 mm | 126 mm | 198 mm |
| sunn hemp | 54 mm | 84 mm | 327 mm |
| sorghum-sudangrass | 52 mm | 75 mm | 355 mm |

Water a cover crop uses is not water wasted - it is transpired through a canopy
that is also building residue and holding soil. But it *is* water unavailable to
the next crop, which is precisely why the rotation must be simulated as a sequence
rather than scored crop by crop.

Two engineering notes:

- **Mass balance closure is computed, not assumed.** Every result carries a
  `closure_residual_mm`; it holds below 1e-6 mm across crops, soils and irrigation
  policies. A leaking water balance produces plausible-looking seasonal totals that
  are quietly wrong.
- **Stored soil water carries between seasons.** `run_water_balance` returns the
  closing depletion so the rotation simulator can chain seasons: a crop that leaves
  the profile dry is a cost to whatever follows it, and the model has to see that.

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

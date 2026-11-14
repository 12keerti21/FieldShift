# FieldShift

### Rethink What Grows Next.

A crop-rotation decision-support tool built on NASA Earth observations, for the
NASA Space Apps challenge on soil health and farm resilience.

---

**What it does differently.** Most rotation tools score crops against a location.
FieldShift simulates the *soil* forward - water, carbon, residue, pathogen history -
through four decades of weather that actually happened, for every candidate
sequence, and reports the bad years rather than the average one.

**Why that matters.** A rotation effect cannot be scored crop by crop. It only
exists across a sequence. So we tested against one that was independently observed:

> USDA NASS reports dryland wheat yield separately for crops following summer
> fallow (35.4 bu/ac) and continuous cropping (32.0 bu/ac) in our pilot county -
> an observed advantage of **+10.8%** over 25 years.
>
> FieldShift, with nothing fitted to that number, produces **+15.0%**.

The advantage is emergent. It comes from soil water banked through the fallow year
and carried into the next crop. A tool that scored crops independently would show
no difference between those two rotations at all.

And the whole trade-off emerged with it, which matters more than the single number:

| | wheat-fallow | continuous wheat |
|---|---|---|
| yield per crop | 2.72 t/ha | 2.36 t/ha |
| **crops in 38 years** | **19** | **39** |
| bare ground | 59.7% | 21.5% |
| soil carbon | +0.07 t C/ha/yr | **+0.28** |
| water lost to evaporation | 64% | 45% |

Fallow wins per crop and loses on everything else. None of that was asserted.

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
| Physical models | ET0, dual-Kc water balance, **RothC carbon** done; RUSLE pending | in progress |
| State simulator | couples the above; carries soil state across seasons | not started |
| Optimizer | enumeration / DP over multi-year crop sequences | not started |
| Replay | each rotation run through 20–40 observed years → outcome distribution | not started |
| Honesty layer | evidence cards, resolution-mismatch confidence downgrade, guardrails | not started |
| Delivery | map UI, bare-soil × erosive-rain calendar, printable field plan | not started |

Build order and the cut line (what to drop if behind) live in `docs/DECISIONS.md`.

## Parameter tables (built)

`src/fieldshift/data/crops.json` - 27 crops spanning cereals, grain legumes, oilseeds,
fibre, tuber, green manures, cover crops, perennial forage, plus an explicit bare
fallow slot so the cost of a bare window can be quantified rather than assumed.
Each crop carries FAO-56 Kc values and stage lengths, rooting depth and depletion
fraction, thermal thresholds and GDD, N fixation and uptake, residue mass and C:N,
a RothC DPM/RPM ratio, erosion-relevant canopy cover, and trait scores.

`src/fieldshift/data/pathogens.json` - 19 soilborne pathogens, nematodes, parasitic
weeds and one allelopathy entry, each with its survival structure, persistence and
the return interval it demands.

`src/fieldshift/data/soils.json` - the 12 USDA texture classes with field capacity,
wilting point, saturation, readily evaporable water, conductivity, bulk density and
NRCS hydrologic group, plus derived FAO-56 quantities (TAW, RAW, TEW). The USDA
texture triangle classifier in `soils.py` is the path gridded soil products take,
since they give sand/silt/clay fractions rather than class names - and measured
fractions are kept rather than replaced by the class centroid, because clay percent
feeds the RothC carbon partition and the RUSLE K factor directly.

Both tables are read through `fieldshift.data.load_registry()`, which validates them on
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

`src/fieldshift/agro/climate.py` is the seam between NASA data and the science. Models
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

## Validation status

All against independently observed data. The model never sees a yield.

### It works away from the field it was built on

Everything was developed and debugged at one point in Finney County, Kansas. The
same engine, with **no per-site adjustment of any kind**, run at four contrasting
sites against observed county yield:

| site | rainfall | soil available water | dryland skill vs detrended yield |
|---|---|---|---|
| Finney, KS | 541 mm | 212 mm/m silt loam | corn **+0.50**, sorghum **+0.42**, wheat **+0.61** |
| Sedgwick, KS | 841 mm | 200 mm/m silt loam | corn **+0.52**, sorghum **+0.70**, wheat +0.15 ns |
| Holt, NE | 651 mm | **84 mm/m loamy sand** | corn **+0.70**, wheat **+0.57**, sorghum +0.06 ns |
| Hale, TX | 503 mm | 178 mm/m clay loam | wheat **+0.51**, sorghum +0.18 ns |

**11 of 11 dryland combinations show positive skill; 8 of 11 reach p<0.05.** Mean
r = +0.447. The range matters more than the mean: it holds across a 2.5-fold
difference in soil water-holding capacity, including a Sandhills loamy sand that
holds 84 mm/m against the pilot field's 212.

Three combinations are not significant and are named rather than explained away:
sorghum at Holt and Hale, and wheat at Sedgwick. Irrigated combinations average
r = +0.007 - **as expected**, since irrigation exists to decouple yield from
weather.

Dryland corn yield penalty, absolute magnitude across three sites: mean absolute
error **6.0%** (observed 57-69%, predicted 45-67%).

### And it is robust to parameter uncertainty

There are roughly forty parameters per crop, many tagged medium or low confidence,
so the headline claim is worth only as much as its robustness. Each of the most
leveraged parameters was moved to the edge of its plausible range and the key
rotations re-simulated:

**The claim holds under 18 of 18 perturbations.** No single parameter can flip it.
The most leveraged are wheat Ky (moves the fallow advantage 5.5 points), attainable
yield (2.9), and depletion fraction (2.0) - which is the list an agronomist should
check first.

The sweep also produced a diagnostic. The modelled fallow advantage ranges
**+14.7% to +23.5%** across every perturbation and never reaches the observed
+10.8%. So the over-prediction is **structural, not parametric** - parameter error
is ruled out, which confirms the stated mechanism (a modelled fallow is perfectly
bare; a real one loses water to weeds) rather than leaving it a guess. The right
response is to name it, not to tune a weed parameter until the number matches.

```bash
PYTHONPATH=src ./.venv/bin/python scripts/sensitivity.py
```

| Our output | Independent reference | Result |
|---|---|---|
| FAO-56 Penman-Monteith ET0 | gridMET published 4 km grass ETo | **-3.2%** over 40 yr, daily r = 0.966, RMSE 0.70 mm/d |
| Stress index, **dryland corn** | NASS county yield, detrended | **r = +0.51, p = 0.026**, n = 19 |
| Stress index, **dryland wheat** | NASS county yield, detrended | **r = +0.50, p = 0.008**, n = 27 |
| Stress index, **dryland sorghum** | NASS county yield, detrended | **r = +0.41, p = 0.046**, n = 24 |
| Stress index, irrigated corn | NASS county yield, detrended | r = -0.13, ns - *as expected, see below* |
| **Dryland corn yield penalty** | NASS irrigated vs non-irrigated | observed 69.3%, **predicted 65.9%** (error -3.3 pp) |
| Dryland sorghum yield penalty | NASS irrigated vs non-irrigated | observed 45.8%, predicted 34.3% (error -11.5 pp) |
| Worst dryland maize seasons | documented US High Plains droughts | recovers 2012, 2011, 2002, 1988, 2022 unprompted |
| Soil texture class at pilot point | SSURGO (Richfield silt loam) | class correct; **our centroid AWC understated by 52%** |
| RothC carbon gain, irrigated no-till | published western Kansas range | 0.38 t C/ha/yr vs 0.2-0.5 reported |

The *absence* of skill on irrigated crops is a result, not a failure: irrigation
exists to break the link between weather and yield, so there is little variance
left for a water-stress index to explain. A strong correlation there would be
suspicious rather than reassuring - and it was predicted in the validation script
before being measured.

The yield-penalty rows are the harder test. Correlation only shows co-variation;
those compare **absolute magnitude** against 19-24 years of USDA observation, and
for corn the model lands within 3.3 percentage points.

**Open target.** NASS reports dryland wheat yield separately for crops following
summer fallow (35.4 bu/ac) and continuous cropping (32.0 bu/ac) - an *observed*
rotation effect of **+10.8%** over 25 years. Reproducing that requires simulating
rotations as sequences with soil water carried across the fallow year, which is the
next component to build. It is recorded as the number to hit.

Reproduce any of it:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/validate_et0.py     # vs gridMET
PYTHONPATH=src ./.venv/bin/python scripts/validate_yield.py   # vs NASS yields
PYTHONPATH=src ./.venv/bin/python scripts/validate_multisite.py  # 4 sites
PYTHONPATH=src ./.venv/bin/python scripts/show_carbon.py      # 41-yr RothC runs
```

**A process rule, learned the hard way:** a validation target must be a dataset we
have fetched or a citation we can point at. An earlier run attributed a residual to
a "+18% ET0 bias" on the strength of a *remembered* climatology figure; gridMET
showed the bias did not exist. See `docs/DECISIONS.md` for the retraction.

## Layout
```
src/fieldshift/        engine package  (agro/ models, data/ parameter tables, io/ dataset readers)
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

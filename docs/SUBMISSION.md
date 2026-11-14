# FieldShift — submission page

*Copy the sections below into the matching fields of the Space Apps submission
form. Every figure here is reproducible by a command listed at the bottom.*

---

## Project name

**FieldShift**

## Tagline

**Rethink What Grows Next.**

---

## High-level summary

Farmers choosing a crop rotation are making a decision that plays out over years,
but almost every tool scores crops against a location one at a time. A rotation
benefit does not exist crop by crop — it only exists across a sequence.

FieldShift simulates the **soil itself** forward through four decades of weather
that actually happened — water, nitrogen, carbon, residue and pathogen history all
carried from one crop to the next — for every candidate rotation, and reports the
bad years rather than the average one.

Then we tested whether that is real, against something independently observed. USDA
publishes dryland wheat yield separately for crops following summer fallow (35.4
bu/ac) and continuous cropping (32.0 bu/ac) in our pilot county: **an observed
rotation effect of +10.8% over 25 years.** FieldShift, with nothing fitted to that
number, produces **+18.0%** — imperfect, but in the right place, and a tool that
scored crops independently would show no difference between those two rotations at
all.

The same engine, with no per-site adjustment, then transferred to three further
counties across a 2.5-fold range in soil water-holding capacity and kept positive
skill against observed yields at **11 of 11** dryland crop–site combinations.

---

## Project details

### What it does

Pick a field on the map. FieldShift shows you what your soil has actually been
doing, searches the rotations that are physically possible there, simulates each
one through 41 years of real weather, and shows you the ones that are not beaten on
every count — with the trade-off between them made explicit.

At our pilot field in Finney County, Kansas, the result is concrete. Of 176 distinct
rotation cycles examined, 79 were physically feasible, and **the farmer's current
winter wheat–summer fallow rotation is beaten on every single objective by four of
them.** Against continuous winter wheat:

| | current | suggested |
|---|---|---|
| production | 1.03 t/ha/yr | **2.29** |
| soil carbon | **-0.050 t C/ha/yr (losing)** | **+0.270 (building)** |
| bare ground | 59.8% of the time | 21.5% |
| wind erosion exposure | 774 index | 391 |
| crops harvested per decade | 5.2 | 10.1 |

Nothing is given up. The trade-off panel prints **"WORSE: nothing."**

### How it works

Seven physical models, each published and auditable, chained so that state carries
between crops:

- **FAO-56 Penman-Monteith** reference evapotranspiration
- **FAO-56 dual crop coefficient** water balance — separates transpiration (water
  converted into growth) from soil evaporation (water simply lost), which is what
  makes the cost of bare ground a number instead of an assertion
- **SCS curve number** runoff, adjusted for how wet the profile already is
- **RothC-26.3** soil organic carbon, the model underpinning IPCC Tier 2 accounting
- **A nitrogen balance coupled to RothC's own carbon flows**, so straw
  immobilisation and legume credits emerge from the arithmetic
- **RUSLE** water erosion
- **A sequence simulator** that carries water, nitrogen, carbon, residue and crop
  history across the whole rotation

Mass balance closes to 1e-13 mm for water and 1e-9 t C/ha for carbon — computed and
reported on every result rather than assumed.

Candidate rotations are enumerated exhaustively over year patterns, reduced by
cyclic equivalence (a rotation has no inherent starting point, so [A,B,C] and
[B,C,A] are one option, not two), and filtered against pathogen return intervals and
calendar feasibility. **Every rejection is counted and explained.**

### Validation — the part we care most about

The model never sees a yield. It takes weather, soil and crop parameters, produces a
water-stress index, and FAO-33 maps that onto yield loss. Everything on the observed
side is USDA. That makes it an out-of-sample test rather than a fit.

| Claim | Independent reference | Result |
|---|---|---|
| Reference ET | gridMET published 4 km product | **−3.2%** over 40 yr, daily r = 0.966 |
| Stress index vs **detrended** county yield | USDA NASS, 4 counties, 3 states | **11/11 positive, 8/11 p<0.05**, mean r = +0.447 |
| Dryland corn yield penalty (absolute) | NASS irrigated vs non-irrigated | mean absolute error **6.0%** across 3 sites |
| **An observed rotation effect** | NASS wheat after fallow vs continuous | observed **+10.8%**, modelled **+18.0%** |
| Soil carbon gain, irrigated no-till | published western Kansas range | 0.38 t C/ha/yr vs 0.2–0.5 reported |
| Drought years | documented US High Plains droughts | recovers 2012, 2011, 2002, 1988, 2022 unprompted |

It also **transfers**. Same code, same crop table, no per-site tuning:

| site | rainfall | soil available water | dryland skill |
|---|---|---|---|
| Finney, KS | 541 mm | 212 mm/m silt loam | corn +0.50, sorghum +0.42, wheat +0.61 |
| Sedgwick, KS | 841 mm | 200 mm/m silt loam | corn +0.52, sorghum +0.70, wheat +0.15 ns |
| Holt, NE | 651 mm | **84 mm/m loamy sand** | corn +0.70, wheat +0.57, sorghum +0.06 ns |
| Hale, TX | 503 mm | 178 mm/m clay loam | wheat +0.51, sorghum +0.18 ns |

And it is **robust**: each of the most leveraged parameters was moved to the edge of
its plausible range and the headline conclusion held under **18 of 18**
perturbations. No single parameter can flip it.

### What validating it taught us

Checking against observation found **eight real errors in our own model**, each of
which would have produced a confident, plausible, wrong answer:

1. FAO-56 Table 12 *single* crop coefficients used where *basal* coefficients belong — double-counting soil evaporation
2. Irrigation scheduled on the instantaneous root zone, so the model irrigated a bare seedbed from day three in every one of 40 seasons
3. FAO-56 organic-mulch suppression missing, hiding a ~700 m³/ha/yr residue dividend
4. RothC ignoring irrigation — overstating carbon gain by about a third, **in the flattering direction**
5. gridMET packed integers read without their scale factor, giving 42 mm/day of evapotranspiration
6. **DEM slope read over 30 m on near-flat ground: 5.77% against a true 0.25%, which would have overstated erosion 17-fold**
7. Williams erodibility returned in US units and used as SI — a 7.6× error
8. Nitrogen uptake rationed monthly instead of seasonally, starving a winter crop

We also **retracted a published claim of our own**. Early on we reported a "+18%
bias" in our evapotranspiration, based on a figure one of us half-remembered. When
checked against gridMET there was no bias at all. The claim was withdrawn and a
standing rule adopted: *a validation target must be a dataset we fetched or a
citation we can point at — never a remembered number.*

### Honesty is a feature, not a disclaimer

Every recommendation carries an **evidence card**: the datasets behind it, the
equations, what validation showed, and what is not known. Two things on it are
computed rather than described:

- **Resolution mismatch.** Calling a dataset "coarse" conveys nothing. One NASA
  POWER cell covers **270,400 hectares**; a quarter-section field is 65 — a ratio of
  **4,160 : 1**, calculated from the actual areas. That is what tells a farmer how
  far to trust an absolute number.
- **Confidence is the weakest link, never an average**, because averaging would let
  strong inputs launder a weak one. The card also names *every* source at the
  confidence floor, since naming one would imply fixing it alone would help.

**Guardrails** refuse crops the optimiser would otherwise happily propose, using
frequencies from the record — "killing frost during grain fill in 7 of 41 years" —
because "frost risk: high" cannot be weighed against anything. Almost everything is
a warning rather than a refusal: a screen that refuses freely removes options a
farmer may have good local reasons to want.

We validated the guardrails against **what farmers actually grow**, which caught
three more errors, including refusing winter wheat in the county where it has been
the dominant crop for a century.

### What FieldShift cannot do, stated on the output itself

- **RUSLE models water erosion only.** At our pilot field the land is almost flat, so
  it reports a reassuring 3.6 t/ha/yr. This is the Dust Bowl region and the real
  mechanism is **wind**. Rather than print a comforting number, the tool computes a
  wind-exposure index from the observed record — which separates the same three
  rotations **sixfold** where RUSLE calls them all safe — and states plainly that
  quantifying wind properly needs field-roughness data no available dataset carries.
- **The rainfall erosivity factor is a regression, not measured 30-minute intensity.**
  Sub-hourly point data was not retrievable, so we compute it two ways and report the
  spread (1211 to 1525) as the uncertainty rather than quoting one figure.
- **We over-predict the fallow benefit by 4–7 points.** We could have closed that by
  adding a weed water-use term tuned until it matched. We refused, then tested
  whether parameter uncertainty could explain the gap — it cannot, so the cause is
  structural (our modelled fallow is perfectly bare; a real one loses water to
  weeds). There was nothing legitimate to fit.
- **No local prices.** Every marketed crop is flagged `price_local_required`. We will
  not invent market data, so the default economic objective is physical production.
- **Pathogen return intervals are literature mid-ranges** and need an agronomist.
  Patchily distributed organisms are reported but never block a rotation.

Three external services we planned to use were unavailable: **OpenET** returns a
server-side error on every request, the **USGS groundwater endpoint** has been
retired, and **CropScape** timed out. Each is documented with the exact failure.

### Architecture

Simulating one site takes about a minute. Re-ranking that scored set against a
farmer's priorities takes **10 ms**. So physics is precomputed and preference is
live — because the expensive half does not depend on anyone's preferences, and the
cheap half is entirely preference and has to move the instant a slider does.

Python engine (no heavy geospatial stack — NASADEM `.hgt` is read directly as raw
int16), FastAPI, Leaflet, no build step. **708 tests.**

### What a farmer takes away

A **printable one-page field plan**: a month-by-month calendar showing exactly when
the field is bare, the handful of numbers that change, the risks counted from 41
years of weather, and the questions only they or their agronomist can answer. Plain
HTML with print CSS and no JavaScript, so it prints identically anywhere — because a
dashboard needs a laptop and a signal, and a sheet of paper goes in the cab.

Every output ends the same way: **FieldShift supports a decision; it does not make
one.**

---

## NASA data used

| Dataset | Used for |
|---|---|
| **NASA POWER** | 41 years of daily temperature, humidity, wind, solar radiation and corrected precipitation at four sites — drives every water, carbon and nitrogen calculation. All six contract variables present, so evapotranspiration runs at full Penman-Monteith with no substitutions. |
| **NASADEM** (SRTM) | 1-arc-second elevation for slope and slope length in the erosion model, read directly from `.hgt`. |

Catalogued and assessed but not ingested, with reasons recorded: SMAP, GPM/IMERG,
ECOSTRESS, Landsat, MODIS/VIIRS, GEDI.

## Space agency partner and other data

- **gridMET** (University of Idaho) — published 4 km reference ET, used as an
  *independent check* on our own, not as an input
- **USDA SSURGO** — field-scale soil texture, available water capacity, organic
  matter, bulk density
- **USDA NASS QuickStats** — county yields 1984–, split by irrigation practice and,
  critically, by rotation (after fallow vs continuous), which is what made validating
  a *rotation* effect possible
- Challenge-listed and assessed: PRISM, CDL, NLCD, NAIP, OpenET, NWIS, AMS Market
  News, ERA5, GAEZ, CoCoRaHS

## Use of artificial intelligence

This project was built in collaboration with **Claude (Anthropic)**, which wrote the
large majority of the code, tests and documentation under human direction.

Deliberately, **no AI model makes or influences a recommendation at runtime.** There
is no LLM in the decision path. Every number a farmer sees comes from a published
equation applied to an observed dataset, and the evidence card names which. We chose
this because a language model cannot be audited by an agronomist, and an
unauditable agronomic recommendation is not decision support.

## Reproduce any claim on this page

```bash
./scripts/serve.sh                                  # the tool itself, on :8000
PYTHONPATH=src python scripts/validate_et0.py       # vs gridMET
PYTHONPATH=src python scripts/validate_yield.py     # vs NASS, pilot site
PYTHONPATH=src python scripts/validate_multisite.py # 4 sites
PYTHONPATH=src python scripts/sensitivity.py        # 18 perturbations
PYTHONPATH=src python scripts/search_rotations.py   # the rotation search
PYTHONPATH=src python scripts/show_evidence.py      # an evidence card
python -m pytest                                    # 708 tests
```

`docs/DECISIONS.md` is a dated log of every design decision, every alternative
rejected, and every error found — including the ones we made.

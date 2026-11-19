# FieldShift

### Rethink What Grows Next.

Crop-rotation decision support built on NASA Earth observations.
**NASA Space Apps Challenge 2026.**

---

A farmer decides what to plant next season. That one decision sets up the next ten
years — how much water the field needs, whether the soil gets richer or poorer,
whether the ground sits bare and blows away.

Farmers already know rotation matters. What nobody gives them is a way to compare
*their* options on *their* field, with numbers they can check.

FieldShift takes a field, pulls **41 years of NASA weather** for that exact point,
and simulates **every workable rotation day by day** across all of it — roughly a
million simulated days per field. Then it shows which options beat what the farmer
is doing now, and exactly how much to trust the answer.

```bash
./scripts/serve.sh      # then open http://127.0.0.1:8000
```

Six fields are precomputed and ship with the repository, so this works on a fresh
clone with no downloads. Any other point on Earth can be analysed on demand in
about forty seconds.

---

## What it found

**Finney County, Kansas** — 176 rotation cycles examined, 79 workable here, and
**two beat current practice on every single objective.** No trade-off.

| | wheat–fallow–sorghum now | suggested |
|---|---|---|
| production | 1.38 t DM/ha/yr | **2.02** |
| soil carbon | +0.101 t C/ha/yr | **+0.283** |
| bare ground | 61.6% of the time | **21.5%** |
| wind erosion exposure | 653 | **382** |

That baseline is not an assumption. USDA's Cropland Data Layer shows this field
in a clean wheat–fallow–sorghum cycle every year from 2008 to 2021. We used to
assume wheat–fallow, at a coordinate that was not this field.

**Sangrur District, Punjab** — rice–wheat over a falling water table. We measured
that fall ourselves: **20 CGWB observation wells, records up to 27 years, every
one showing the table dropping 1.167 m/year.**

Seven rotations beat it on every objective. The cheapest in water replaces one
rice year in three with a rainfed kharif legume: **1,961 → 1,489 mm/yr, a 24%
saving, while producing more.**

---

## How it works

Three parts, and the design decision behind them matters more than any of them.

**State simulator.** A field is not a blank slate each season. Soil water, carbon,
nitrogen and surface residue are carried forward from one season into the next, so
a legume shows up as a nitrogen credit to the following crop and a thirsty crop
shows up as a cost to whatever comes after it.

**Replay.** Each rotation is run through the weather that actually happened,
1984–2024. That is where the "bad year" number comes from — a distribution, not an
average, because nobody farms an average year.

**Ranking.** Rotations beaten on every objective are set aside; what remains are
genuine trade-offs, ordered by the farmer's own stated priorities. The interface
says when *the weighting* rather than the evidence is deciding the answer.

> The obvious way to build this is a scoring table — points per crop for water,
> nitrogen, erosion, summed. We deliberately did not. A scoring table cannot
> represent sequence, so it cannot see the thing rotation actually is, and it can
> never produce a downside. Scoring happens only at the last step, on simulated
> outputs.

**The architectural split.** Simulating one site takes about a minute; re-ranking
it against stated priorities takes **10 ms**. So physics is precomputed and
preference is live — the expensive half does not depend on anyone's preferences,
and the cheap half is entirely preference and has to move the instant a slider
does.

---

## NASA data

Not a layer on a map — the thing it runs on.

| Dataset | Role |
|---|---|
| **NASA POWER** | 41 years of daily temperature, rain, humidity, wind and solar radiation at every field. Drives the water balance, and through it the yield, carbon, nitrogen and erosion. **Every number in the tool traces back to it.** |
| **NASADEM** | 30 m elevation → slope → the RUSLE length-slope factor |
| **MODIS MOD13Q1 NDVI** (via NASA AppEEARS) | Independent check on modelled ground cover — used as evidence, not decoration |

Sangrur has no 41-year daily weather station we could access. POWER supplies 41
gap-free years at any coordinate on Earth, processed identically everywhere, which
is the only reason the same engine runs in Kansas and in Punjab and the comparison
between them means anything.

**Partner and other data:** USDA SSURGO (US soil), ISRIC SoilGrids (global soil),
USDA NASS QuickStats (county yields and irrigation census), gridMET (independent
reference ET), CGWB via India-WRIS (groundwater).

---

## Validation

This is where most of the work went. A crop model that has not been checked
against observation is a plausible-looking opinion.

**12 checks: 6 pass, 4 partial, 2 fail.** All twelve are displayed in the app with
the command that reproduces each one.

| Check | Result |
|---|---|
| Reference ET vs gridMET | **−3.3%** bias, daily r = 0.966 over 40 years |
| Modelled stress vs detrended NASS yield | **10/11 positive**, 8/11 significant, mean r = +0.430 |
| Dryland yield penalty, absolute magnitude | mean absolute error **3.8%** |
| Parameter sensitivity | **18/18** perturbations leave the recommendation standing |
| Water and carbon mass balance | closes to **1e-12 mm** |
| **Ground cover vs MODIS NDVI** | **4 of 6 pixels comparable, all four agree** — mean r = **+0.738** |
| Site locations vs USDA CDL | 4 of 5 US sites now sit on the land we model, after all five were moved |
| Bad-year recall vs harvest records | weak: 4 of 12 against 2.6 expected by chance |
| Rotation effect | ⚠️ observed **+10.8%**, modelled **+18.2%** — we over-predict |
| Punjab groundwater | ⚠️ direction confirmed by 20/20 wells, magnitude unreconciled 5–13× |
| **Paddy irrigation vs NASS** | ❌ **+53% over** observed 610–640 mm |
| Punjab yields | ❌ no district series obtainable — this is why Punjab is Tier 2 |

**23 errors found in our own model**, every one of which produced confident,
plausible, wrong output before it was caught. All are listed in the app.

### The satellite check, after the sites were corrected

MODIS NDVI now agrees with our modelled canopy at four of six fields: Holt
**+0.790**, Sangrur **+0.772**, Finney **+0.750**, Arkansas **+0.641**, mean
**+0.738**, with peak greenness landing within 5 and 9 days at the two closest.

On the original county reference points only 1 of 5 pixels was a valid
comparison, because four of the sites were not farmland. The two still excluded
are excluded by the data: Sedgwick never bares off, and Hale grows cotton in 8 of
14 years, which our crop table does not carry.

Sangrur matters most of the group. Punjab has no district yield series, so this
is the only independent check of the cropping there — the groundwater check only
ever tested the water.

### The two failures are the point

We could not check the flooded-rice model that the whole Punjab result rests on,
because India publishes no water record we could obtain. So we found somewhere
that floods rice the same way and does: **Arkansas**, where USDA reports 610–640
mm applied to gravity-irrigated rice.

**The model failed — 77% over.** Percolation could not rescue it; swept across its
whole plausible range the closest approach was still 50% over, so the error was
structural. We rebuilt the season model so one rice crop runs dry, then flooded,
then drained. That rebuild found three further bugs and the mass balance now closes
to 1e-12 across both transitions.

**It still fails, at +53%** — and the residual is now traced to FAO-56 Table 12's
paddy coefficients, which already include evaporation from standing water and so
charge open-water evaporation to a dry seedbed.

Nothing was tuned to close it. Percolation, efficiency and the coefficients would
each reproduce 610 mm with a plausible-looking number, and each would destroy the
meaning of the agreement.

### We checked our own ground, and failed — then fixed it

Every other check here tests the model. This one tests an assumption underneath
all of them — that the coordinate we picked for each site is a field growing the
rotation we modelled against it. Nothing had ever checked that.

USDA's Cropland Data Layer classifies every 30 m of the United States by crop,
annually. It is independent of MODIS NDVI, so it can judge our points without
making the NDVI check circular. CropScape is down and behind a sign-in wall, but
CDL is in the **Microsoft Planetary Computer** catalogue, which needs no account.

A MODIS pixel is 250 m and CDL is 30 m, so we asked for the composition of the
whole pixel rather than the class of the centre cell.

**0 of 5 US sites matched. Four were not farmland in any year of the record.**

| site | what was actually there |
|---|---|
| sedgwick_ks | **90% developed** — the point sat in built-up Wichita |
| holt_ne | 85% grass and pasture — the Sandhills |
| hale_tx | 70–96% pasture |
| arkansas_ar | 65% developed, the rest woody wetland |
| finney_ks | real cropland, but wheat–sorghum–fallow, not wheat–fallow |

A county reference point is a label on a map, not a field. We used one as if it
were the other, at every US site, for the whole project.

**So we moved all five** onto CDL-verified cropland inside the same county —
which matters, because the yield comparison is against county statistics, and two
candidate points had to be rejected for drifting into neighbouring counties. Holt
turned out to grow continuous irrigated corn and Sedgwick continuous wheat, so
their baselines changed too. **Four of five now match.** Hale is the honest
exception: verified cropland, but CDL shows cotton in 8 of 14 years and our crop
table carries no cotton.

Correcting it moved the POWER cell and the soil series at every site, and
therefore **every number this project reports**. Dryland skill went from 11 of 11
to 10 of 11, and the absolute yield-penalty error improved from 6.0% to **3.8%**.
A correction that makes one measure slightly worse and another clearly better is
what a real one looks like. The satellite check went from 1 of 5 comparable
pixels to **4 of 6, all agreeing**.

The correction exposed **four further bugs**, each of which made the fix appear to
work when it had not:

1. **The download cache was keyed by county, not coordinate.** The first rebuild
   silently reused the old field's weather and soil, and gave itself away because
   rainfall and soil series came back identical after a 25 km move.
2. **Seven scripts each hardcoded their own coordinates and cache paths**, so the
   validation scripts kept measuring the old points. The same duplication that put
   the sites in the wrong place kept them there.
3. **The baseline rotation was ordered by the pattern registry, not by the site.**
   Finney's real wheat–fallow–sorghum became wheat–sorghum–fallow; winter wheat
   holds the field from October into July, so sorghum the following June overlapped
   it by six weeks, the plan failed to simulate, and **the baseline silently
   vanished from the results.**
4. **The headline claimed nothing was given up** while the panel beside it showed
   irrigation rising by 456 mm — the sentence fired whenever *any* rotation beat
   current practice, not when the displayed one did.

**A process rule, learned the hard way:** a validation target must be a dataset we
fetched or a citation we can point at. An earlier claim of a "+18% ET0 bias" came
from a remembered figure; gridMET showed −3.3%. It was withdrawn. A later claim
that the model recovered drought years unprompted turned out never to have been
run at all — reproducing it properly downgraded the check from pass to partial.

```bash
PYTHONPATH=src python scripts/validate_et0.py        # vs gridMET
PYTHONPATH=src python scripts/validate_multisite.py  # 4 sites vs NASS
PYTHONPATH=src python scripts/validate_punjab.py     # vs CGWB groundwater
PYTHONPATH=src python scripts/validate_paddy.py      # vs NASS irrigation census
PYTHONPATH=src python scripts/validate_ndvi.py       # vs MODIS NDVI
PYTHONPATH=src python scripts/validate_drought.py    # bad-year recall
PYTHONPATH=src python scripts/validate_sites.py      # site locations vs USDA CDL
PYTHONPATH=src python scripts/sensitivity.py         # 18 perturbations
```

---

## Two tiers, enforced in code

| | |
|---|---|
| **Tier 1 — validated** | US High Plains (4 fields) and US Mid-South (Arkansas). Checked against 41 years of county yields |
| **Tier 2 — transferred** | Indian Punjab. Same engine, global-only inputs, no yield record to check against |

A tier-2 region **cannot claim validation** — it is refused in the constructor and
there is a test for it. An evidence card takes its confidence from its weakest
source, so a region declaring "no district yield series" can never read above LOW
however good the physics is. The downgrade is structural, not a disclaimer somebody
remembered to write.

---

## What it cannot do

Stated on the output itself, not in a footnote.

- **No farmer has used this.** Zero field trials. The largest limitation.
- **No prices, subsidies or markets.** The crop table refuses to invent them.
- **No methane** from flooded paddy — the dominant climate term for rice. Our
  carbon figure flatters rice.
- **Erosion is water only** in RUSLE terms; wind is reported as an exposure index
  because quantifying it properly needs field-roughness data no dataset carries.
- **One POWER cell is 52 km across** — 108,160 times the area of the 2.5 ha Punjab
  field. That ratio is printed on the evidence card rather than hidden.

---

## Stack

Python 3.11, FastAPI, NumPy. Frontend is a single HTML file with Leaflet and no
build step. NASADEM `.hgt` tiles are read directly as raw big-endian int16, so
there is no heavy geospatial stack to install.

**Seven published model families:** FAO-56 Penman-Monteith and the dual crop
coefficient, FAO-33 yield response, RothC-26.3 soil carbon, per-pool C:N nitrogen
under Liebig's minimum, RUSLE, SCS curve number runoff, and growing degree days.

**Parameter tables:** 33 crops, 19 soilborne pathogens, 12 USDA soil texture
classes — validated on load, so bad agronomy raises rather than passing silently.

**827 tests.**

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=src ./.venv/bin/python -m pytest
```

---

## Use of AI

An AI coding assistant wrote the large majority of the code, tests and
documentation, under human direction.

**No AI model makes or influences a recommendation at runtime.** There is no
language model in the decision path. Every number a farmer sees comes from a
published equation applied to an observed dataset, and the evidence card names
which. An agronomist cannot audit a sentence a language model generated, and an
unauditable agronomic recommendation is not decision support.

---

## Documents

- **`docs/SUBMISSION.md`** — the project page: every claim and how to reproduce it
- **`docs/EXPLAIN.md`** — the whole project in plain language, no jargon
- **`docs/DECISIONS.md`** — dated log of every design decision, every alternative
  rejected, and every error found in our own model

> **FieldShift supports a decision; it does not make one.** Nothing here replaces
> local agronomic judgement.

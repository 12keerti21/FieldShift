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

## 2026-11-12 - Weather contract and FAO-56 ET0
Built before any dataset work, against the internal contract rather than a product.
149 tests.

- **A narrow internal data contract** (`agro/climate.py`) is what makes building
  ahead of the resource list safe rather than a gamble. Models depend only on it;
  adapters become thin translation layers.
- **Penman-Monteith is used even when inputs must be estimated**, per FAO-56's own
  recommendation, rather than silently switching to a simpler equation.
  Hargreaves-Samani is kept as an *independent cross-check*, not a fallback - two
  methods disagreeing is information, a silent fallback is not.
- **Every substitution is recorded on the result** with the FAO-56 equation used,
  and confidence degrades accordingly. Radiation weighs most: it drives the energy
  term, so estimating it from diurnal temperature range costs more than assuming a
  wind speed does.
- **Rejected:** letting the models gap-fill. Filling belongs in the adapter where
  the method can be recorded; a model that quietly interpolates produces a number
  nobody can audit.

### Validation status
Verified against published reference values: saturation vapour pressure table
(0-30 degC), the psychrometric constant at sea level, the slope of the vapour
pressure curve, and extraterrestrial radiation against FAO-56 Annex 2 Table 2.6
(equator and 20N in mid-January). Polar day/night, hemispheric symmetry including
the orbital-eccentricity residual, and all sensitivity signs are covered.

**CLOSED 2026-11-14 by gridMET comparison.** Our ET0 agrees with gridMET's
independently computed 4 km published grass reference ET to **-3.2%** over 40
complete years at Finney County, with daily r = 0.966, RMSE 0.70 mm/day, and
May-Sep agreement of -0.6%. January differs by -17%, a low-magnitude winter month
(1.33 vs 1.59 mm/day) that barely affects a crop season. This is stronger evidence
than reproducing a worked example, because gridMET is computed from a different
forcing dataset and so is genuinely independent.

## 2026-11-12 - Water balance, runoff and a synthetic weather generator
268 tests.

- **Dual crop coefficient, not single Kc.** The whole value of this model for
  rotation design is separating transpiration from soil evaporation; a single Kc
  cannot express the cost of bare ground.
- **Mass balance closure is a computed output**, surfaced on every result. It
  closes to ~1e-13 mm in practice and is asserted below 1e-6 across crops, soils
  and irrigation policies.
- **Curve number is adjusted for antecedent moisture**, not fixed. A fixed CN makes
  the first storm after a dry spell generate the same runoff as one on an
  already-wet profile, which is backwards, and would understate the benefit of a
  crop that keeps the profile drawn down.
- **Irrigation is reported gross, at the field gate.** Net-to-root understates what
  a farmer actually has to find by the application efficiency.
- **Seasons are run one crop occupancy at a time**, returning closing depletion, so
  the rotation simulator chains them and stored water carries across the sequence.
- **`subsoil_depletion_frac` is a named parameter, not a buried constant.** How
  depleted newly-explored subsoil is assumed to be materially changes stress days
  (tested), so it is exposed and flagged as an early target for calibration against
  local soil moisture observations.
- **A seeded synthetic weather generator** (`agro/synthetic.py`) exists so models
  can be validated over multi-year runs now. Two couplings in it are deliberate:
  wet days have a compressed diurnal temperature range and higher humidity, because
  ET0 estimates radiation from diurnal range - a generator without that coupling
  would produce heavy rain under cloudless skies and would flatter the water
  balance. Archetype rainfall and annual ET0 were tuned to land in the ranges those
  climates actually report. Every series is tagged `synthetic` in its provenance.
- **Timing convention documented on the result:** coefficients are evaluated on
  start-of-day soil state, depletions are recorded end-of-day. A test asserting
  same-day consistency between the two failed for exactly this reason, which is why
  it is now written down where consumers will read it.

### Known simplifications, flagged for review
- Flooded rice needs a percolation term the standard FAO-56 root-zone balance does
  not provide; lowland rice results are not yet trustworthy.
- The surface-layer balance omits the FAO-56 `Tew` term (transpiration drawn from
  the surface layer).
- `Ky` (FAO-33 yield response factor) is not in the crop table, so the stress index
  is deliberately not converted into a yield number.
- Kc_max falls back to FAO-56 reference wind and humidity when those are missing,
  making peak soil evaporation approximate.

## 2026-11-14 - Resources received; direction locked

Resource list assessed in RESOURCES.md. The decisive finding: **13 of the 24
offered datasets are United States only**, which turns geography from a preference
into a data-availability consequence.

### Locked decisions

**1. Scope: US primary (Tier 1) + one Indian district (Tier 2).**
Southern High Plains / Ogallala as Tier 1, because it is the only place the
validation loop closes - CDL, SSURGO, OpenET, QuickStats, NWIS, PRISM, CoCoRaHS and
AMS Market News all available - *and* because the live question there (declining
aquifer vs cotton/sorghum/wheat/fallow rotations) is exactly what this tool answers.
Tier 2 runs the same engine on global-only inputs with the confidence downgrade
stated explicitly. Rejected "global/region-adaptive": it means validated nowhere.

**2. Ranking: simulate state forward and replay observed years.** MCDA is used only
to aggregate *simulated outputs* against farmer priorities at the final step, never
as the engine. A weighted sum over static crop attributes cannot represent sequence
effects (a legume's N credit to the next crop, a thirsty crop leaving the profile
dry, carbon accumulating over six years) and cannot produce a downside distribution.

**3. Cut line: protect validation and physics.** If behind, keep the simulator, the
20-year replay, the bare-window finding and three honest skill scores. Cut
within-field zoning, SMAP downscaling, voice, and the LLM narrator (static templates
instead).

### Revisions to the plan that the resource list forces
- **PRISM outranks POWER for Tier 1 forcing** - 4 km and topography-aware against
  POWER's ~0.5 degree (~55 km). A 13x linear resolution gain on the variable that
  drives everything downstream. POWER becomes the global/Tier 2 fallback.
- **CDL is reclassified from input layer to labelled validation set.** It is ground
  truth for the hardest claim in the architecture - rotation history inferred from
  NDVI phenology - which turns an unverifiable demo trick into a confusion matrix.
- **RUSLE R factor gets the proper EI30 path.** IMERG half-hourly supplies real
  rainfall intensity, so Modified Fournier Index is demoted from necessity to
  fallback.
- **NWIS becomes the primary groundwater-decline constraint**, GRACE the regional
  corroboration. GRACE is named in the challenge text but absent from the Resources
  tab; for a US field, in situ well records at the aquifer the farmer actually pumps
  from are the better instrument than ~300 km mascons.
- **Effort reallocated**: data integration 25 -> 30 percent (order latency is the
  real risk), validation 0 -> 15 percent, dashboard 15 -> 7, LLM 10 -> 3.
- **Cut from the stack**: Google Earth Engine (account approval is a dependency that
  can fail on the weekend, and direct adapters already work) and PostgreSQL/PostGIS
  (parquet is sufficient for one region; a database is hours of infrastructure for
  no demo value).

### Still missing, and all three are local-expertise items
Sowing windows / crop calendars; `Ky` FAO-33 yield response factors (check GAEZ);
local pathogen presence to escalate the `regional` constraints we report but do not
enforce.

## 2026-11-14 - RETRACTION: the "+18% ET0 bias" did not exist

**What I reported earlier was wrong.** After validating the water balance against
remembered Kansas extension figures, I attributed a residual overestimate to a
"+18% bias in POWER-driven ET0 against station climatology" and recorded it as a
forcing-resolution artefact of POWER's ~0.5 degree grid.

That conclusion rested on a remembered May-Sep climatology figure of ~825 mm. It
was not measured. gridMET - a published 4 km product computed from different
forcing - puts May-Sep reference ET at 975 mm against our 969 mm, a difference of
**-0.6%**. Annual agreement is -3.2%, daily r = 0.966.

**Our ET0 was never biased high.** The claim is withdrawn.

Two consequences that matter more than the retraction itself:

1. **The "Kansas reference ET 610-710 mm" figure I validated against was also
   recalled, not sourced.** Having been wrong once with the same method, it must
   carry the same suspicion. The remaining crop-ET discrepancy may be largely in
   that reference rather than in the model. It needs a cited source - OpenET, or a
   named KSU publication - before any further calibration is done against it.
2. **SSURGO contradicts our soil parameters in the direction that would explain
   much of the residual.** Measured AWC for the Richfield silt loam at this point
   is 212 mm/m depth-weighted to 1.5 m, against our texture-class centroid of
   140 mm/m - we were understating plant-available water by 52%, i.e. 318 mm of
   root-zone storage versus 210 mm. More storage means less modelled stress and
   less irrigation demand. The texture *class* was right; the centroid's water
   retention was not.

**Process change adopted:** no remembered figure is used as a validation reference
again. A validation target must be a dataset we have fetched or a citation we can
point at. `scripts/validate_et0.py` is the pattern - it is reproducible, it names
its source, and anyone can re-run it.

## 2026-11-14 - gridMET adapter and a one-sided plausibility guard

The THREDDS NCSS CSV export returns the variable's **packed integer values and
does not apply the `scale_factor`** recorded in the file metadata. For reference ET
that factor is 0.1, so an unguarded read yields 42 mm/day. That is physically
impossible, but it is large enough to look like a credible "the model is biased"
finding, and for one run it was reported as exactly that.

- `fieldshift/io/gridmet.py` reads `scale_factor` and `add_offset` **from the dataset
  metadata rather than hard-coding them**, so a repack upstream cannot silently
  rescale everything downstream.
- It then **refuses values outside a physical range** per variable. A magnitude
  guard on a well-understood quantity costs nothing and catches the whole class of
  silent unit errors.
- The guard is **one-sided by construction**: over-scaling leaves values
  implausibly small and still inside the window. A test documents this rather than
  letting anyone assume the guard is complete.
- **Chunking is required, not an optimisation.** A single 41-year NCSS request came
  back with 30 years and no error - silently truncated. The adapter chunks by
  decade and de-duplicates.

## 2026-11-14 - SSURGO wired in; measured soil overrides centroids

`fieldshift/io/ssurgo.py` fetches the dominant soil component at a point from USDA Soil
Data Access (SQL over REST, no credentials) and builds a `SoilTexture` backed by
measurements.

- **A measured AWC always beats one derived from texture.** `SoilTexture.taw_mm_per_m`
  returns `measured_awc_mm_per_m` when present. At the pilot point that is
  212 mm/m against the silt-loam centroid's 140 - the centroid understated
  root-zone storage by 52 percent (318 mm vs 210 mm over a 1.5 m maize root zone).
- **Two different depths, deliberately.** Available water is weighted over the root
  zone (150 cm); the texture CLASS comes from the FAO-56 surface evaporation layer
  (Ze = 15 cm), because that is the layer the class actually parameterises - TEW,
  REW and surface drying. The distinction is not cosmetic here: the top 16 cm is a
  silt loam, but anything weighted past ~20 cm becomes a silty clay loam because the
  next horizon reaches 39 percent clay. A test demonstrates the sensitivity.
- **SSURGO's hydrologic group is taken as authoritative** over the centroid's, since
  NRCS assigns it from the limiting layer and measured infiltration rather than from
  texture. Here SSURGO says group B where the profile-weighted centroid would have
  said D - a material difference in runoff.
- **Organic matter is converted to carbon explicitly** (/1.724, van Bemmelen), not
  folded in silently, because that factor is itself an approximation. This is what
  initialises the RothC pools instead of a guess.
- `taw_is_measured` is exposed so an evidence card can tell a farmer whether the
  soil behind an irrigation figure was measured on their field.

Measured effect at Finney County, maize over 40 seasons: dryland deficit 0.56 ->
0.50 and stress days 127 -> 121 (more real storage to draw on); irrigated gross
demand 693 -> 661 mm. The irrigated change is modest because seasonal ET is set by
atmospheric demand, not by storage - extra storage buys fewer, larger applications
rather than much less total water.

**Still outstanding:** the irrigated-corn reference figure (470-590 mm) is recalled,
not sourced, and must not be calibrated against further. We now hold a working
OpenET key, which gives field-scale actual ET for the western US - that is the
proper, citable reference and the next validation to run.

## 2026-11-14 - RothC soil carbon

`fieldshift/agro/rothc.py` implements RothC-26.3: five pools, monthly timestep, three
rate modifiers. This is the model that makes rotation comparison worth doing -
water stress and erosion are seasonal, but carbon is the state variable that
remembers decades of management and that a farmer cannot see changing year to year.

Validated against the model's published behaviour: the temperature modifier equals
1.0 at RothC's 9.25 degC reference, the CO2 partition falls from 0.830 at 5 percent
clay to 0.757 at 60 percent, maximum TSMD matches the published form, and carbon
balance closes to 1e-9 t C/ha.

- **The clay dependence is a result, not an assumption.** Of the carbon that
  decomposes, the share retained rather than respired rises with clay, so the same
  residue input builds more carbon on a heavy soil than a light one. A
  carbon-building rotation is therefore soil-specific, which is a genuine
  agronomic conclusion the model produces.
- **Irrigation was added to the moisture term**, which standard RothC omits because
  it assumes rainfed conditions. Leaving it out made an irrigated Ogallala field
  look as dry as a rainfed one, understating decomposition and overstating its
  carbon gain by roughly a third - an error in the flattering direction, which is
  the worst kind. With irrigation included, continuous irrigated maize with residue
  retained gains 0.38 t C/ha/yr, inside the 0.2-0.5 range reported for western
  Kansas no-till.
- **Inverse mode is used for initialisation**, not a guessed pool split. A measured
  SOC total says nothing about how carbon divides between a pool with a five-week
  turnover and one with fifty years, and that split controls how fast the soil can
  respond to management. The implied historic input is itself a check: at the pilot
  point it is 0.8 t C/ha/yr, which is what the previous rotation must have returned
  to hold SOC where SSURGO measured it.
- **Spin-up runs 400-2000 simulated years.** HUM has a ~50-year turnover, so a
  short spin-up leaves it still filling and the model then shows a spurious trend
  that looks like a management effect.
- **Concentration is converted to stock explicitly** via bulk density and depth.
  Treating a percentage as a stock is a factor-of-three error that leaves no trace.

Headline result on real data (41 years POWER weather, SSURGO soil, measured
starting stock 25.7 t C/ha): a 30.1 t C/ha spread between the best and worst
rotation. Baling maize stover reduces carbon gain to -0.02 t C/ha/yr - it stops
soil building outright.

## 2026-11-14 - Headline validation: the model reproduces observed yield

`fieldshift/io/quickstats.py` plus `scripts/validate_yield.py`. The model takes NASA
POWER weather, SSURGO soil and FAO-56 crop parameters, produces a relative
transpiration deficit, and FAO-33's Ky maps that onto yield loss. Everything on the
observed side comes from USDA NASS. **The model never sees a yield**, so this is an
out-of-sample test rather than a fit.

Year-to-year skill against DETRENDED county yield, Finney County:

| crop / regime | r | p | n |
|---|---|---|---|
| dryland corn | +0.508 | 0.026 | 19 |
| dryland wheat | +0.499 | 0.008 | 27 |
| dryland sorghum | +0.411 | 0.046 | 24 |
| irrigated corn | -0.134 | 0.54 | 23 |
| irrigated sorghum | +0.066 | 0.76 | 24 |

Level test on absolute magnitude, which is harder than correlation:

- dryland corn yield penalty: observed **69.3%**, predicted **65.9%** (-3.3 pp)
- dryland sorghum yield penalty: observed 45.8%, predicted 34.3% (-11.5 pp)

**The null results on irrigated crops are the expected pattern and were predicted
in the script before measurement.** Irrigation exists precisely to decouple yield
from weather, so a water-stress index should explain little of the remaining
variance. Had the irrigated correlations been strong, that would have indicated a
spurious common trend rather than physical skill.

Two methodological choices are load-bearing and are enforced in the adapter, not
left to the caller:

1. **Detrending is mandatory.** County yields carry a large technology trend
   (+2.42 bu/ac/yr for irrigated corn) with no weather content. Correlating against
   raw yield would largely measure the trend and would flatter any model.
2. **Units are never mixed.** A single QuickStats response for one crop carries
   BU / ACRE, BU / NET PLANTED ACRE and TONS / ACRE together.

`skill()` refuses to report a correlation on fewer than five paired years, and
always returns n and a p-value alongside r.

### The next target, already measured
NASS reports dryland wheat yield separately for crops following summer fallow
(35.4 bu/ac) versus continuous cropping (32.0 bu/ac) - an **observed rotation
effect of +10.8% over 25 years**. This is the ideal validation target for a
rotation tool, because it is a rotation effect rather than a weather effect. Our
model cannot yet reproduce it, since that requires simulating sequences with soil
water carried across the fallow year. Recorded as the number the sequence simulator
must hit.

### CDL deferred, with reason
CropScape's point service (`nassgeodata.gmu.edu`) timed out repeatedly over both
HTTP and HTTPS; the service appears down. Albers coordinate conversion is solved
(pyproj, EPSG:5070) so the blocker is purely availability. QuickStats turned out to
be the better validation channel anyway - it carries the irrigated/dryland and
fallow/continuous splits that CDL pixel labels do not.

## 2026-11-14 - Rotation simulator, validated against an observed rotation effect

`fieldshift/agro/rotation.py` chains seasons so four things carry between crops: soil
water, soil carbon (one continuous RothC run), surface residue, and crop history.
Without that carry-over a rotation can only be scored crop by crop, which is the
weighted-sum approach this project explicitly rejected.

**The validation result.** USDA NASS reports dryland wheat yield separately for
crops following summer fallow (35.4 bu/ac) and continuous cropping (32.0 bu/ac) in
Finney County - an observed rotation effect of **+10.8%** over 25 years. Nothing in
the model was fitted to it.

    modelled advantage to fallow:  +15.0%
    observed advantage (NASS):     +10.8%
    error:                         +4.2 percentage points

The advantage is emergent: it comes from soil water banked through the fallow year
and carried into the next wheat crop. A simulator that scored crops independently
would show no difference between these two rotations at all.

We **over-predict** the fallow benefit by about 4 points, and the likely reason is
honest and specific: a modelled fallow is perfectly bare, while a real summer
fallow loses water to weeds and imperfect control, so it banks less than the model
assumes. That is a named limitation, not a tuning parameter.

**The full trade-off emerged with it**, which is the more important outcome:

| | wheat-fallow | continuous wheat |
|---|---|---|
| yield per crop | 2.72 t/ha | 2.36 t/ha |
| crops in 38 years | 19 | 39 |
| bare fraction | 59.7% | 21.5% |
| carbon | +0.07 t C/ha/yr | +0.28 |
| evaporation fraction | 0.64 | 0.45 |

Fallow wins per crop and loses on everything else. No part of that was asserted.

Design decisions:

- **Gaps between crops are simulated, not skipped.** Every uncovered window gets an
  explicit bare-fallow water balance, so its evaporation and runoff cost is counted
  and the water a dry fallow genuinely conserves is credited.
- **Overlapping occupancies raise rather than silently double-cropping.** This guard
  immediately caught an agronomically impossible rotation in our own test suite:
  sorghum planted in June of the year winter wheat is harvested in July.
- **Cover-crop biomass is estimated from transpiration** via biomass water
  productivity, because a crop with no harvest has no yield to derive residue from.
  Flagged in the module as the weakest number in it.
- **Baling reduces carbon input but never to zero**, because roots are returned
  whatever happens to the straw.
- **Low-confidence Ky is surfaced in the result notes**, so a yield resting on an
  analogue crop says so.

### Crop table extended for Tier 1
The set was India-leaning. Added **winter wheat** (285 days, Oct-Jul, the dominant
High Plains crop and necessary for the validation above - the existing `wheat`
entry is SPRING wheat at 140 days and must not be used for an overwintering crop)
plus five US cover crops: hairy vetch, crimson clover, oats, field pea and forage
radish. 33 crops total.

FAO-33 **Ky** yield response factors added to every crop, with confidence tags -
`low` where no FAO-33 entry exists and an analogue was used. The validator now
enforces that a crop with a marketable yield must have a Ky and one without must
not.

### Groundwater deferred, with a specific reason
The USGS legacy `gwlevels` endpoint is retired (403 with and without an API key),
and the replacement OGC API serves no historical groundwater levels for this county
- those are discrete annual field measurements, not daily values, and that
collection is not yet exposed. The authoritative alternative is the Kansas
Geological Survey WIZARD database, outside the challenge resource list. GRACE
remains available via PO.DAAC once Earthdata credentials work.

## 2026-11-14 - Earthdata working; terrain, and a 17x error avoided

Earthdata downloads verified end to end: CMR search, the redirect chain to the
LP DAAC, and a 6.4 MB NASADEM tile retrieved through `~/.netrc`.

`fieldshift/io/terrain.py` reads NASADEM `.hgt` directly - raw big-endian int16,
3601x3601 per tile, row zero at the north edge - which removes rasterio from the
critical path entirely.

### The finding
A standard 3x3 Horn slope estimate at the pilot point returned **5.77%**. The real
gradient there is about **0.25%**. Garden City sits on near-flat High Plains
tableland, and two independent mechanisms corrupt a small-window estimate:

1. **Sensor noise.** NASADEM carries a few metres of vertical error. Over a 31 m
   cell that is several percent of apparent gradient on its own.
2. **Storage quantisation.** `.hgt` holds whole metres, so one 31 m cell pair
   resolves slope in steps of roughly 3 percent. A 1 percent true slope cannot be
   represented by a single cell pair at all - and this error exists even with a
   perfect sensor.

Because RUSLE's LS factor scales close to linearly with slope in this range, taking
the 3x3 figure would have **overstated soil loss by about 17 times** (LS 0.909
against 0.052).

### The fix, and the check that it is not just smoothing
Slope is now fitted as a least-squares plane over a ~300 m window
(`field_slope_percent`). `slope_stability_profile` shows the estimate converging:
4.91% at 62 m, 1.64% at 124 m, 0.24% at 186 m, then stable.

The obvious risk is that a wide fit simply flattens everything, which would be a
worse error. Checked against the steepest ~1 km cell in the same tile, a valley
wall at 38.705N: Horn gives 6.38% and the wide planar fit gives 5.51%, so real
gradient survives. On the flat site the same pair is 5.77% against 0.25%. The
method discriminates rather than smooths, and a synthetic-tile test pins both
behaviours independently of any download.

### Still deliberately explicit
`slope_length_m` remains a required argument with a documented default of 75 m. It
is the distance from where overland flow begins to where it deposits or reaches a
channel - not the field length, and not derivable from a DEM point. It is a prime
candidate for local correction and should never be silently defaulted in a result
shown to a farmer.

## 2026-11-14 - Nitrogen balance, coupled to carbon flows

`fieldshift/agro/nitrogen.py`. Nitrogen is computed from exactly the carbon flows
RothC produced - `rothc_step_detailed` now exposes them - rather than from a
parallel decomposition model, so the two budgets cannot drift apart.

Three canonical rotation behaviours emerge from the arithmetic rather than being
asserted. Measured over 12 months after a single 3 t C/ha residue addition,
relative to adding nothing:

| residue | C:N | net effect |
|---|---|---|
| wheat straw | 90 | **-11.7 kg N/ha** (locks up) |
| maize stover | 65 | -1.5 |
| legume residue | 25 | +56.9 |
| green manure | 18 | +93.8 |

And the timing is right: straw shows **-3.4 kg N/ha in month 2**, the month it
begins decomposing, then recovers. Month 1 shows nothing, correctly - the residue
has not decomposed yet. Magnitudes match reported ranges for straw immobilisation
(tens of kg N/ha) and legume cover credits (50-100).

### A real defect found and fixed
The first version took a single assumed residue C:N and applied it to every pool,
including carbon already in the soil. That meant changing the incoming residue also
changed the assumed composition of the existing humus, and it produced a baseline
gross mineralisation of **251 kg N/ha/month** - physically absurd, and large enough
to swamp the effect being measured.

Nitrogen is now held **per pool**, mirroring RothCPools. Each pool's C:N is a
consequence of its own history rather than a parameter anyone chooses. Baseline
mineralisation is now 36 kg N/ha/month on a 25 t C/ha soil, and whole-soil C:N
comes out at 12.4 - both in the right range for a cultivated topsoil.

### Other decisions
- **Immobilisation outcompetes the crop.** Microbes are in direct contact with the
  residue they decompose; the crop is not. So immobilisation is subtracted before
  uptake, and a nitrogen shortfall appears as unmet demand rather than a negative
  pool - which is what lets the simulator report that a rotation left its successor
  short.
- **Fixation is scaled by relative transpiration.** It is energy-expensive and
  collapses under water stress faster than growth does, so crediting the full table
  value would overstate the rotation benefit in exactly the dry years that matter.
- **Water and nitrogen stress combine by MINIMUM, not product** (Liebig). A crop
  capped by nitrogen is not additionally penalised for also lacking water; the
  binding constraint already determines what it can build. Multiplying would
  double-count the same lost growth.
- **Leaching uses a mixing model** - draining water carries the root zone's own
  nitrate concentration. Crude, but it captures the point that banking water under a
  fallow and banking nitrogen are not the same thing.
- **Nitrogen balance closure is asserted** over a 24-month run with inputs, uptake,
  leaching and denitrification all active.
- **Denitrification is deliberately crude** (a flat monthly fraction when wet),
  because its dominant control is anaerobic microsites a field-scale model cannot
  resolve. Flagged rather than dressed up.

## 2026-11-14 - RUSLE, and the mechanism it cannot see

`fieldshift/agro/rusle.py`. A = R K LS C P, with every factor and its provenance
carried on the result rather than collapsed into one number - they come from very
different places and carry very different confidence.

### A unit error worth 7.6x
The Williams/EPIC erodibility equation returns K in US customary units. RUSLE in SI
needs t ha h / (ha MJ mm), a factor of **0.1317** smaller. Without the conversion K
for our pilot soil came out at **0.3526** against a published silt-loam range of
0.03-0.045, and soil loss with it. That would have read as a serious erosion problem
on a field that has none. K is now converted and **range-checked on every call**,
with an error message naming a missing conversion as the likely cause.

After the fix every texture lands in its published range: sand 0.0123, silt loam
0.0464, silt 0.0599, clay 0.0300.

### The R factor is not EI30, and says so
Rainfall erosivity properly needs the kinetic energy of 30-minute intensity.
Retrieving sub-hourly precipitation as a point time series was not achievable: the
GES DISC Data Rods endpoint returns an empty shell page, and granule-by-granule
IMERG retrieval is tens of thousands of requests. R therefore uses the Renard &
Freimund (1994) regressions, which were fitted on US data and so suit the Tier 1
site. **Both available forms are computed and their spread is reported as the
uncertainty** - 1211 from annual rainfall against 1525 from the MFI - because a
single figure would imply a precision that is not there. MFI is preferred by
default, since two sites with equal annual totals are not equally erosive and that
distinction is exactly what a rotation tool needs.

### The finding that matters: RUSLE reports on the wrong mechanism here
The pilot field slopes **0.25 percent**, so LS is 0.060 and water erosion is
inherently negligible. Even a bare fallow reaches **3.59 t/ha/yr**, a third of
tolerable soil loss. RUSLE's verdict is that the field is fine.

It is not. This is the Dust Bowl region and the dominant process is wind, which
RUSLE does not model at all. Using the wind data already in the POWER record:

| rotation | wind index | bare days above 6 m/s |
|---|---|---|
| wheat-fallow | 1572 (2.50x) | 799 |
| continuous wheat | 628 (1.00x) | 182 |
| wheat + cover crop | 263 (0.42x) | 0 |

RUSLE ranks all three as safe; the dominant mechanism separates them sixfold. So
`wind_exposure_index` computes an explicit flag from observed wind and simulated
cover, and the result states plainly that RUSLE does not account for it and that
quantifying it needs the Wind Erosion Equation and field roughness data this model
does not have. A flat-terrain note also fires automatically, saying that a low soil
loss there reflects topography rather than management.

This completes the fallow trade-off. Summer fallow gives better individual crops
(+15 percent) but half as many of them, three times the bare ground, a quarter of
the carbon gain, more water lost to evaporation, and **2.5 times the wind erosion
exposure**. It loses on five of six dimensions.

### Organic carbon is a second erosion pathway
K falls as organic carbon rises - 0.0490 at 0.5 percent against 0.0374 at 2.5
percent, a 24 percent reduction. Because RothC moves that carbon over the
simulation, erodibility is not a static property of the field: a carbon-building
rotation reduces erosion through soil structure as well as through surface cover.

## 2026-11-14 - The decision layer: search, Pareto front, trade-offs

`fieldshift/decide/` - candidates, objectives, pareto, search. This is the step
that makes FieldShift a decision-support tool rather than a calculator: it proposes
rotations, discards the ones that cannot work, simulates the rest through 41 years of
observed weather, and returns the set where no option is beaten on every count.

### The headline result
On the pilot field, searching a 3-year cycle over 8 year patterns:

    176 distinct cycles examined -> 79 feasible -> 80 simulated -> 16 on the front
      rejected 80  impossible calendar
      rejected  7  too much fallow
      rejected  5  exceeds the irrigation limit
      rejected  4  breaks a pathogen return interval
      rejected  1  no marketable crop

**The farmer's existing rotation - winter wheat and summer fallow - is dominated on
every objective by four of the 79 alternatives.** Against continuous winter wheat:
production 1.05 -> 2.38 t/ha/yr, carbon **-0.021 -> +0.286** t C/ha/yr (it is
currently losing carbon), wind exposure 769 -> 381, bare ground 59.8% -> 21.5%.
Nothing is given up. The trade-off report prints "WORSE: nothing".

### Design decisions
- **Year patterns, not free crop slots.** A pattern is one year's cropping already
  known to fit the calendar. Searching over patterns means the generator cannot
  produce two crops occupying one field, which was the commonest way a hand-written
  rotation turned out impossible.
- **Cyclic equivalence is collapsed.** [A,B,C], [B,C,A] and [C,A,B] are one rotation
  started at different points. Deduplicating cut the space by more than two thirds
  and stops a farmer being shown duplicates dressed as alternatives.
- **Calendar feasibility is checked before simulation**, so an impossible sequence is
  a reported reason rather than an exception. It removed 80 of 176 candidates - and
  it **independently derives why western Kansas runs wheat-sorghum-FALLOW on three
  years rather than wheat-sorghum on two**: winter wheat harvested in July collides
  with a June sorghum sowing, so the fallow year is forced by the calendar, not
  chosen. That is a result, not a rule we encoded.
- **Rejections are counted and explained**, and an empty survivor set says so
  explicitly: a constraint that eliminates every option is a bug that looks like a
  result.
- **One shared starting soil for every candidate.** Derived per rotation, the carbon
  spin-up would hand each candidate a slightly different soil, since the pool split
  depends on the cover calendar - and the comparison would then partly measure that.
  Also hundreds of times faster.
- **Pareto front over weighted scoring.** A weighted sum collapses a real trade-off
  into one number and hides the exchange rate. Weighted ranking exists for when a
  choice must be made, but it operates on simulated outcomes rather than crop
  attributes, and `weight_sensitivity` reports whether the top choice survives a
  50 percent shift in each stated priority - because a recommendation that flips on
  one weight is an artefact of the weighting and the farmer should be told which
  priority is actually deciding.

### A real bug, and what it cost
`downside_production` was computed only over years that produced a crop. That handed
a fallow-heavy rotation the yield of its good years and none of its empty ones,
inflating a corn-corn-fallow rotation's "bad year" to **8.8 t/ha** and putting **13
extra rotations on the Pareto front** that did not belong there. Fixed to span every
year in the window, including the empty ones; the front fell from 29 to 16 and every
fallow-containing rotation left it.

### Field versus farm, stated rather than finessed
`downside` and `production` answer different questions and the difference is easy to
misread. This model simulates ONE FIELD through time, so a fallow year genuinely
produces nothing and the downside is zero. In practice wheat-fallow is run across
space as well - half the farm in wheat every year - so farm-level income is far
steadier. `production_t_ha_yr` is the closer analogue of that view. Presenting only
the field-level downside would unfairly condemn fallow rotations; presenting only the
cycle average would hide that this field carries a year of no income. Both are
reported, and the objective's own note explains why.

### Prices
`score_rotation` refuses to value some crops and not others: supplying prices for
only part of a rotation would penalise diversification for being diversified. Either
every price is given, or the economic objective stays physical production - which is
the default, since the crop table will not invent market data.

## 2026-11-14 - Multi-site validation: the model transfers

The largest weakness in the project was that everything had been developed,
debugged and validated at a single point. A model tuned at one location has no
demonstrated right to be used anywhere else, and a rotation tool that works on one
field is not a tool.

The same engine, **no per-site adjustment of any kind** - soil from SSURGO at each
point, weather from POWER, the same crop table - run at four contrasting sites:

| site | rainfall | available water | dryland skill |
|---|---|---|---|
| Finney, KS | 541 mm | 212 mm/m silt loam | corn +0.50, sorghum +0.42, wheat +0.61 |
| Sedgwick, KS | 841 mm | 200 mm/m silt loam | corn +0.52, sorghum +0.70, wheat +0.15 ns |
| Holt, NE | 651 mm | 84 mm/m loamy sand | corn +0.70, wheat +0.57, sorghum +0.06 ns |
| Hale, TX | 503 mm | 178 mm/m clay loam | wheat +0.51, sorghum +0.18 ns |

**11 of 11 dryland combinations positive, 8 of 11 significant at p<0.05, mean
r = +0.447.** The soil range matters more than the climate range: the Sandhills
site holds 84 mm/m against the pilot field's 212, so this is not a model that only
works on a silt loam.

Irrigated combinations average r = +0.007. That is the expected result and was
predicted before measuring: irrigation exists to decouple yield from weather.

Absolute magnitude also transfers - dryland corn yield penalty across three sites
has a mean absolute error of 6.0 percent (observed 57-69, predicted 45-67).

Three combinations are not significant: sorghum at Holt and at Hale, wheat at
Sedgwick. They are named rather than explained away. A tidy story was available
(skill should fall where water is not binding) but it does not survive the data -
mean skill by site is Finney 0.51, Sedgwick 0.46, Holt 0.44, Hale 0.34, and the
driest site has the lowest skill, which is the opposite of that hypothesis. So no
explanation is offered.

## 2026-11-14 - Sensitivity sweep, and OpenET unavailable

### The headline claim is robust
One-at-a-time sweep over the most leveraged parameters, each moved to the edge of
its plausible range, re-simulating the key rotations. **The claim holds under 18 of
18 perturbations** - continuous wheat beats wheat-fallow on production, carbon and
bare ground simultaneously in every case. No single parameter can flip it.

Most leveraged, by how far they move the fallow advantage: wheat Ky (5.5 points),
attainable yield (2.9), depletion fraction (2.0), straw C:N (1.4). That is the list
to put in front of an agronomist first.

Notably, soil available water at plus or minus 20 percent barely moves the answer
(+18.3 against +17.5 percent) even though the SSURGO correction changed absolute
values by half - because it changes both rotations in the same direction.

### A diagnostic that came out of it
The modelled fallow advantage ranges **+14.7 to +23.5 percent** across every
perturbation and never reaches the observed +10.8. The bias is one-directional and
parameter uncertainty cannot account for it, which **rules out parameter error** and
confirms the structural cause already suspected: a modelled summer fallow is
perfectly bare, while a real one loses water to weeds and imperfect control.

This is why the gap was never closed by tuning. Adding a weed water-use term with a
cover fraction chosen to make the number match would be fitting, and the sweep now
shows there is nothing to fit - the residual is structural.

Limitation stated: one-at-a-time cannot find interactions. What it gives is a
ranking of what the answer depends on.

### OpenET is unavailable
The intended fix for the open crop-ET validation gap. The account authenticates
(Tier 1, 100 requests/month, 0 used) but every timeseries request returns HTTP 408
with a server-side Google Earth Engine credential error: "The credentials do not
contain the necessary fields need to refresh the access token." Reproduced across
the Ensemble, SSEBop and eeMETRIC models and both the point and polygon endpoints.

This is an OpenET service fault, not an authentication problem here. The crop-ET
validation gap therefore stays open, and the recalled Kansas figure stays withdrawn
rather than being quietly reinstated. Retry if the service recovers.

## 2026-11-14 - Tier 2: guardrails and evidence cards

### Guardrails
`decide/guardrails.py`. The search ranks rotations on simulated outcomes and has no
idea whether a crop can survive at the site - it will propose a frost-sensitive crop
for a Nebraska winter if the water balance happens to look good.

- **Frequencies, not verdicts.** Every rule runs on the 41-year record and returns
  how often the hazard occurred. "Killing frost during grain fill in 7 of 41 years"
  can be weighed against something; "frost risk: high" cannot.
- **Two levels, and almost everything is a warning.** A screen that refuses freely
  removes options a farmer may have good local reasons to want, and we are not in a
  position to overrule them - only to say what the record shows. Across all three
  sites and nine crops, warnings outnumber refusals, and a test asserts that.
- **Heat is never a refusal.** `t_max_c` is where growth ceases, not where the crop
  dies; heat reduces yield rather than making a crop impossible, and part of that
  cost already flows through the water balance.
- **Frost and heat are judged on the reproductive window only.** Frost on dormant
  winter wheat is normal; the same frost at anthesis is fatal. Testing the whole
  season would be simultaneously too strict for an overwintering crop and too
  lenient for a spring one.

**Validated against what farmers actually grow**, which is the only real test, and
it caught three errors:

1. Winter wheat refused in Finney County on heat - where it has been the dominant
   crop for a century. Cause: treating `t_max` as disqualifying.
2. Winter wheat refused in Nebraska on degree days. Cause: a whole-season sum counts
   dormant months at zero, understating what an overwintering crop achieves. The
   refusal threshold moved from 60 to 25 percent of years and the verdict now
   carries a dormancy caveat.
3. Cereal rye cover crop refused for "not reaching maturity" in a 70-day autumn
   window - **a category error**. A crop with no harvest is terminated on purpose, so
   maturity is the wrong test entirely. Cover crops now report their growth window
   instead, and frost on them is reported as a possible intended termination rather
   than a risk, because oats sown as an autumn cover are grown to be winter-killed.

Screen now agrees with practice at 12 of 12 tested crop-site combinations, with one
deliberate exception left standing: grain sorghum refused at the cooler Nebraska
site although it is grown there. Our table holds one cultivar per crop at 1700
degree days; northern growers use short-season hybrids at 1300-1500. The refusal is
correct for the cultivar given, so every maturity refusal now says so rather than
the threshold being loosened until it agrees. Recorded as a known gap in crops.json.

**The water budget is not invented.** Irrigation can only be refused against a
supplied sustainable cap. The groundwater record that would set one automatically is
unavailable - the USGS endpoint carrying historical well levels has been retired -
and guessing a figure would silently decide which rotations are viable.

**Market pathway is never a refusal.** We cannot know local prices, buyers or seed
availability, and a tool that eliminated a crop for lacking price data would be
presenting its own ignorance as a recommendation.

### Evidence cards
`decide/evidence.py`. Every claim travels with its datasets, equations, validation
results and unknowns.

- **Resolution mismatch is computed.** One NASA POWER cell is 270,400 ha against a
  65 ha quarter-section field: a ratio of 4,160 to 1, calculated from actual areas.
  A test asserts the word "coarse" does not appear, because it conveys nothing.
- **Confidence is the weakest link, never an average.** Averaging would let strong
  inputs launder a weak one, which is the specific failure this layer prevents. A
  test confirms three high-confidence sources cannot lift a card carrying one
  low-confidence source.
- **All sources at the floor are named**, not just one - naming a single source
  would imply that fixing it alone would raise the card.
- **"Needs local review" is assembled, not written**, from guardrail flags and source
  caveats, so it cannot drift out of step with what it summarises.
- **Standing unknowns are always present**: wind erosion, market access, equipment
  and labour, whether the pathogens are actually in the field, sowing dates, and
  groundwater decline. The wind-erosion gap in particular appears on every card.
- Every card ends with the same line: FieldShift supports a decision, it does not
  make one.

## 2026-11-14 - Delivery: API, map interface, printable field plan

### The architectural decision that shaped everything else
Simulating one site - fetching 41 years of weather, reading the soil survey, running
about 80 candidate rotations through every year - takes roughly a minute.
Re-ranking that scored set against stated priorities takes **10 ms**, measured.

So the split is:

| | what | cost |
|---|---|---|
| precomputed | weather, soil, simulation, scoring, guardrail screen | ~1 min/site |
| live | ranking against priorities, Pareto front, weight sensitivity | 10 ms |

This is not only an optimisation. **The expensive half is physics and does not
depend on anyone's preferences; the cheap half is entirely preference and has to be
instant**, because a farmer exploring trade-offs needs the answer to move with the
slider. Precomputing the preferences and computing the physics live would have been
exactly the wrong way round - and it is the obvious way to build it if you treat the
whole pipeline as one request.

Four sites are precomputed: Finney KS, Sedgwick KS, Holt NE, Hale TX. A point
without a bundle gets an honest refusal from `/api/analyse` that states what a run
would cost and what data it would need, rather than blocking a request for a minute
and timing out somewhere unhelpful.

### A stale-index bug worth recording
`available_sites` originally read a written `index.json`. Building a subset of three
bundles rewrote that index and **silently dropped the pilot field from the API while
its bundle sat on disk**. The list is now derived by scanning the bundle directory,
so it cannot drift from what actually exists, and a test asserts the offered set
equals the files present.

### The rule applied across every endpoint
No endpoint returns a recommendation without the evidence behind it. Ranking carries
the weight-sensitivity report; rotation detail carries the full evidence card; the
crop screen carries the frequency behind every verdict. Tests enforce each of those
rather than trusting the convention.

### The printable field plan
`report/fieldplan.py`. The artefact that survives everything else: a dashboard needs
a laptop, a signal and someone to drive it, while a sheet of paper goes in the cab
and comes out at the co-op.

Plain HTML with print CSS and **no JavaScript**, so it prints identically from any
browser and survives being emailed, saved or photographed - a test asserts there is
no `<script>` in it. It carries a month-by-month rotation calendar showing exactly
when the field is bare, the handful of numbers that change against the current
rotation, risks counted from the record, the questions only a local person can
answer, and the resolution caveat.

One small fix worth noting: calendar cells originally truncated the crop name,
rendering winter wheat as "Winte", which is worse than useless on a sheet someone
reads in a tractor cab. Short labels are now explicit, and a test checks for that
exact string.

## Open decisions
- [x] Target region(s) — decided 2026-11-14: US (Ogallala) primary, one Indian district secondary
- [ ] Team composition / who owns which layer
- [ ] Product name (repo is `rotation-dss`, placeholder)
- [ ] Frontend stack (deferred until engine API shape is known)

---

## 2026-11-14 — Tier 2: the Punjab transfer, and what it forced

### 1. Region as an abstraction, not a config flag

`src/fieldshift/api/regions.py`. A `Region` supplies exactly three things: which
year patterns are agronomically real and on what dates, where the soil comes from,
and what independent data exists to check against. Everything below it — water
balance, RothC, RUSLE, the rotation search — is physics that does not know where it
is. `build_bundle` previously hardcoded `HIGH_PLAINS_PATTERNS` and SSURGO, so the
engine could not leave the United States.

The load-bearing invariant is that **a tier-2 region cannot claim yield
validation** — enforced in `Region.__post_init__` and tested. If someone later
obtains a district yield series and wires it in, construction fails until they also
promote the region to tier 1, which is the correct order of events: having the data
is what makes it tier 1.

The confidence downgrade is structural rather than written. The Punjab region
declares a low-confidence source literally named `no district yield series`, and
because `EvidenceCard.confidence` is the minimum over sources, no amount of good
physics can lift a Punjab card above LOW.

### 2. Sangrur, Punjab, because it is the same problem shape

Rice-wheat over a falling water table is the Ogallala story on the other side of the
world: a profitable monoculture mining a finite aquifer. That makes it the transfer
worth running rather than an arbitrary second site. 2.5 ha field, which is realistic
for the district and makes the POWER resolution mismatch honest — one 52 km cell is
about 108,000x the field.

### 3. Rice got its own water balance

`run_ponded_balance`. Under standing water the dual-Kc machinery describes nothing
real: Ke/Kr/TEW/REW parameterise a surface that wets and dries, Ks expresses
depletion a submerged field does not have, and percolation is a driven flux through
the plough pan rather than the overflow of a full profile. It uses FAO-56 **Table 12
single** coefficients, which is the opposite of the rest of the module and correct
here, because Table 12's paddy Kc already includes evaporation off the water
surface; using Kcb would silently drop open-water evaporation from a flooded field.

Dispatch lives inside `run_water_balance` on purpose. Every caller already goes
through it, and a caller that had to check the mode itself would eventually forget
and run a flooded field through a drying-soil balance.

Two bugs the tests caught:

- **Untracked store.** ET was debited in full when the pond could not supply it, so
  the balance was short by exactly that amount. ET now takes priority over
  percolation (a crop keeps transpiring from a saturated profile; percolation needs
  a head of water), then draws the profile store, and any remainder is counted as
  `soil_draw_total` and reported. An untracked store that always has water is how a
  model reports a drained paddy as unstressed.
- **Puddling spilled the bund.** 80 mm of a 180 mm establishment application became
  runoff on day one, because establishment water was routed straight into the pond.
  Puddling wets and saturates the profile before anything stands on the surface, so
  the profile is now filled first.

Stated limitation, prominently: **methane is not modelled**, and it is the dominant
climate term for paddy. The carbon figure for a rice rotation is incomplete in
rice's favour, and every ponded run says so in its notes.

### 4. Production is dry matter now

Found by the Punjab region, latent before it. Summing marketed tonnes across crops
holding different amounts of water is not a quantity. Potato at 25 t/ha of fresh
tubers against wheat at 4 t/ha of grain is six-fold in sale weight and about
1.5-fold in dry matter, and a maize-potato rotation was ranking 2.5x more productive
than rice-wheat purely through tuber water. `Economics.dry_matter_frac` is now
required for every marketed crop, with a validator refusing the 1.0 placeholder on
anything with a yield. On the US sites every crop is grain, so the error was
invisible there.

This moved the headline Finney figures from 1.03 -> 2.29 to **0.90 -> 1.99**; every
other objective is unchanged. `docs/SUBMISSION.md` and `docs/DEMO.md` were corrected.

### 5. SoilGrids, and treating throttling as throttling

`src/fieldshift/io/soilgrids.py`. ISRIC answers **HTTP 200 with every value null**
when rate-limiting, so "no data" and "slow down" are the same response on the wire.
Eight of ten test points returned nulls on a first pass and succeeded on retry. An
adapter that trusted the first 200 would report most of the world as having no soil,
so an all-null body is a fetch failure and never an absence of soil.

Three things are reported rather than fixed, because fixing them would mean
inventing data:

- Predicted topsoil organic carbon at Sangrur is **1.01%** (1.3% at 0-5 cm), roughly
  twice what the region's cropped alluvium is normally reported to hold. It
  initialises the RothC pools. We report what the global product says and name the
  discrepancy.
- SoilGrids has **no hydrologic group**, so runoff falls back to the texture class,
  which puts Sangrur in group D. That likely overstates monsoon runoff and penalises
  the rainfed options against the ponded ones. First item in `review_first`.
- Sowing dates and the 4 mm/day puddled percolation are district practice in round
  numbers, not sourced values, and they are listed for review rather than presented
  as citations.

**`awc_provenance` replaced a measured/estimated boolean.** SoilGrids does populate
`measured_awc_mm_per_m` — it is the best number available and should override the
texture centroid — but `taw_is_measured` would then have reported a 250 m
machine-learning output as a measurement to exactly the person least able to check
it. Provenance is now `survey` / `prediction` / `centroid`, and the farmer-facing
flag reads provenance.

### 6. ET0 in India: what we can and cannot say

Sangrur Penman-Monteith ET0 is 1,909 mm/yr with a monthly shape that peaks at
8.8 mm/day in May and dips through the monsoon — the expected shape for north-west
India. There is no independent gridded ET product for India in our hands, so this
cannot be validated the way gridMET validated the US.

The one usable check: PM sits **+6.4% above Hargreaves-Samani** at Sangrur and
**+11.0%** at Anantapur, against **+16.9%** at Finney County, where PM is
independently confirmed good to −3.2% against gridMET. So the Indian PM values are
not anomalously high relative to the one site we can actually check. That argument
uses only data we fetched plus one validated reference point, which is the standing
rule. It is weak evidence and is offered as weak evidence.

Explicitly **not** done: comparing against a remembered figure for Punjab ET0 or
rice water use. Those numbers exist in my training data and are not admissible
here — this is the exact failure that produced the retracted +18% claim.

### 7. Interface rebuilt around five screens

The user judged the single-panel interface too plain, and reviewed an LLM-generated
plan proposing seven screens including GPM rainfall, SMAP soil moisture, NDVI and
land-surface-temperature layers. Four of those seven would have been facades: we
have none of those datasets, OpenET returns HTTP 408, and our nitrogen is modelled
rather than observed. Building them would have meant drawing charts with nothing
behind them.

Taken from the plan: the sidebar shell, the three-rotation side-by-side compare, the
flip-card treatment for evidence, the forest/leaf/sage palette, and its own best
line — "clear distinction between measured data, derived indicators and hypothetical
projections".

Rejected and replaced: the fake satellite-layer screens, in favour of a
**Validation** screen served from `data/validation.json` via `/api/validation`, so
the page and the record cannot drift. It shows all eight checks including the one we
fail, the ten errors validation found in our own model, and the withdrawn ET0 claim
with the rule adopted afterwards. Every check carries the command that reproduces
it.

Also added: goal presets (Balanced / Save water / Most output / Build soil / Lowest
risk) in front of the six raw weight sliders, because six abstract weights are how
an engineer thinks about this and not how a farmer does; a plain-language headline
answer; and a **calendar strip** per rotation drawn from a new `schedule` block in
the bundle, since a list of crop names hides the thing that matters most — where the
bare gaps are, which crop overwinters, and how tight the turnarounds get.

### 8. The evidence endpoint was claiming sources it had not used

`/api/site/{id}/rotation/{i}/evidence` hardcoded `[POWER, SSURGO, CROP_TABLE,
QUICKSTATS, GRIDMET]`. On a Punjab site that is a false citation on the one artefact
whose entire job is to be checkable. Sources now come from the region.

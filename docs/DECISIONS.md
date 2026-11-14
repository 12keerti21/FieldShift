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

## Open decisions
- [x] Target region(s) — decided 2026-11-14: US (Ogallala) primary, one Indian district secondary
- [ ] Team composition / who owns which layer
- [ ] Product name (repo is `rotation-dss`, placeholder)
- [ ] Frontend stack (deferred until engine API shape is known)

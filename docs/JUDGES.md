# Judging brief — FieldShift

Written for: the team, to be read before judging. Every number here was verified
against `data/bundles/*.json` and `data/validation.json` on the day of writing.

---

## ⚠️ FIRST: four questions that are traps

These assume capabilities we **do not have**. Claiming them is fatal if a judge
checks, and judges do check. Each has a strong honest answer.

| The question | The honest answer |
|---|---|
| *"Which NASA products — SMAP, ECOSTRESS, GPM, Landsat?"* | **We use NASA POWER, NASADEM, and MODIS NDVI via AppEEARS. Not SMAP, not ECOSTRESS, not GPM, not Landsat.** Say so, then pivot to depth and validation (below). |
| *"How does your AI translate anomalies into advice?"* | **It doesn't, deliberately.** No language model touches a recommendation at runtime. See Creativity Q2. |
| *"What makes your What-If climate simulator interactive?"* | ✅ **We have one now.** Six named stress tests, ~26 s each. But be precise: it is a **stress test, not a projection** — we replay the observed record with an offset. Say that before they ask. |
| *"How do you handle cloud cover gaps?"* | **Two different answers, and both are good.** Our *driving* data is reanalysis, so no cloud gaps at all. Our *validation* data (MODIS NDVI) is optical, so we screen on the product's pixel reliability band — and a test proves ignoring it would report a never-bare field as 40% bare. |

**Rule for the day: never name a dataset we do not load.** Our whole pitch is
auditability. One invented citation destroys it.

---

## 1. IMPACT

### Opening line
> "At our pilot field, four of seventy-nine workable rotations beat current
> practice on *every single measure we track*. Not a trade-off — nothing given up."

### Quantifiable gain — Finney County, Kansas (65 ha)

| | now | suggested | change |
|---|---|---|---|
| production | 0.90 t DM/ha/yr | **1.99** | **+121%** |
| soil carbon | −0.050 t C/ha/yr (losing) | **+0.270 (building)** | sign flip |
| bare ground | 59.8% of the time | **21.5%** | **−64%** |
| wind erosion exposure | 774 index | **391** | **−49%** |
| harvests per decade | 5.2 | **10.1** | +94% |

### Quantifiable gain — Sangrur, Punjab (2.5 ha)

| | rice–wheat now | best alternative |
|---|---|---|
| irrigation | 1,996 mm/yr | **1,511 mm/yr (−485, −24%)** |
| production | 5.72 t DM/ha/yr | **5.92 (+3%)** |
| soil carbon | −0.002 t C/ha/yr | **+0.107** |

**Less water AND more output.** In a district whose water table we measured
falling **1.167 m/yr**, that is the actual policy question.

### Accessibility for smallholders
- The Punjab design case is a **2.5 ha** field, not a 500 ha operation.
- The deliverable is a **printable one-page field plan** — plain HTML, print CSS,
  **no JavaScript**. It prints identically anywhere and goes in the tractor cab.
  A dashboard needs a laptop and a signal; paper does not.
- Interface works at **400 px** phone width. No login, no app install.
- **Honest limit:** reaching the server needs connectivity once. The paper
  artefact is what survives after that.

### Synthetic inputs
In Punjab, swapping one rice year for green manure + groundnut:
- fixes **1,214 kg N/ha** biologically across the simulated record (~31 kg N/ha/yr)
- cuts modelled nitrogen leaching by **41%**

⚠️ **Caveat to volunteer:** our *absolute* N-leaching figure is unvalidated and
looks high. Quote the **relative** reduction, and say the absolute needs local
soil-N measurement. Do not let a judge catch this before you say it.

### The weakness — say it first
> "No farmer has used this yet. Zero field trials. What we have is a model
> validated against forty-one years of observed yields, not a deployment."

---

## 2. CREATIVITY

### Opening line
> "Most teams will show you a dashboard. We'll show you the fourteen bugs we found
> in our own model — and the one validation check we still fail."

### Why hasn't this been built?
Rotation models exist in research. What doesn't exist is one that **refuses to be
trusted blindly**:
- Every recommendation ships an **evidence card** listing each dataset, its
  resolution mismatch, what the model cannot see, and who must check it.
- Confidence is the **minimum** across inputs, never an average — so one good
  dataset can't launder a bad one.
- A **tier system** makes it structurally impossible for the unvalidated region to
  claim validation. It's enforced in a constructor and tested.

### Bridging coarse data to a small plot — our real answer
We don't use SMAP, but we have the identical problem and we **print it on the card
instead of hiding it**:

> One NASA POWER cell is 52 km across — **108,160 times** the area of the 2.5 ha
> Punjab field.

We bridge it the only honest way: coarse data for **weather** (which really is
regionally uniform) and field-scale **soil** (SSURGO survey in the US, SoilGrids
250 m prediction globally) for the thing that actually varies field to field.
Then we state the mismatch rather than implying 52 km data is field-scale.

### The AI question — a principled answer, not a dodge
> "Claude wrote most of the code. **No language model touches a recommendation at
> runtime** — and that's deliberate. An agronomist can't audit a sentence a model
> generated. Every plain-language line in our interface is produced
> deterministically from the model's own numbers, so you can trace any sentence
> back to the physics that made it."

### Climate stress testing — what to say

> "We replay the same forty-one years with every day two degrees hotter and ten
> percent drier, and re-simulate everything. At our Kansas field that drops
> current practice by 28% and degrades the soil twice as fast — but the number of
> rotations that beat it goes **up**, from four to five. The case for changing
> gets stronger under stress, not weaker."

**Say the limit in the same breath:** *"It's a stress test, not a climate
projection. We're replaying real weather with an offset — we don't have a climate
model, and we don't pretend to."*

### Also
- **Live re-ranking:** physics precomputed (1 min), preferences applied in **10 ms**.
  Move a slider, the answer moves instantly.
- **Sensitivity sweep:** 18 systematic perturbations; **18/18** leave the leading
  recommendation standing.
- **Live sensitivity readout:** the UI tells you when *your own weighting*, not the
  evidence, is deciding the answer.

---

## 3. VALIDATION — our strongest axis

### Opening line
> "A crop model that hasn't been checked against observation is a
> plausible-looking opinion. Here's our whole record, including what we fail."

### What we actually pull from NASA

**NASA POWER** — daily, 1984–2024, 41 complete years, every field:
`T2M_MAX`, `T2M_MIN`, `PRECTOTCORR`, `RH2M`, `WS2M`, `ALLSKY_SFC_SW_DWN`

→ FAO-56 Penman-Monteith ET₀ → dual-Kc water balance → yield → carbon → erosion.
**Every number in the tool traces back to POWER.**

**NASADEM** — 30 m elevation → slope → RUSLE LS factor.

**MODIS MOD13Q1 NDVI**, via NASA AppEEARS — 16-day, 250 m, 2003–2024. Used as
**evidence, not decoration**: it validates our bare-ground measure, which was the
only one of our six that nothing else could check.

### Data gaps — answer it in two halves

> "Our *driving* data has no cloud gaps at all, because POWER is reanalysis, not
> imagery. What we enforce there is a **gap-free daily record** — sentinels are
> rejected and the model **refuses to gap-fill**, because that belongs in an
> adapter where it can be declared, not buried in physics.
>
> Our *validation* data does face cloud, because MODIS is optical. We screen on
> the product's own pixel reliability band and keep good and marginal pixels
> only. That matters seasonally: in a monsoon climate the cloud arrives exactly
> when the crop is densest, so keeping cloudy pixels would make the greenest
> season read as bare ground. We have a test showing that ignoring it would
> report a field that is **never** bare as **40% bare**."

### Established agronomic science — name them
| Model | Published basis |
|---|---|
| Reference ET | **FAO-56** Penman-Monteith, dual crop coefficient (Ch. 7–8) |
| Yield response to water | **FAO-33** Ky factors |
| Soil carbon | **RothC-26.3**, 5 pools, monthly |
| Nitrogen | per-pool C:N coupled to RothC flows, **Liebig minimum** |
| Erosion | **RUSLE** (Williams/EPIC K, McCool LS) |
| Runoff | **SCS curve number**, antecedent-moisture adjusted |
| Crop development | **growing degree days**, FAO-56 stage lengths |
| Legumes | biological N fixation per crop |

### The validation results
| Check | Result |
|---|---|
| ET₀ vs gridMET | **−3.2% bias, r = 0.966** over 40 years |
| Stress vs detrended NASS yield | **11/11 positive, 8/11 significant**, mean r = **+0.447** |
| Dryland vs irrigated corn penalty | MAE **6.0%** |
| Sensitivity | **18/18** perturbations hold |
| Drought years | 2012, 2011, 2002, 1988, 2022 recovered **unprompted** |
| Mass balance | closes to **1e-13 mm** |
| **Rotation effect** | observed +10.8% vs modelled +18.0% — **we fail this** |
| **Punjab groundwater** | direction **20/20 wells**, magnitude off **5–13×** |

**11 checks: 6 pass, 3 partial, 2 fail. 819 tests. 14 bugs validation found in us.**

### The two failures — lead with these, don't bury them

> "We built a flooded-rice water balance for Punjab and had no way to check it,
> because India gave us no water record we could get. So we went and found a
> place that floods rice exactly the same way and *does* publish the water
> applied — Arkansas.
>
> **Our model failed. It asked for 1,135 millimetres against an observed 610 to
> 640.** Seventy-seven percent over.
>
> Then we found out why. We flood for the whole 140-day season; Delta rice is
> drilled dry and flooded only at four-leaf, about 95 days. Fixing just that
> takes it to within eight percent.
>
> We did **not** apply the fix and call it validated — doing it properly means
> splitting one rice crop into a dry phase and a ponded phase, and changing a
> model against a number you already know is fitting, not modelling."

**Why this wins rather than loses:** we are the team that went looking for a way
to break our own flagship result, found one, and can tell you exactly what it
costs. Nobody else will do that.

### The line that wins this criterion
> "Our sensitivity sweep showed the fallow advantage ranges +14.7% to +23.5% and
> **never reaches the observed +10.8%**. So the over-prediction is structural, not
> a mis-set parameter. We could have added a weed parameter and tuned until it
> matched — we didn't, because tuning an unvalidated parameter until the answer
> agrees destroys the meaning of the agreement."

---

## 4. RELEVANCE

### Climate threat, right now
- **Ogallala:** the aquifer under our pilot field is finite and declining. Irrigated
  corn is the thirstiest option and the most profitable — that's the trap.
- **Punjab:** we didn't read this in a paper, we **measured it**. 20 CGWB
  observation wells, records to 27 years: water table falling **1.167 m/yr**,
  every single well agreeing on the direction.

### Why NASA data is indispensable
> "Sangrur has no 41-year daily weather station we could access. POWER gave us
> **41 gap-free years at any coordinate on Earth, processed identically
> everywhere** — which is the only reason the same engine runs in Kansas and
> Punjab and the comparison between them means anything. A ground station gives
> you one point, if it exists, if it's maintained, and if you can get the data."

### End user
Primary: the **farmer or extension officer**. The workflow matches their reality
because the output is a sheet of paper with a month-by-month calendar, not a
login. Secondary: an **agronomist auditing the recommendation** — the evidence
card exists for them.

⚠️ **Honest:** we have not tested with farmers. Say it before you're asked.

---

## 5. PRESENTATION

### The 30-second pitch — learn this
> "Farmers know rotation matters. What they don't have is a way to compare their
> options on their own field with numbers they can check.
>
> FieldShift simulates every workable rotation through forty-one years of NASA
> weather at your exact field — the water, the carbon, the nitrogen, the erosion.
>
> At our pilot field in Kansas, four rotations beat current practice on *every*
> measure. Nothing given up.
>
> And every recommendation comes with an evidence card showing what's behind it,
> how coarse the data is, and what we *don't* know. Because a recommendation a
> farmer can't interrogate isn't decision support — it's an instruction."

### The demo — run it in this order
1. **Field** — the headline answer in plain language, the map, the two tiers
2. **Rotations** — click a goal preset ("Save water"), *the ranking moves live*
3. **Compare** — current vs recommended, side by side, calendar strips
4. **Evidence** — **flip a card** (this lands well)
5. **Validation** — "here's the check we fail"

### The killer demo — if asked "does it work right now?"
**Analyse a field the judge picks, live.** Thomas County, Kansas completed in
**31.6 seconds** end to end — POWER fetch, SSURGO lookup, 176 rotations simulated
through 39 years, ranked, with a printable plan at the end.

Let a judge click the map. That's the moment.

⚠️ **Risk:** ISRIC SoilGrids rate-limits and was in cooldown during testing. **Pick a
US point for the live demo** — SSURGO is reliable and the fallback is automatic.
If a judge insists on a non-US point and it fails, the failure message explains
the throttling honestly, which is itself on-brand — but don't choose that.

### Fallback if the demo breaks
Open the **printed field plan**. It's paper. It cannot fail.

### UI
Five screens, sidebar navigation, dark/light theme, flip cards, live Pareto
scatter, rotation calendar strips. Responsive to 400 px.

---

## The three sentences that matter most

1. **"Four rotations beat current practice on every measure — nothing given up."**
2. **"Here are the fourteen bugs validation found in our own model."**
3. **"Here's the one check we still fail, and why we refused to tune it away."**

Most teams will claim everything works. Being the team that says what doesn't is
what makes the rest believable.

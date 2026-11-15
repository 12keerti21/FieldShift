# Project status — what is done, what is not

Verified against the repository, not written from memory.
**39 commits · 819 tests passing · 44 source modules · 35 test files · 21 API routes**

---

## ✅ DONE

### The engine (the physics)
| Piece | What it does | State |
|---|---|---|
| Weather contract | Gap-free daily records; rejects bad values; **refuses** to invent missing data | done |
| Reference ET | FAO-56 Penman-Monteith, with a second method as cross-check | done, validated |
| Water balance | Day-by-day soil water, splits what the crop drinks from what evaporates off bare soil | done, validated |
| **Paddy water balance** | Separate model for flooded rice — the normal one is meaningless under standing water | done |
| Soil carbon | RothC, 5 pools, monthly | done |
| Nitrogen | Tracked per carbon pool, limited by whichever runs out first | done |
| Erosion | RUSLE water erosion + a wind exposure index | done |
| Runoff | Curve number, adjusted for how wet the soil already is | done |
| Rotation simulator | Chains crops so one season's leftover water and residue carry into the next | done |
| **Climate stress test** | Replays the 41 years hotter and/or drier and re-simulates everything | done |

### The decision layer
- Enumerates every rotation, removes duplicates, **explains every rejection**
- Ranks on 6 measures; finds the options nothing beats outright
- Tells you when **your own weighting**, not the evidence, is deciding the answer
- Evidence card on every recommendation: sources, resolution mismatch, unknowns, who must check it
- Guardrails screen each crop against the local record

### Data adapters (all working)
NASA POWER · NASADEM · **NASA AppEEARS (MODIS NDVI)** · USDA SSURGO · ISRIC SoilGrids · USDA NASS · gridMET · India-WRIS groundwater

### Regions — 3 regions, 6 fields
- **Tier 1 — US High Plains:** 4 fields (KS ×2, NE, TX), 8 patterns. Semi-arid, aridity 1.6–3.6
- **Tier 1 — US Mid-South:** Arkansas County, 7 patterns. **Humid (aridity 0.92)** and floods
  its rice the way Punjab does — which is what made the paddy model testable
- **Tier 2 — Indian Punjab:** Sangrur, 10 patterns, transferred and marked as such
- A tier-2 region **cannot claim validation** — enforced in code and tested

### Validation — 11 checks: 6 pass, 3 partial, 2 fail
| Check | Result |
|---|---|
| Weather maths vs gridMET | ✅ **−3.2%** |
| Stress vs real harvests | ✅ **11/11 right direction**, 8/11 significant |
| Irrigation benefit size | ✅ 6.0% error |
| Sensitivity | ✅ **18/18** hold |
| Drought years | ✅ found unprompted |
| Mass balance | ✅ closes to 1e-13 |
| Rotation effect | ⚠️ we over-predict (+18% vs +10.8%) |
| Punjab groundwater | ⚠️ direction right (20/20 wells), magnitude off 5–13× |
| Ground cover vs MODIS NDVI | ⚠️ 1 of 5 pixels comparable — and it passes (r = +0.772) |
| **Paddy irrigation vs NASS** | ❌ **+77% over.** Cause found and quantified |
| Punjab yields | ❌ not validated — no data obtainable |

**14 bugs found in our own model, all documented.**

### The product
- **7 screens:** Analyse · Field · Rotations · Compare · Evidence · Climate stress · Validation
- Progressive disclosure, semantic colour per measure, motion that carries meaning
- Sidebar navigation, light/dark, flip cards, live chart, rotation calendar strips
- Works down to phone width
- **Live analysis of any point on Earth** — ~30 seconds for a US field
- **Printable one-page field plan** — no JavaScript, works offline once printed

### Documents
`SUBMISSION.md` · `DEMO.md` · `JUDGES.md` · `EXPLAIN.md` · `DECISIONS.md` (full decision log)

---

## ❌ NOT DONE

### 🔴 Blocking the submission — do these first
| Item | Why it matters | Effort |
|---|---|---|
| **30-second video** | **Space Apps requires it.** Currently a zero, not a weakness | 1–2 hrs |
| **Submit the project page** | Content is written in `SUBMISSION.md` but not pasted into the form | 30 min |

### 🟡 Would meaningfully improve scoring
| Item | Why | Effort |
|---|---|---|
| ~~Climate scenario mode~~ | ✅ **DONE** — 6 named stress tests, ~26 s each on a precomputed field | — |
| ~~NDVI validation~~ | ✅ **DONE** — gave Punjab its first crop-side check (r = +0.772) and found a defect in our own site coordinates | — |
| ~~Paddy validation~~ | ✅ **DONE** — Arkansas, and the model **failed**: +77% over observed. Cause diagnosed | — |
| **Fix the paddy flood window** | The failure above needs one rice crop split into a dry phase and a ponded phase. Real work, not a parameter change | 3–4 hrs |

### 🟢 Real gaps, honestly stated, not fixable today
| Gap | Status |
|---|---|
| **No farmer has used it** | Zero field trials. The single biggest limitation |
| **India yields unvalidated** | We could not obtain district harvest records. Groundwater validated the *water*, not the *crop* |
| **India water off 5–13×** | Our model doesn't know canal irrigation exists. Needs a district water budget |
| **No money** | No prices, subsidies, or markets. The crop table refuses to invent them |
| **No methane** | Flooded rice emits it; we don't model it, so our carbon figure flatters rice |
| **Paddy model over-predicts water** | +77% against Arkansas. Cause known (floods 140 d where the field floods 95), fix not yet made |
| **3 NASA datasets** | POWER, NASADEM and MODIS NDVI via AppEEARS. Deeper than most, still narrower than teams listing six |
| **OpenET blocked** | Returns HTTP 408, server-side credential error. Tried, logged, failed |
| **Jobs are in-memory** | On-demand results vanish if the server restarts. Precomputed ones don't |
| **SoilGrids rate-limits** | Global soil can be unavailable for long stretches; US falls back to SSURGO automatically |

---

## If you have one hour

1. **Record the 30-second video** — the only true zero left on this list
2. Paste `SUBMISSION.md` into the Space Apps project page

## If you have one day

3. **Fix the paddy flood window** — split rice into a dry phase and a ponded phase,
   then re-run `validate_paddy.py`. If it lands inside 610–640 mm, the Punjab water
   figures gain their first real footing
4. Show it to **one actual farmer or agronomist** and write down what they say

---

## Honest summary

The **science and the validation are strong** — that's where the work went, and
it is unusual to be able to show eleven checks — six passing, three partial, **two
outright failures we found ourselves** — and fourteen bugs in our own work.

The **product is complete and demonstrable** — anyone can pick a field anywhere on
Earth and get an audited answer in about thirty seconds.

What's thin is **outward-facing**: no video, no farmer has touched it, and we use
fewer NASA datasets than teams who use them more shallowly.

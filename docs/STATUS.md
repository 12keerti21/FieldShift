# Project status — what is done, what is not

Verified against the repository, not written from memory.
**34 commits · 816 tests passing · 42 source modules · 33 test files · 19 API routes**

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

### Regions
- **Tier 1 — US High Plains:** 4 fields (KS ×2, NE, TX), 8 rotation patterns, validated
- **Tier 2 — Indian Punjab:** Sangrur, 10 rotation patterns, transferred and marked as such
- A tier-2 region **cannot claim validation** — enforced in code and tested

### Validation — 9 checks: 6 pass, 2 partial, 1 fail
| Check | Result |
|---|---|
| Weather maths vs gridMET | **−3.2%** |
| Stress vs real harvests | **11/11 right direction**, 8/11 significant |
| Irrigation benefit size | 6.0% error |
| Sensitivity | **18/18** hold |
| Drought years | found unprompted |
| Mass balance | closes to 1e-13 |
| Rotation effect | ⚠️ we over-predict (+18% vs +10.8%) |
| Punjab groundwater | ⚠️ direction right (20/20 wells), size off 5–13× |
| Punjab yields | ❌ not validated — no data obtainable |

**12 bugs found in our own model, all documented.**

### The product
- **7 screens:** Analyse · Field · Rotations · Compare · Evidence · **Climate stress** · Validation
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
| ~~NDVI validation~~ | 🔄 **IN PROGRESS** — adapter and validation script built and tested; waiting on the NASA AppEEARS task to finish so the numbers can be produced | — |

### 🟢 Real gaps, honestly stated, not fixable today
| Gap | Status |
|---|---|
| **No farmer has used it** | Zero field trials. The single biggest limitation |
| **India yields unvalidated** | We could not obtain district harvest records. Groundwater validated the *water*, not the *crop* |
| **India water off 5–13×** | Our model doesn't know canal irrigation exists. Needs a district water budget |
| **No money** | No prices, subsidies, or markets. The crop table refuses to invent them |
| **No methane** | Flooded rice emits it; we don't model it, so our carbon figure flatters rice |
| **3 NASA datasets** | POWER, NASADEM and MODIS NDVI via AppEEARS. Deeper than most, still narrower than teams listing six |
| **OpenET blocked** | Returns HTTP 408, server-side credential error. Tried, logged, failed |
| **Jobs are in-memory** | On-demand results vanish if the server restarts. Precomputed ones don't |
| **SoilGrids rate-limits** | Global soil can be unavailable for long stretches; US falls back to SSURGO automatically |

---

## If you have one hour

1. **Record the 30-second video** — it is now the only true zero on this list
2. Finish the NDVI validation once the AppEEARS task lands (the code is ready)

## If you have one day

3. Show it to **one actual farmer or agronomist** and write down what they say
4. Add a second non-US district, now that the groundwater adapter works

---

## Honest summary

The **science and the validation are strong** — that's where the work went, and
it's unusual to be able to show nine checks, two partial failures and twelve
self-found bugs.

The **product is complete and demonstrable** — anyone can pick a field anywhere on
Earth and get an audited answer in about thirty seconds.

What's thin is **outward-facing**: no video, no farmer has touched it, and we use
fewer NASA datasets than teams who use them more shallowly.

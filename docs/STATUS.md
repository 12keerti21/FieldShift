# Project status — what is done, what is not

Verified against the repository, not written from memory.
**31 commits · 791 tests passing · 40 source modules · 31 test files · 17 API routes**

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

### The decision layer
- Enumerates every rotation, removes duplicates, **explains every rejection**
- Ranks on 6 measures; finds the options nothing beats outright
- Tells you when **your own weighting**, not the evidence, is deciding the answer
- Evidence card on every recommendation: sources, resolution mismatch, unknowns, who must check it
- Guardrails screen each crop against the local record

### Data adapters (all working)
NASA POWER · NASADEM · USDA SSURGO · ISRIC SoilGrids · USDA NASS · gridMET · **India-WRIS groundwater**

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
- **5 screens:** Analyse · Field · Rotations · Compare · Evidence · Validation
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
| **Climate scenario mode** | Judges ask for it; the machinery already exists — perturb weather +2 °C / −20% rain and re-run | ~30 min |
| **NDVI validation of bare ground** | Fixes two problems at once: validates the one measure we can't check anywhere, *and* adds a NASA dataset used as evidence rather than decoration | 2–3 hrs |

### 🟢 Real gaps, honestly stated, not fixable today
| Gap | Status |
|---|---|
| **No farmer has used it** | Zero field trials. The single biggest limitation |
| **India yields unvalidated** | We could not obtain district harvest records. Groundwater validated the *water*, not the *crop* |
| **India water off 5–13×** | Our model doesn't know canal irrigation exists. Needs a district water budget |
| **No money** | No prices, subsidies, or markets. The crop table refuses to invent them |
| **No methane** | Flooded rice emits it; we don't model it, so our carbon figure flatters rice |
| **Only 2 NASA datasets** | POWER and NASADEM. Deep, but narrow next to teams listing six |
| **OpenET blocked** | Returns HTTP 408, server-side credential error. Tried, logged, failed |
| **Jobs are in-memory** | On-demand results vanish if the server restarts. Precomputed ones don't |
| **SoilGrids rate-limits** | Global soil can be unavailable for long stretches; US falls back to SSURGO automatically |

---

## If you have one hour

1. **Record the 30-second video** — it's the only true zero on this list
2. Build the **climate scenario mode** (~30 min) — turns a trap question into a live demo

## If you have one day

3. **NDVI validation** — the highest-value technical work left
4. Show it to **one actual farmer or agronomist** and write down what they say

---

## Honest summary

The **science and the validation are strong** — that's where the work went, and
it's unusual to be able to show nine checks, two partial failures and twelve
self-found bugs.

The **product is complete and demonstrable** — anyone can pick a field anywhere on
Earth and get an audited answer in about thirty seconds.

What's thin is **outward-facing**: no video, no farmer has touched it, and we use
fewer NASA datasets than teams who use them more shallowly.

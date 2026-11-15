# Data sources — assessed, used, and refused

A record of what the challenge offered, what we actually ingested, and what we
tried and could not get. The last of those three is the most useful: a dataset we
attempted and failed to obtain tells you more than one we never looked at.

---

## The finding that shaped the architecture

The offered resource list is really **two lists**.

Thirteen of them are **United States only** — SSURGO, Web Soil Survey, Cropland
Data Layer, NAIP, NLCD, PRISM, NASS QuickStats, AMS Market News, OpenET, CoCoRaHS,
NWIS, Ag Data Commons, LANDFIRE. For a US field those close a validation loop that
exists almost nowhere else on earth. The rest — POWER, IMERG, SMAP, Landsat,
MODIS, ECOSTRESS, SRTM, GEDI, Sentinel-2, ERA5, GAEZ — are global.

That asymmetry is not a detail. It means a tool can be *validated* in the United
States or *useful* everywhere, and the two are not the same claim.

So we built both and made the engine say which one you are looking at:

| | |
|---|---|
| **Tier 1 — validated** | US fields. Checked against 41 years of county yields. |
| **Tier 2 — transferred** | Anywhere else. Same engine, global inputs only, and an evidence card that cannot read above LOW confidence. |

The tier is enforced in code, not asserted in prose: a tier-2 region is refused at
construction if it tries to claim validation, and there is a test for it.

---

## Actually ingested

### NASA

| Dataset | What it does here | Access |
|---|---|---|
| **NASA POWER** | The weather contract — `T2M_MAX`, `T2M_MIN`, `PRECTOTCORR`, `RH2M`, `WS2M`, `ALLSKY_SFC_SW_DWN`, daily, 1984–2024, at every field. Drives the water balance and through it yield, carbon, nitrogen and erosion. **Every number in the tool traces back to it.** | REST JSON, no auth |
| **NASADEM** | 30 m elevation → slope → the RUSLE length-slope factor. `.hgt` read directly as raw big-endian int16, so no geospatial stack is needed. | Earthdata login |
| **MODIS MOD13Q1 NDVI** | 16-day 250 m vegetation index, 2003–2024, as an **independent check** on modelled ground cover — the one objective nothing else could test. | NASA AppEEARS, Earthdata login |

### Partner and other

| Dataset | What it does here |
|---|---|
| **USDA SSURGO** | Field-scale soil for US sites. Measured available water capacity replaces our class centroid — at the pilot point the difference was 52%. |
| **ISRIC SoilGrids v2.0** | Global soil, and the only reason the engine can leave the United States. Never presented as equivalent to a survey. |
| **USDA NASS QuickStats** | County yields by production practice, and the irrigation census. The yields split by *preceding crop* are what made validating a rotation effect possible at all. |
| **gridMET** | Published 4 km reference ET, used purely as an independent check on ours. |
| **CGWB via India-WRIS** | Groundwater observation wells. 20 of them in Sangrur, records up to 27 years. |

---

## Tried and could not get

Each of these was attempted, and the failure is recorded rather than the dataset
quietly dropped.

| Dataset | What happened |
|---|---|
| **OpenET** | HTTP 408 with a server-side Earth Engine credential error, across all three models and both endpoints. Account authenticates; 0 of 100 requests consumed. |
| **USGS NWIS groundwater** | The `gwlevels` service was retired. The replacement OGC API does not expose county historical levels. |
| **CropScape / CDL** | Timed out over both HTTP and HTTPS. |
| **GES DISC Data Rods** | Returns an empty shell page. |
| **Indian district yield statistics** | Nothing obtainable. **This single gap is why Punjab is Tier 2 rather than Tier 1.** |

---

## Assessed and deliberately not used

- **SMAP** — surface soil moisture would validate the water balance, and it is the
  obvious next addition. Not reached.
- **ECOSTRESS, Landsat, Sentinel-2** — order queues and cloud masking for a
  capability we could reach more cheaply through MODIS.
- **IMERG** — sub-daily intensity for a proper RUSLE R factor. We use the Renard
  and Freimund regression instead, and say so on the erosion figure.
- **PRISM** — 4 km and topography-aware, so better than POWER for US forcing. We
  kept POWER everywhere instead, because using a different weather source in each
  tier would make the Kansas–Punjab comparison meaningless.
- **GEDI** — the resource text itself concedes it works best under taller
  vegetation. On cropland, sparse footprints add little.
- **LANDFIRE** — wildland fuels. No path to a rotation decision.
- **NAIP** — excellent for field boundaries, but boundary delineation is a project
  in itself. The farmer draws the polygon.
- **AMS Market News** — prices. The crop table refuses to invent a price, and we
  did not have time to wire real ones, so every marketed crop carries
  `price_local_required: true` and the tool reports physical production only.

### A noted absence

**GRACE / GRACE-FO** is named in the challenge text but not in the Resources tab.
For a specific field, in-situ well records are the better instrument anyway —
longer, and measuring the aquifer the farmer actually pumps from. That is the route
we took in Punjab, through CGWB.

---

## What the data could not supply, and we had to write ourselves

- **Crop calendars** — sowing windows and durations for each region. Written per
  region and listed for local review, because no dataset carries them.
- **FAO-33 Ky yield response factors** — ten of the India-tier crops have no
  published entry and use a documented analogue, tagged low confidence.
- **Local pathogen presence** — reported as `regional` and never used to block a
  rotation, because confirming it is a local question.

---

**Standing rule.** A validation target must be a dataset we fetched or a citation
we can point at — never a remembered number. Adopted after an earlier claim of a
"+18% ET0 bias" came from memory and gridMET showed −3.2%. It was withdrawn, and
`docs/DECISIONS.md` records the retraction.

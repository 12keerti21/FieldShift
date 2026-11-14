# Resources — challenge list, assessed

Received 2026-11-14. Assessed for what each dataset does for *this* architecture,
not just what it contains.

## The decisive finding: the list is two lists

Thirteen of the offered datasets are **United States only**: SSURGO/STATSGO2, Web
Soil Survey, Cropland Data Layer, NAIP, NLCD, PRISM, NASS QuickStats, AMS Market
News, OpenET, CoCoRaHS, NWIS, Ag Data Commons, LANDFIRE.

For a US field, those close a **complete validation loop** that exists nowhere else
on earth:

| Our model output | Ground truth available |
|---|---|
| Rotation history inferred from NDVI phenology | **CDL** — labelled 30 m annual crop classes |
| Water balance ET | **OpenET** — field-scale ET (western US) |
| Soil hydraulic parameters | **SSURGO** — field-scale, replaces our class centroids |
| Yield / stress index | **NASS QuickStats** — county yields, multi-decade |
| IMERG rainfall | **CoCoRaHS** — gauge observations |
| Crop prices (which our table refuses to invent) | **AMS Market News** |
| Satellite climate estimates | **PRISM** — topography-aware, 30-year normals |
| Groundwater decline constraint | **NWIS** well records, *in situ* |

The remaining datasets are **global**: POWER, IMERG, SMAP, Landsat, MODIS/VIIRS,
ECOSTRESS, SRTM, GEDI, Sentinel-2, ERA5, GAEZ.

### What this implies for the build

A **two-tier design**, where the tool knows which tier a field is in and says so:

- **Tier 1 (validated):** US field. Every layer is checked against ground truth, and
  the numbers shown carry measured skill scores.
- **Tier 2 (transferable):** anywhere else. Same engine, global inputs only, soil
  from class centroids, no local price data — and the evidence card states the
  downgrade explicitly.

This is a stronger position than choosing either alone. "Validated where ground
truth exists, transferable where it does not, and honest about which you are
looking at" answers the challenge's own demand to explain uncertainty and data
gaps, and it is a claim almost no other team will be able to substantiate.

## Dataset assessment

### Tier A — fetch first, API-fast, no granule wrangling
| Dataset | Gives us | Resolution | Access | Status |
|---|---|---|---|---|
| **NASA POWER** | the entire weather contract: T2M_MAX/MIN, RH2M, WS2M, ALLSKY_SFC_SW_DWN, PRECTOTCORR | ~0.5°, daily, 1981– | REST JSON, no auth | **adapter built** |
| GPM IMERG | precipitation, sub-daily intensity for RUSLE R / EI30 | 0.1°, 30 min | GES DISC / Earthdata | next |
| SRTM / NASADEM | slope and length for RUSLE LS | 30 m | Earthdata | next |
| PRISM | climate validation, frost dates, normals | 4 km, daily | direct download (US) | after |
| NASS QuickStats | county yields for validation | county, annual | REST API, free key | after |

### Tier B — slow orders, start them before they are needed
| Dataset | Gives us | Why it is slow |
|---|---|---|
| SMAP | surface soil moisture; validates our water balance | AppEEARS / Earthdata order queue |
| ECOSTRESS | land-surface temperature and ET at field scale | large granules, sparse revisit, order queue |
| Landsat / Sentinel-2 | NDVI phenology → infer the existing rotation | many scenes, cloud masking |
| CDL | **labelled** rotation history — our phenology ground truth | CropScape bulk download per state |
| SSURGO | field-scale soil properties | per-survey-area downloads |

### Tier C — valuable, only if time allows
OpenET (western US only), NWIS, CoCoRaHS, NLCD, NAIP, GAEZ, ERA5, Ag Data Commons,
AMS Market News, LANDFIRE.

### Deliberately deprioritised, with reasons
- **GEDI** — the resource text itself concedes it is "most effective in areas with
  taller vegetation". On cropland its canopy and biomass metrics add little, and
  sparse-footprint sampling does not give field coverage. Not worth the ingest cost.
- **LANDFIRE** — wildland fuels and vegetation condition. Interesting landscape
  context, no path to a rotation decision.
- **NAIP** — 1 m imagery is excellent for field boundaries, but boundary delineation
  is a project in itself. Let the farmer draw the polygon instead.

### Noted absence
**GRACE / GRACE-FO is named in the challenge text but not in the Resources tab.**
Still reachable via PO.DAAC (JPL mascon solutions). For a US field, **NWIS well
records are the better instrument anyway** — in situ, long, and at the aquifer the
farmer actually pumps from. Use NWIS as the primary groundwater-decline constraint
and GRACE as the regional corroboration, which is a more defensible pairing than
GRACE alone.

## Access routes to confirm
- [ ] Earthdata Login created for every team member (needed for SMAP, ECOSTRESS,
      IMERG, Landsat, SRTM, GEDI)
- [ ] `earthaccess` or AppEEARS tested end-to-end on ONE small granule
- [x] POWER confirmed to need no authentication
- [ ] NASS QuickStats API key requested
- [ ] CropScape / CDL bulk download path for the chosen state

## Still needed, not in the list
- [ ] Sowing windows / crop calendars for the chosen region (region config)
- [ ] `Ky` FAO-33 yield response factors, to convert our stress index into yield
- [ ] Local pathogen presence, to escalate the `regional` constraints we currently
      report but do not enforce

## Open questions for mentors
- [ ] Is a non-US pilot region acceptable given how US-centric the data offering is?
- [ ] Any constraint on compute or on dataset size for the submission?

"""Precomputed site bundles: the heavy work, done once.

Simulating eighty candidate rotations through forty-one years of weather takes about
a minute. That cannot happen inside a web request, and pretending otherwise would
produce either a timeout or a tool that silently cuts corners under load.

So the split is deliberate and it maps onto what is actually expensive:

    PRECOMPUTED   fetching weather and soil, simulating every candidate rotation,
                  scoring them, screening them against the guardrails. Minutes.
    LIVE          re-ranking that scored set against a farmer's stated priorities,
                  and finding the Pareto front. Milliseconds.

That division is not just an optimisation. The expensive part is physics and does
not depend on anyone's preferences; the cheap part is entirely preference and should
be instant, because a farmer exploring trade-offs needs to move a slider and see the
answer move. Precomputing the preferences instead would have been the wrong way
round.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from ..agro.climate import Site
from ..agro.et0 import et0_penman_monteith
from ..agro.rothc import soc_from_organic_carbon_pct
from ..data import load_registry
from ..decide.candidates import build_rotation
from ..decide.evidence import build_card  # noqa: F401  (re-exported for the API)
from ..decide.guardrails import check_crop
from ..decide.objectives import OBJECTIVES, select_objectives
from ..decide.search import search_rotations
from ..io.power import load_power_daily, parse_power_daily
from ..io.soilgrids import fetch_soilgrids_point, texture_from_soilgrids
from ..io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo
from ..io.terrain import field_slope_percent, ls_factor
from .regions import PADDY, PIVOT, TUBEWELL, Region, region  # noqa: F401

BUNDLE_DIR = Path("data/bundles")
RAW_DIR = Path("data/raw")

OBJECTIVE_KEYS = (
    "production", "downside", "irrigation", "carbon", "wind_exposure", "bare_ground"
)


@dataclass(frozen=True)
class SiteSpec:
    """A field FieldShift has a precomputed answer for."""

    id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float
    state: str
    county: str
    note: str
    dem_tile: str | None = None
    existing_rotation: tuple[str, ...] = ()
    field_hectares: float = 65.0
    # Which region's calendar, soil source and evidence apply here. This is the
    # only thing that has to change to move the engine to a new part of the world.
    region: str = "us_high_plains"

    @property
    def region_spec(self) -> Region:
        return region(self.region)

    @property
    def site(self) -> Site:
        return Site(self.latitude, self.longitude, self.elevation_m, self.name)


SITES: tuple[SiteSpec, ...] = (
    SiteSpec(
        "finney_ks", "Finney County, Kansas", 38.03, -100.74, 858.0, "KS", "FINNEY",
        "Semi-arid High Plains over the Ogallala aquifer. Irrigated corn against "
        "dryland wheat and sorghum, with a declining water table.",
        dem_tile="nasadem_n38w101/n38w101.hgt",
        existing_rotation=("wheat", "fallow"),
    ),
    SiteSpec(
        "sedgwick_ks", "Sedgwick County, Kansas", 37.70, -97.46, 410.0, "KS",
        "SEDGWICK",
        "Wetter eastern Kansas at about 840 mm. Water is much less often the "
        "binding constraint here.",
        existing_rotation=("wheat", "fallow"),
    ),
    SiteSpec(
        "holt_ne", "Holt County, Nebraska", 42.45, -98.78, 610.0, "NE", "HOLT",
        "Cooler Nebraska Sandhills on a coarse soil holding only 84 mm/m of "
        "available water - a quarter of the Kansas pilot field.",
        existing_rotation=("wheat", "fallow"),
    ),
    SiteSpec(
        "hale_tx", "Hale County, Texas", 34.07, -101.82, 1030.0, "TX", "HALE",
        "Hotter southern High Plains. Heat at flowering is a routine cost here "
        "rather than an exception.",
        existing_rotation=("wheat", "fallow"),
    ),
    # ---- Tier 2: the transfer case ----
    # Same engine, global-only inputs. Sangrur sits in the part of central Punjab
    # with the steepest reported water-table decline, under the rice-wheat system
    # that causes it. It is the same shape of problem as the Ogallala, which is
    # why it is the transfer worth running rather than an arbitrary second site.
    SiteSpec(
        "sangrur_pb", "Sangrur District, Punjab", 30.23, 76.09, 232.0,
        "PB", "SANGRUR",
        "Rice-wheat on the central Punjab plain, irrigated from a falling water "
        "table. Run on global inputs only - predicted soil, no yield series to "
        "check against - so every number here is the engine transferred, not "
        "validated.",
        existing_rotation=("rice_wheat",),
        region="in_punjab",
        field_hectares=2.5,
    ),
)

SITES_BY_ID = {s.id: s for s in SITES}


def _power_path(spec: SiteSpec) -> Path:
    return RAW_DIR / f"power_{spec.state}_{spec.county}.json".lower()


def _ssurgo_path(spec: SiteSpec) -> Path:
    return RAW_DIR / f"ssurgo_{spec.state}_{spec.county}.json".lower()


def _soilgrids_path(spec: SiteSpec) -> Path:
    return RAW_DIR / f"soilgrids_{spec.state}_{spec.county}.json".lower()


def _load_soil(spec: SiteSpec, registry):
    """Soil from whichever survey reaches this field.

    Returns the texture plus a short provenance label for the bundle. The two
    sources are not interchangeable and the difference is reported rather than
    smoothed over: SSURGO is a survey of a mapped component, SoilGrids is a 250 m
    global prediction with no hydrologic group, so a SoilGrids site carries lower
    confidence and says which input caps it.
    """
    source = spec.region_spec.soil_source
    if source == "ssurgo":
        profile = fetch_ssurgo_point(
            spec.latitude, spec.longitude, cache_path=str(_ssurgo_path(spec))
        )
        soil = texture_from_ssurgo(profile, registry.texture)
        return soil, {
            "component": profile.component,
            "component_pct": profile.component_pct,
        }
    if source == "soilgrids":
        profile = fetch_soilgrids_point(
            spec.latitude, spec.longitude, cache_path=str(_soilgrids_path(spec))
        )
        soil = texture_from_soilgrids(profile, registry.texture)
        return soil, {
            "component": "SoilGrids 250 m prediction",
            "component_pct": None,
            "described_depth_cm": profile.described_depth_cm,
        }
    raise ValueError(f"unknown soil source {source!r}")


def build_bundle(spec: SiteSpec, *, cycle_years: int = 3, max_candidates: int = 90) -> dict:
    """Run the whole pipeline for one site and return a serialisable bundle."""
    registry = load_registry()

    path = _power_path(spec)
    if path.exists():
        weather, report = parse_power_daily(json.loads(path.read_text()), site=spec.site)
    else:
        weather, report = load_power_daily(
            spec.site, "1984-01-01", "2024-12-31", cache_path=str(path)
        )
    et0 = et0_penman_monteith(spec.site, weather)

    soil, soil_meta = _load_soil(spec, registry)
    soc = soc_from_organic_carbon_pct(
        soil.organic_carbon_pct, soil.bulk_density_g_cm3
    )

    # Terrain is only available where a DEM tile has been downloaded. Without it the
    # erosion factor falls back to a stated default rather than being invented.
    slope = None
    if spec.dem_tile and (RAW_DIR / spec.dem_tile).exists():
        slope, _ = field_slope_percent(
            str(RAW_DIR / spec.dem_tile), spec.latitude, spec.longitude
        )
        ls = ls_factor(slope, 75.0)
        terrain_note = f"slope {slope:.2f}% from NASADEM, fitted over 300 m"
    else:
        ls = 0.25
        terrain_note = (
            "no DEM tile downloaded for this site, so the slope-length factor uses a "
            "placeholder of 0.25; the erosion figure here is indicative only"
        )

    region_spec = spec.region_spec
    patterns_all = region_spec.patterns

    existing_plan = None
    if spec.existing_rotation:
        patterns = [p for p in patterns_all if p.id in spec.existing_rotation]
        if patterns:
            existing_plan = build_rotation(
                patterns, name="EXISTING: " + " - ".join(p.name for p in patterns)
            )

    result = search_rotations(
        patterns_all,
        registry=registry, soil=soil, weather=weather, et0_mm=et0.values_mm,
        start_year=1984, initial_soc_t_ha=soc, ls=ls, cycle_years=cycle_years,
        constraints=region_spec.constraints,
        objectives=select_objectives(OBJECTIVE_KEYS),
        max_candidates=max_candidates,
        extra_plans=[existing_plan] if existing_plan else (),
    )

    years = np.unique(weather.year)[1:-1]
    rainfall = float(np.mean([weather.precip_mm[weather.year == y].sum() for y in years]))

    # Guardrail screen for every crop the patterns can place.
    windows = {}
    for pattern in patterns_all:
        for season in pattern.seasons:
            windows[season.crop_id] = (
                season.planting_doy,
                season.duration_days or registry.crop(season.crop_id).duration_days,
            )
    screen = {}
    for crop_id, (doy, duration) in windows.items():
        rep = check_crop(registry.crop(crop_id), weather, doy, duration)
        screen[crop_id] = {
            "crop": registry.crop(crop_id).name,
            "refused": rep.refused,
            "verdicts": [
                {
                    "rule": v.rule, "level": v.level.value, "message": v.message,
                    "frequency": v.frequency,
                    "needs_local_review": v.needs_local_review,
                }
                for v in rep.verdicts
            ],
        }

    rotations = []
    for index, (plan, score, outcome) in enumerate(
        zip(result.plans, result.scores, result.outcomes)
    ):
        rotations.append({
            "index": index,
            "name": score.name,
            "is_existing": score.name.startswith("EXISTING"),
            "on_front": index in result.front,
            "cycle_years": plan.cycle_years,
            "crops": list(plan.crop_sequence),
            # When each crop actually occupies the field. A rotation is a
            # calendar, and a list of crop names does not show the thing that
            # matters most to a farmer looking at it: where the gaps are, which
            # crop overwinters, and how tight the turnarounds get.
            "schedule": [
                {
                    "crop_id": season.crop_id,
                    "crop_name": registry.crop(season.crop_id).name,
                    "year_offset": season.year_offset,
                    "start_doy": season.planting_doy,
                    "duration_days": int(
                        season.duration_days
                        or registry.crop(season.crop_id).duration_days
                    ),
                    "irrigation": season.irrigation.mode,
                    "fertiliser_n_kg_ha": season.fertiliser_n_kg_ha,
                    "group": registry.crop(season.crop_id).group,
                }
                for season in plan.seasons
            ],
            "notes": list(score.notes)[:3],
            "objectives": {
                o.key: float(o.extract(score)) for o in result.objectives
            },
            "detail": {
                "crops_per_decade": score.crops_per_decade,
                "evaporation_fraction": score.evaporation_fraction,
                "nitrogen_limited_seasons": outcome.nitrogen_limited_seasons,
                "nitrogen_fixed_kg_ha": outcome.total_nitrogen_fixed_kg_ha,
                "nitrogen_leached_kg_ha": outcome.total_nitrogen_leached_kg_ha,
                "seasons_simulated": sum(1 for s in outcome.seasons if not s.is_fallow),
                "years": score.years,
            },
        })

    return {
        "site": {
            **{k: v for k, v in asdict(spec).items() if k != "existing_rotation"},
            "existing_rotation": list(spec.existing_rotation),
            "rainfall_mm_yr": rainfall,
            "soil": {
                **soil_meta,
                "texture": soil.name,
                "available_water_mm_per_m": soil.taw_mm_per_m,
                "measured": soil.taw_is_measured,
                "provenance": soil.taw_provenance,
                "organic_carbon_pct": soil.organic_carbon_pct,
                "hydrologic_group": soil.hydrologic_group,
                "source": soil.source,
            },
            "terrain": {"slope_percent": slope, "ls_factor": ls, "note": terrain_note},
            "weather": {
                "years": int(len(years)),
                "first_year": int(weather.year[0]),
                "last_year": int(weather.year[-1]),
                "et0_confidence": et0.confidence,
                "et0_method": et0.method.value,
                "record_confidence": report.confidence,
                "annual_et0_mm": float(et0.values_mm.sum() / len(years)),
            },
            "starting_carbon_t_ha": soc,
        },
        "region": {
            "id": region_spec.id,
            "name": region_spec.name,
            "tier": region_spec.tier,
            "validated": region_spec.is_validated,
            "yield_validation": region_spec.yield_validation,
            "soil_source": region_spec.soil_source,
            "review_first": list(region_spec.review_first),
            "note": region_spec.note,
        },
        "objectives": [
            {
                "key": o.key, "label": o.label, "units": o.units,
                "maximise": o.maximise, "note": o.note,
            }
            for o in result.objectives
        ],
        "rotations": rotations,
        "front": list(result.front),
        "search": {
            "examined": result.candidates.examined,
            "feasible": len(result.candidates),
            "simulated": len(result.scores),
            "on_front": len(result.front),
            "rejections": result.candidates.rejection_tally,
        },
        "guardrails": screen,
    }


def write_bundles(
    specs: Sequence[SiteSpec] = SITES, out_dir: Path = BUNDLE_DIR, **kw
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for spec in specs:
        bundle = build_bundle(spec, **kw)
        path = out_dir / f"{spec.id}.json"
        path.write_text(json.dumps(bundle, indent=1))
        written.append(path)
    return written


def load_bundle(site_id: str, out_dir: Path = BUNDLE_DIR) -> dict:
    path = out_dir / f"{site_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no precomputed bundle for {site_id!r}. Build it with "
            "python -m fieldshift.api.bundle"
        )
    return json.loads(path.read_text())


def available_sites(out_dir: Path = BUNDLE_DIR) -> list[dict]:
    """Every site with a bundle on disk.

    Scans the directory rather than reading a written index. An index file goes
    stale the moment someone rebuilds a subset of sites - which happened: building
    three bundles rewrote the index and silently dropped the fourth, so the pilot
    field vanished from the API while its bundle sat on disk. Deriving the list from
    what actually exists cannot drift.
    """
    if not out_dir.exists():
        return []
    sites = []
    for path in sorted(out_dir.glob("*.json")):
        if path.stem == "index":
            continue
        try:
            site = json.loads(path.read_text())["site"]
        except (json.JSONDecodeError, KeyError):
            continue
        try:
            region_block = json.loads(path.read_text()).get("region", {})
        except json.JSONDecodeError:
            region_block = {}
        sites.append({
            "id": site["id"], "name": site["name"],
            "latitude": site["latitude"], "longitude": site["longitude"],
            "note": site["note"],
            "field_hectares": site.get("field_hectares"),
            # The tier is surfaced here so the map can distinguish a validated
            # field from a transferred one before anything is clicked. A viewer
            # should not have to open a site to find out which kind it is.
            "region": region_block.get("id"),
            "region_name": region_block.get("name"),
            "tier": region_block.get("tier", 1),
            "validated": region_block.get("validated", True),
        })
    # Keep the declared order where the site is known, so the pilot field leads.
    order = {s.id: i for i, s in enumerate(SITES)}
    return sorted(sites, key=lambda s: order.get(s["id"], 999))


if __name__ == "__main__":
    import sys

    only = sys.argv[1:] or None
    specs = [s for s in SITES if not only or s.id in only]
    for path in write_bundles(specs):
        print(f"wrote {path}")

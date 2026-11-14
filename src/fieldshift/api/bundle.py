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
from ..agro.rotation import SeasonPlan
from ..agro.waterbalance import IrrigationPolicy
from ..data import load_registry
from ..decide.candidates import RotationConstraints, YearPattern, build_rotation
from ..decide.evidence import (
    CROP_TABLE,
    GRIDMET,
    NASADEM,
    POWER,
    QUICKSTATS,
    SSURGO,
    build_card,
)
from ..decide.guardrails import check_crop
from ..decide.objectives import OBJECTIVES, select_objectives
from ..decide.search import search_rotations
from ..io.power import load_power_daily, parse_power_daily
from ..io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo
from ..io.terrain import field_slope_percent, ls_factor

BUNDLE_DIR = Path("data/bundles")
RAW_DIR = Path("data/raw")

PIVOT = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.85,
    refill_to_depletion_frac=0.25, max_application_mm=25,
)

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

    @property
    def site(self) -> Site:
        return Site(self.latitude, self.longitude, self.elevation_m, self.name)


# Year patterns for the US High Plains calendar. Winter wheat is sown in October and
# harvested the following July, so a wheat year leaves no room for a summer crop in
# the year after it - the calendar check enforces that rather than trusting us.
HIGH_PLAINS_PATTERNS: tuple[YearPattern, ...] = (
    YearPattern("fallow", "fallow", (), "banks water, loses carbon and nitrogen"),
    YearPattern(
        "wheat", "winter wheat",
        (SeasonPlan("wheat_winter", 274, 285, fertiliser_n_kg_ha=70.0),),
        "dryland, 70 kg N/ha",
    ),
    YearPattern(
        "wheat_irr", "winter wheat (irrigated)",
        (SeasonPlan("wheat_winter", 274, 285, irrigation=PIVOT,
                    fertiliser_n_kg_ha=100.0),),
        "centre pivot",
    ),
    YearPattern(
        "sorghum", "grain sorghum",
        (SeasonPlan("sorghum", 152, 125, fertiliser_n_kg_ha=50.0),),
        "dryland, drought tolerant",
    ),
    YearPattern(
        "corn_irr", "corn (irrigated)",
        (SeasonPlan("maize", 125, 150, irrigation=PIVOT, fertiliser_n_kg_ha=200.0),),
        "the thirstiest option here",
    ),
    YearPattern(
        "soy_irr", "soybean (irrigated)",
        (SeasonPlan("soybean", 140, 140, irrigation=PIVOT),),
        "fixes its own nitrogen",
    ),
    YearPattern(
        "sorg_cover", "sorghum + rye cover",
        (SeasonPlan("sorghum", 152, 125, fertiliser_n_kg_ha=50.0),
         SeasonPlan("cereal_rye_cover", 288, 70)),
        "a cover crop closes the autumn window",
    ),
    YearPattern(
        "millet", "pearl millet",
        (SeasonPlan("pearl_millet", 152, 100, fertiliser_n_kg_ha=40.0),),
        "lowest water use of any grain here",
    ),
)

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
)

SITES_BY_ID = {s.id: s for s in SITES}


def _power_path(spec: SiteSpec) -> Path:
    return RAW_DIR / f"power_{spec.state}_{spec.county}.json".lower()


def _ssurgo_path(spec: SiteSpec) -> Path:
    return RAW_DIR / f"ssurgo_{spec.state}_{spec.county}.json".lower()


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

    profile = fetch_ssurgo_point(
        spec.latitude, spec.longitude, cache_path=str(_ssurgo_path(spec))
    )
    soil = texture_from_ssurgo(profile, registry.texture)
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

    existing_plan = None
    if spec.existing_rotation:
        patterns = [p for p in HIGH_PLAINS_PATTERNS if p.id in spec.existing_rotation]
        if patterns:
            existing_plan = build_rotation(
                patterns, name="EXISTING: " + " - ".join(p.name for p in patterns)
            )

    result = search_rotations(
        HIGH_PLAINS_PATTERNS,
        registry=registry, soil=soil, weather=weather, et0_mm=et0.values_mm,
        start_year=1984, initial_soc_t_ha=soc, ls=ls, cycle_years=cycle_years,
        constraints=RotationConstraints(
            max_consecutive_fallow_years=1, max_fallow_fraction=0.5,
            max_irrigated_years=2,
        ),
        objectives=select_objectives(OBJECTIVE_KEYS),
        max_candidates=max_candidates,
        extra_plans=[existing_plan] if existing_plan else (),
    )

    years = np.unique(weather.year)[1:-1]
    rainfall = float(np.mean([weather.precip_mm[weather.year == y].sum() for y in years]))

    # Guardrail screen for every crop the patterns can place.
    windows = {}
    for pattern in HIGH_PLAINS_PATTERNS:
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
                "component": profile.component,
                "component_pct": profile.component_pct,
                "texture": soil.name,
                "available_water_mm_per_m": soil.taw_mm_per_m,
                "measured": soil.taw_is_measured,
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
        sites.append({
            "id": site["id"], "name": site["name"],
            "latitude": site["latitude"], "longitude": site["longitude"],
            "note": site["note"],
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

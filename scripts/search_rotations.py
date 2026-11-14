"""Search the rotation space for the pilot field and show the trade-offs.

    PYTHONPATH=src ./.venv/bin/python scripts/search_rotations.py
"""

from __future__ import annotations

import json
import sys

import numpy as np

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.rothc import soc_from_organic_carbon_pct
from fieldshift.agro.rotation import SeasonPlan
from fieldshift.agro.waterbalance import RAINFED, IrrigationPolicy
from fieldshift.data import load_registry
from fieldshift.decide.candidates import RotationConstraints, YearPattern, build_rotation
from fieldshift.decide.objectives import select_objectives
from fieldshift.decide.pareto import weight_sensitivity, weighted_ranking
from fieldshift.decide.search import search_rotations
from fieldshift.io.power import parse_power_daily
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo
from fieldshift.io.terrain import field_slope_percent, ls_factor

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
PIVOT = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.85,
    refill_to_depletion_frac=0.25, max_application_mm=25,
)

# Year patterns valid for this calendar. Winter wheat is sown in October and
# harvested the following July, so a wheat year leaves no room for a summer crop in
# the year that follows it - the calendar check enforces that rather than trusting us.
PATTERNS = (
    YearPattern("fallow", "fallow", (), "no crop; banks water, loses carbon"),
    YearPattern("wheat", "winter wheat",
                (SeasonPlan("wheat_winter", 274, 285),), "dryland"),
    YearPattern("wheat_irr", "winter wheat (irr)",
                (SeasonPlan("wheat_winter", 274, 285, irrigation=PIVOT),), ""),
    YearPattern("sorghum", "grain sorghum",
                (SeasonPlan("sorghum", 152, 125),), "dryland, drought tolerant"),
    YearPattern("corn_irr", "corn (irr)",
                (SeasonPlan("maize", 125, 150, irrigation=PIVOT),), ""),
    YearPattern("soy_irr", "soybean (irr)",
                (SeasonPlan("soybean", 140, 140, irrigation=PIVOT),), ""),
    YearPattern("sorg_cover", "sorghum + rye cover",
                (SeasonPlan("sorghum", 152, 125),
                 SeasonPlan("cereal_rye_cover", 288, 70)),
                "cover crop closes the autumn window"),
    YearPattern("millet", "pearl millet",
                (SeasonPlan("pearl_millet", 152, 100),), "dryland, lowest water use"),
)


def main() -> int:
    registry = load_registry()

    payload = json.loads(open("data/raw/power_finney_ks_1981_2024.json").read())
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    weather, _ = parse_power_daily(payload, site=SITE)
    et0 = et0_penman_monteith(SITE, weather).values_mm

    profile = fetch_ssurgo_point(
        38.03, -100.74, cache_path="data/raw/ssurgo_finney_point.json"
    )
    soil = texture_from_ssurgo(profile, registry.texture)
    soc = soc_from_organic_carbon_pct(
        soil.organic_carbon_pct, soil.bulk_density_g_cm3
    )
    slope, _ = field_slope_percent(
        "data/raw/nasadem_n38w101/n38w101.hgt", 38.03, -100.74
    )
    ls = ls_factor(slope, 75.0)

    print(f"FieldShift rotation search - {SITE.name}")
    print(f"  soil {soil.source}, {soil.taw_mm_per_m:.0f} mm/m available water")
    print(f"  slope {slope:.2f}%, LS {ls:.3f}   starting carbon {soc:.1f} t C/ha")
    print(f"  weather: 41 years of NASA POWER, {weather.year[0]}-{weather.year[-1]}\n")

    # The farmer's existing rotation must appear whatever the search proposes.
    existing = build_rotation(
        [p for p in PATTERNS if p.id in ("wheat", "fallow")],
        name="EXISTING: winter wheat - fallow",
    )

    objectives = select_objectives(
        ["downside", "irrigation", "carbon", "wind_exposure", "bare_ground"]
    )
    constraints = RotationConstraints(
        max_consecutive_fallow_years=1,
        max_fallow_fraction=0.5,
        max_irrigated_years=2,
    )

    result = search_rotations(
        PATTERNS, registry=registry, soil=soil, weather=weather, et0_mm=et0,
        start_year=1984, initial_soc_t_ha=soc, ls=ls, cycle_years=3,
        constraints=constraints, objectives=objectives, max_candidates=120,
        extra_plans=[existing],
    )
    print(result.describe(limit=14))

    print("\n--- Best on each single axis ---")
    for o in objectives:
        best = result.best_for(o.key)
        print(f"  {o.label:28s} {o.extract(best):10.3f} {o.units:12s} {best.name[:40]}")

    print("\n--- What each front option costs relative to the existing rotation ---")
    for t in result.trade_offs_from("EXISTING: winter wheat - fallow")[:6]:
        print(f"  {t.describe()}")

    print("\n--- Ranked for a farmer who prioritises saving water ---")
    weights = {"downside": 1.0, "irrigation": 3.0, "carbon": 1.0,
               "wind_exposure": 1.0, "bare_ground": 0.5}
    ranking = weighted_ranking(result.scores, objectives, weights)
    for index, total in ranking[:5]:
        s = result.scores[index]
        print(f"  {total:.3f}  {s.name[:52]:52s} irrig {s.irrigation_mm_yr:5.0f} mm/yr")
    stability = weight_sensitivity(result.scores, objectives, weights)
    unstable = [k for k, ok in stability.items() if not ok]
    print(f"\n  Weight sensitivity: the top choice "
          f"{'survives every' if not unstable else 'FLIPS when'} "
          f"{'priority shift' if not unstable else ', '.join(unstable) + ' moves'}")
    print("  (a recommendation that flips on one priority is an artefact of the")
    print("   weighting, and the farmer should be told which priority decides it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

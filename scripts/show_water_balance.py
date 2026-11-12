"""Demonstrate the water balance on synthetic weather.

Shows the output the rotation comparison is built on, and in particular the cost of
a bare window - the number that is invisible to a single-coefficient model.

    PYTHONPATH=src ./.venv/bin/python scripts/show_water_balance.py
"""

from __future__ import annotations

import sys

from rdss.agro.climate import Site
from rdss.agro.et0 import et0_penman_monteith
from rdss.agro.synthetic import annual_rainfall_mm, synthetic_weather
from rdss.agro.waterbalance import IrrigationPolicy, run_water_balance
from rdss.data import load_registry

SITE = Site(17.4, 78.5, 500.0, "Deccan plateau (illustrative)")
CLIMATE = "semi_arid_monsoon"
TEXTURE = "clay_loam"
MONSOON_SOWING = 166  # about 15 June
IRRIGATED = IrrigationPolicy(mode="refill_on_stress", efficiency=0.65)


def main() -> int:
    reg = load_registry()
    weather = synthetic_weather(SITE, CLIMATE, start="2000-01-01", years=3, seed=11)
    demand = et0_penman_monteith(SITE, weather)
    texture = reg.texture(TEXTURE)

    rain = annual_rainfall_mm(weather)
    print(f"Site: {SITE.name}   soil: {texture.name} "
          f"({texture.taw_mm_per_m:.0f} mm/m available, group {texture.hydrologic_group})")
    print(f"Synthetic {CLIMATE}: rainfall {rain[2000]:.0f} mm in year 1")
    print(f"ET0: {demand.describe()}  [confidence: {demand.confidence}]\n")

    print("=== Monsoon-sown options, rainfed, same weather and soil ===")
    header = f"{'crop':20s} {'days':>5s} {'T':>6s} {'E':>6s} {'E/(E+T)':>8s} " \
             f"{'runoff':>7s} {'drain':>6s} {'deficit':>8s} {'stress':>7s}"
    print(header)
    print("-" * len(header))

    for crop_id in (
        "pearl_millet", "sorghum", "soybean", "maize", "cotton", "sugarcane"
    ):
        crop = reg.crop(crop_id)
        r = run_water_balance(
            crop, texture, weather, demand.values_mm, planting_index=MONSOON_SOWING
        )
        s = r.summary()
        print(
            f"{crop.name[:20]:20s} {crop.duration_days:5d} "
            f"{s['transpiration_mm']:6.0f} {s['soil_evaporation_mm']:6.0f} "
            f"{s['evaporation_fraction']:8.2f} {s['runoff_mm']:7.0f} "
            f"{s['deep_percolation_mm']:6.0f} {s['relative_transpiration_deficit']:8.2f} "
            f"{r.stress_days:4d}d/{r.max_consecutive_stress_days:<2d}"
        )

    print("\n  deficit = 1 - actual/potential transpiration over the season")
    print("  stress  = total stressed days / longest unbroken stressed spell")

    print("\n=== What a bare window costs, 120 days from monsoon onset ===")
    bare = run_water_balance(
        reg.crop("fallow_bare"), texture, weather, demand.values_mm,
        planting_index=MONSOON_SOWING, duration_days=120,
    )
    print(
        f"  bare fallow        : {bare.total_soil_evaporation_mm:5.0f} mm evaporated, "
        f"{bare.total_runoff_mm:5.0f} mm run off, 0 mm transpired"
    )
    for cover_id in ("sunn_hemp", "mungbean", "sorghum_sudan_cover"):
        cover = reg.crop(cover_id)
        r = run_water_balance(
            cover, texture, weather, demand.values_mm,
            planting_index=MONSOON_SOWING, duration_days=120,
        )
        saved_runoff = bare.total_runoff_mm - r.total_runoff_mm
        print(
            f"  {cover.name[:18]:18s} : {r.total_soil_evaporation_mm:5.0f} mm evaporated, "
            f"{r.total_runoff_mm:5.0f} mm run off, "
            f"{r.total_transpiration_mm:5.0f} mm transpired  "
            f"(-{saved_runoff:.0f} mm runoff vs bare)"
        )
    print(
        "\n  Water a cover crop uses is not water wasted: it is transpired through a "
        "canopy\n  that is also building residue and holding soil. But it IS water "
        "unavailable to\n  the next crop, which is why the rotation has to be "
        "simulated as a sequence."
    )

    print("\n=== Irrigation requirement at the field gate (65% efficiency) ===")
    for crop_id in ("cotton", "sugarcane", "pearl_millet"):
        crop = reg.crop(crop_id)
        r = run_water_balance(
            crop, texture, weather, demand.values_mm,
            planting_index=MONSOON_SOWING, irrigation=IRRIGATED,
        )
        print(
            f"  {crop.name[:20]:20s} {r.total_irrigation_gross_mm:6.0f} mm "
            f"({r.total_irrigation_gross_mm * 10:.0f} m3/ha)"
        )
    print(
        "\n  Synthetic weather, illustrative soil, no local calibration. "
        "Not decision-ready."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

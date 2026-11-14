"""Forty years of real soil carbon trajectories for competing rotations.

Everything here comes from measured or observed data: NASA POWER weather 1984-2024,
SSURGO soil properties at the field, and the starting carbon stock from SSURGO's own
organic matter measurement. Only the rotations themselves are hypothetical.

    PYTHONPATH=src ./.venv/bin/python scripts/show_carbon.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import numpy as np

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.rothc import (
    initialise_from_measured_soc,
    run_rothc,
    soc_from_organic_carbon_pct,
)
from fieldshift.data import load_registry
from fieldshift.io.power import parse_power_daily
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")


@dataclass(frozen=True)
class Rotation:
    name: str
    annual_carbon_input_t_ha: float
    covered_months: tuple[int, ...]
    annual_irrigation_mm: float
    note: str


# Carbon inputs are residue dry matter x 0.45 carbon fraction, using the
# product-to-residue ratios and root:shoot values already in crops.json.
ROTATIONS = (
    Rotation("continuous maize, residue baled", 1.2, (5, 6, 7, 8, 9), 660,
             "stover sold; only roots returned"),
    Rotation("continuous maize, residue retained", 3.4, (5, 6, 7, 8, 9), 660,
             "the common irrigated baseline"),
    Rotation("maize - winter wheat - summer fallow", 2.4, tuple(range(1, 13)), 420,
             "less water, but a bare summer"),
    Rotation("maize - winter wheat, no fallow", 3.1, tuple(range(1, 13)), 480,
             "the fallow replaced by a crop"),
    Rotation("maize - wheat + cereal rye cover", 4.3, tuple(range(1, 13)), 520,
             "cover crop closes every window"),
    Rotation("dryland sorghum - wheat - fallow", 1.9, tuple(range(1, 13)), 0,
             "no pumping at all"),
)


def main() -> int:
    reg = load_registry()

    payload = json.loads(
        open("data/raw/power_finney_ks_1981_2024.json").read()
    )
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    weather, _ = parse_power_daily(payload, site=SITE)
    et0_daily = et0_penman_monteith(SITE, weather).values_mm

    profile = fetch_ssurgo_point(
        SITE.latitude_deg, SITE.longitude_deg,
        cache_path="data/raw/ssurgo_finney_point.json",
    )
    soil = texture_from_ssurgo(profile, reg.texture)
    soc0 = soc_from_organic_carbon_pct(
        soil.organic_carbon_pct, soil.bulk_density_g_cm3
    )

    monthly = weather.monthly().complete()
    keys = weather.dates.astype("datetime64[M]")
    et0_monthly = np.array([et0_daily[keys == mk].sum() for mk in monthly.months])
    n = len(monthly)
    years = n / 12.0

    clim = {
        "temp": np.array([monthly.tmean_c[monthly.month_number == k].mean()
                          for k in range(1, 13)]),
        "rain": np.array([monthly.precip_mm[monthly.month_number == k].mean()
                          for k in range(1, 13)]),
        "et0": np.array([et0_monthly[monthly.month_number == k].mean()
                         for k in range(1, 13)]),
    }

    print(f"Site: {SITE.name}   soil: {soil.source}")
    print(f"  surface clay {soil.clay_pct:.0f}%, bulk density "
          f"{soil.bulk_density_g_cm3:.2f} g/cm3, organic C "
          f"{soil.organic_carbon_pct:.2f}%")
    print(f"  measured starting stock (0-23 cm): {soc0:.1f} t C/ha")
    print(f"  weather: NASA POWER {monthly.year[0]}-{monthly.year[-1]} "
          f"({years:.0f} years, {n} months)\n")

    # One shared starting state: the same measured soil under its historic rotation.
    baseline = ROTATIONS[1]
    cov12 = np.array([k in baseline.covered_months for k in range(1, 13)])
    pools, implied_input = initialise_from_measured_soc(
        soc0, clay_pct=soil.clay_pct, temp_c=clim["temp"], rain_mm=clim["rain"],
        et0_mm=clim["et0"], covered=cov12,
    )
    print(f"  Inverse RothC puts the historic carbon input at "
          f"{implied_input:.1f} t C/ha/yr -")
    print("  the rate that holds SOC where SSURGO measured it. Rotations returning")
    print("  more than that gain carbon; less, and they lose it.\n")

    header = (f"{'rotation':38s} {'C in':>5s} {'irrig':>6s} {'bare':>5s} "
              f"{'SOC end':>8s} {'change':>8s} {'t/ha/yr':>8s}")
    print(header)
    print("-" * len(header))

    results = []
    for rot in ROTATIONS:
        covered = np.array([k in rot.covered_months for k in monthly.month_number])
        r = run_rothc(
            pools, clay_pct=soil.clay_pct, temp_c=monthly.tmean_c,
            rain_mm=monthly.precip_mm, et0_mm=et0_monthly, covered=covered,
            carbon_input_t_ha=np.full(n, rot.annual_carbon_input_t_ha / 12.0),
            irrigation_mm=np.full(n, rot.annual_irrigation_mm / 12.0),
        )
        assert abs(r.closure_residual_t_ha) < 1e-8, "carbon balance did not close"
        results.append((rot, r))
        print(f"{rot.name:38s} {rot.annual_carbon_input_t_ha:5.1f} "
              f"{rot.annual_irrigation_mm:5.0f}m {12 - len(rot.covered_months):4d}m "
              f"{r.final_soc:8.1f} {r.change_t_ha:+8.1f} "
              f"{r.annual_change_t_ha(years):+8.2f}")

    best = max(results, key=lambda x: x[1].change_t_ha)
    worst = min(results, key=lambda x: x[1].change_t_ha)
    print(f"\n  Spread over {years:.0f} years: "
          f"{best[1].change_t_ha - worst[1].change_t_ha:.1f} t C/ha between "
          f"'{best[0].name}'\n  and '{worst[0].name}'.")

    print("\n  Why these differ, in the model's own terms:")
    print("   - carbon returned: the dominant term, and the one residue management sets")
    print("   - months under cover: a cropped soil decomposes at 0.6 of the bare rate,")
    print("     so a bare window costs carbon before any erosion is counted")
    print("   - irrigation: applied water keeps the soil biologically active, so an")
    print("     irrigated rotation turns carbon over faster than its rainfall implies")
    print(f"   - clay {soil.clay_pct:.0f}%: at this clay content "
          f"{(1 - 0.789) * 100:.0f}% of decomposed carbon is retained rather than")
    print("     respired, so the same residue would build less carbon on a sandier soil")

    print("\n  Caveats a farmer should hear: carbon inputs are estimated from residue")
    print("  ratios, not measured on this field; RothC describes 0-23 cm only; and a")
    print("  40-year projection assumes the next 40 years resemble the last 40, which")
    print("  the temperature trend in this same record already contradicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

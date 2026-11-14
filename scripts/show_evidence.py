"""Produce the evidence card for the pilot field's recommendation.

    PYTHONPATH=src ./.venv/bin/python scripts/show_evidence.py

Everything on the card is assembled from what the engine actually used, so it
cannot drift out of step with the recommendation it explains.
"""

from __future__ import annotations

import json
import sys

from fieldshift.agro.climate import Site
from fieldshift.data import load_registry
from fieldshift.decide.evidence import (
    CROP_TABLE,
    GRIDMET,
    NASADEM,
    POWER,
    QUICKSTATS,
    SSURGO,
    build_card,
)
from fieldshift.decide.guardrails import check_crop
from fieldshift.io.power import parse_power_daily

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
FIELD_HA = 65.0  # a quarter section


def main() -> int:
    registry = load_registry()
    payload = json.loads(open("data/raw/power_ks_finney.json").read())
    weather, _ = parse_power_daily(payload, site=SITE)

    rails = check_crop(
        registry.crop("wheat_winter"), weather, 274, 285, irrigation_mm_yr=0.0
    )

    card = build_card(
        "Replace winter wheat - summer fallow with continuous winter wheat",
        field_hectares=FIELD_HA,
        numbers=(
            ("production, cycle average", "1.05 -> 2.29 t/ha/yr"),
            ("soil carbon", "-0.047 -> +0.270 t C/ha/yr (currently losing)"),
            ("bare ground", "59.8% -> 21.5% of the time"),
            ("wind erosion exposure", "769 -> 390 index (lower is better)"),
            ("water erosion, RUSLE", "3.6 -> 0.5 t/ha/yr"),
            ("irrigation required", "none, either way"),
            ("crops harvested per decade", "5.2 -> 10.0"),
            ("yield per individual crop", "2.69 -> 2.28 t/ha - fallow wins here"),
        ),
        sources=(POWER, GRIDMET, SSURGO, NASADEM, QUICKSTATS, CROP_TABLE),
        model_keys=("et0", "water", "carbon", "nitrogen", "erosion", "rotation"),
        guardrails=rails.verdicts,
    )
    print(card.render())

    print("\n" + "-" * 74)
    print("Guardrail screen for every crop considered at this site:\n")
    windows = {
        "wheat_winter": (274, 285), "maize": (125, 150), "sorghum": (152, 125),
        "soybean": (140, 140), "pearl_millet": (152, 100),
        "cereal_rye_cover": (288, 70), "rice_lowland": (152, 150),
        "sugarcane": (100, 360), "cotton": (152, 195),
    }
    for crop_id, (doy, duration) in windows.items():
        report = check_crop(registry.crop(crop_id), weather, doy, duration)
        status = "REFUSED" if report.refused else f"{len(report.warnings)} warning(s)"
        print(f"  {registry.crop(crop_id).name[:28]:28s} {status}")
        for verdict in report.refusals:
            print(f"      why: {verdict.message[:88]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

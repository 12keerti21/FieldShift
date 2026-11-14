"""Does the headline claim survive parameter uncertainty, or is it an artefact?

FieldShift says the pilot farmer's winter wheat - summer fallow rotation is beaten
on every objective. That rests on roughly forty parameters per crop, many tagged
medium or low confidence, so the claim is worth only as much as its robustness.

This is a one-at-a-time sweep: each parameter is moved to the edge of its
plausible range, the key rotations are re-simulated, and two things are checked -
whether continuous wheat still beats wheat-fallow on every objective, and how far
the observed fallow advantage (+10.8 percent, USDA NASS) moves.

One-at-a-time cannot find interactions, which is its known limitation and is stated
rather than glossed. What it does give is a ranking of which parameters the answer
actually depends on - which is the list an agronomist should scrutinise first.

    PYTHONPATH=src ./.venv/bin/python scripts/sensitivity.py
"""

from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass
from typing import Callable

import numpy as np

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.rotation import RotationPlan, SeasonPlan, simulate_rotation
from fieldshift.agro.rothc import soc_from_organic_carbon_pct
from fieldshift.data import load_registry
from fieldshift.io.power import parse_power_daily
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
OCT1, N_RATE = 274, 70.0
OBSERVED_FALLOW_ADVANTAGE = 0.108


@dataclass
class Case:
    """One parameter moved to the edge of its plausible range."""

    name: str
    confidence: str
    apply: Callable
    note: str


def _crop(reg, crop_id, **changes):
    """Return a registry copy with one crop field replaced."""
    crop = reg.crop(crop_id)
    for path, value in changes.items():
        head, _, tail = path.partition(".")
        if tail:
            inner = dataclasses.replace(getattr(crop, head), **{tail: value})
            crop = dataclasses.replace(crop, **{head: inner})
        else:
            crop = dataclasses.replace(crop, **{head: value})
    return dataclasses.replace(reg, crops={**reg.crops, crop_id: crop})


def build_cases(reg, soil) -> list[Case]:
    w = reg.crop("wheat_winter")
    return [
        Case("soil available water -20%", "medium",
             lambda r, s: (r, dataclasses.replace(
                 s, measured_awc_mm_per_m=s.taw_mm_per_m * 0.8)),
             "SSURGO awc_r is a representative value with real spread"),
        Case("soil available water +20%", "medium",
             lambda r, s: (r, dataclasses.replace(
                 s, measured_awc_mm_per_m=s.taw_mm_per_m * 1.2)), ""),
        Case("clay -30%", "high",
             lambda r, s: (r, dataclasses.replace(s, clay_pct=s.clay_pct * 0.7)),
             "drives the RothC CO2 partition and the RUSLE K factor"),
        Case("clay +30%", "high",
             lambda r, s: (r, dataclasses.replace(s, clay_pct=s.clay_pct * 1.3)), ""),
        Case("wheat Kcb_mid -10%", "high",
             lambda r, s: (_crop(r, "wheat_winter", **{"kcb.mid": w.kcb.mid * 0.9}), s),
             "FAO-56 Table 17 values carry about this much spread"),
        Case("wheat Kcb_mid +10%", "high",
             lambda r, s: (_crop(r, "wheat_winter", **{"kcb.mid": w.kcb.mid * 1.1}), s),
             ""),
        Case("wheat p_depletion 0.40", "high",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"root.p_depletion": 0.40}), s),
             "FAO-56 Table 22 range for wheat is roughly 0.40-0.65"),
        Case("wheat p_depletion 0.65", "high",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"root.p_depletion": 0.65}), s), ""),
        Case("wheat rooting depth 1.2 m", "high",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"root.max_depth_m": 1.2}), s),
             "the single most leveraged soil-water parameter"),
        Case("wheat rooting depth 2.2 m", "high",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"root.max_depth_m": 2.2}), s), ""),
        Case("wheat Ky 0.85", "high",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"ky": dataclasses.replace(w.ky, value=0.85)}), s),
             "FAO-33 gives 1.05; cereals span roughly 0.85-1.15"),
        Case("wheat Ky 1.25", "high",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"ky": dataclasses.replace(w.ky, value=1.25)}), s),
             ""),
        Case("wheat straw ratio -25%", "medium",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"residue.product_to_residue_ratio":
                                    w.residue.product_to_residue_ratio * 0.75}), s),
             "sets the carbon and residue-cover return"),
        Case("wheat straw ratio +25%", "medium",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"residue.product_to_residue_ratio":
                                    w.residue.product_to_residue_ratio * 1.25}), s),
             ""),
        Case("wheat straw C:N 70", "medium",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"residue.cn_ratio": 70.0}), s),
             "controls how hard straw immobilises nitrogen"),
        Case("wheat straw C:N 110", "medium",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"residue.cn_ratio": 110.0}), s), ""),
        Case("attainable yield -20%", "medium",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"economics": dataclasses.replace(
                                     w.economics,
                                     yield_t_ha=(w.economics.yield_t_ha[0],
                                                 w.economics.yield_t_ha[1],
                                                 w.economics.yield_t_ha[2] * 0.8))}), s),
             "FAO-33 Ym; sets the ceiling every yield is scaled from"),
        Case("attainable yield +20%", "medium",
             lambda r, s: (_crop(r, "wheat_winter",
                                 **{"economics": dataclasses.replace(
                                     w.economics,
                                     yield_t_ha=(w.economics.yield_t_ha[0],
                                                 w.economics.yield_t_ha[1],
                                                 w.economics.yield_t_ha[2] * 1.2))}), s),
             ""),
    ]


RUN_ARGS = dict(
    initial_root_depletion_frac=0.3, initial_surface_depletion_frac=0.7,
)


def run_pair(reg, soil, weather, et0, soc, fertiliser=N_RATE, subsoil=0.5,
             residue_cover_hint=None):
    """Mean wheat yield under fallow and under continuous cropping."""
    out = {}
    for label, cycle in (("fallow", 2), ("continuous", 1)):
        plan = RotationPlan(
            label, (SeasonPlan("wheat_winter", OCT1, 285,
                               fertiliser_n_kg_ha=fertiliser),), cycle
        )
        r = simulate_rotation(
            plan, registry=reg, soil=soil, weather=weather, et0_mm=et0,
            start_year=1984, cycles=39 // cycle, initial_soc_t_ha=soc,
            **RUN_ARGS,
        )
        out[label] = dict(
            yield_t_ha=r.mean_yield("wheat_winter"),
            production=sum(v.sum() for v in r.yields_by_crop().values())
            / (r.total_days / 365.25),
            carbon=r.carbon_change_per_year(r.total_days / 365.25),
            bare=r.bare_fraction,
        )
    return out


def main() -> int:
    reg = load_registry()
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
    soil = texture_from_ssurgo(profile, reg.texture)
    soc = soc_from_organic_carbon_pct(
        soil.organic_carbon_pct, soil.bulk_density_g_cm3
    )

    base = run_pair(reg, soil, weather, et0, soc)
    base_adv = base["fallow"]["yield_t_ha"] / base["continuous"]["yield_t_ha"] - 1.0
    base_dominates = (
        base["continuous"]["production"] > base["fallow"]["production"]
        and base["continuous"]["carbon"] > base["fallow"]["carbon"]
        and base["continuous"]["bare"] < base["fallow"]["bare"]
    )

    print("SENSITIVITY OF THE HEADLINE CLAIM")
    print("=" * 76)
    print("Claim: continuous wheat beats wheat-fallow on production, carbon and bare")
    print("ground simultaneously. Baseline: " 
          f"{'HOLDS' if base_dominates else 'FAILS'}")
    print(f"Fallow yield advantage, baseline {base_adv:+.1%} "
          f"(observed {OBSERVED_FALLOW_ADVANTAGE:+.1%})\n")

    print(f"{'parameter moved':30s} {'conf':>7s} {'advantage':>10s} {'vs obs':>8s} "
          f"{'claim':>7s}")
    print("-" * 76)
    results = []
    for case in build_cases(reg, soil):
        try:
            r2, s2 = case.apply(reg, soil)
            out = run_pair(r2, s2, weather, et0, soc)
        except Exception as exc:
            print(f"{case.name:30s} {case.confidence:>7s} "
                  f"{'FAILED':>10s}  {type(exc).__name__}")
            continue
        adv = out["fallow"]["yield_t_ha"] / out["continuous"]["yield_t_ha"] - 1.0
        holds = (
            out["continuous"]["production"] > out["fallow"]["production"]
            and out["continuous"]["carbon"] > out["fallow"]["carbon"]
            and out["continuous"]["bare"] < out["fallow"]["bare"]
        )
        results.append((case, adv, holds))
        print(f"{case.name:30s} {case.confidence:>7s} {adv:+10.1%} "
              f"{adv - OBSERVED_FALLOW_ADVANTAGE:+8.1%} "
              f"{'holds' if holds else 'BREAKS':>7s}")

    print("-" * 76)
    broke = [c for c, _, h in results if not h]
    advs = np.array([a for _, a, _ in results])
    print(f"\n  the claim holds under {len(results) - len(broke)}/{len(results)} "
          f"perturbations")
    if broke:
        print("  BREAKS under: " + "; ".join(c.name for c in broke))
    print(f"  fallow advantage ranges {advs.min():+.1%} to {advs.max():+.1%} "
          f"(observed {OBSERVED_FALLOW_ADVANTAGE:+.1%})")

    print("\n  Most leveraged parameters, by how far they move the advantage:")
    ranked = sorted(results, key=lambda x: -abs(x[1] - base_adv))
    for case, adv, _ in ranked[:6]:
        print(f"    {abs(adv - base_adv):6.1%}  {case.name:30s} [{case.confidence}]"
              + (f"  {case.note}" if case.note else ""))

    print("\n  Limitation: this is one-at-a-time, so it cannot find interactions.")
    print("  It ranks what the answer depends on, which is the list to check first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

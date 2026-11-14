"""Is the headline claim robust to parameter uncertainty?

FieldShift says the pilot farmer's winter wheat - summer fallow rotation is beaten
on every objective. That rests on roughly forty parameters per crop, many tagged
medium or low confidence, so the claim is worth only as much as its robustness.

These tests move the most leveraged parameters to the edges of their plausible
ranges and assert the conclusion survives. They also pin a diagnostic that came out
of the full sweep: the fallow advantage never falls to the observed +10.8 percent
under ANY single-parameter perturbation, which rules out parameter error as the
explanation for the over-prediction and leaves the structural cause - a modelled
fallow is perfectly bare, a real one loses water to weeds.

Full sweep: scripts/sensitivity.py
"""

import dataclasses
import json
import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.rotation import RotationPlan, SeasonPlan, simulate_rotation
from fieldshift.agro.rothc import soc_from_organic_carbon_pct
from fieldshift.io.power import parse_power_daily
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
OCT1, N_RATE = 274, 70.0
OBSERVED_ADVANTAGE = 0.108

pytestmark = pytest.mark.skipif(
    not (DATA / "power_finney_ks_1981_2024.json").exists(),
    reason="cached pilot data not present",
)


@pytest.fixture(scope="module")
def pilot(reg):
    payload = json.loads((DATA / "power_finney_ks_1981_2024.json").read_text())
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    weather, _ = parse_power_daily(payload, site=SITE)
    et0 = et0_penman_monteith(SITE, weather).values_mm
    profile = fetch_ssurgo_point(
        38.03, -100.74, cache_path=str(DATA / "ssurgo_finney_point.json")
    )
    soil = texture_from_ssurgo(profile, reg.texture)
    soc = soc_from_organic_carbon_pct(
        soil.organic_carbon_pct, soil.bulk_density_g_cm3
    )
    return weather, et0, soil, soc


def _pair(reg, soil, pilot_data):
    weather, et0, _, soc = pilot_data
    out = {}
    for label, cycle in (("fallow", 2), ("continuous", 1)):
        plan = RotationPlan(
            label,
            (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=N_RATE),),
            cycle,
        )
        r = simulate_rotation(
            plan, registry=reg, soil=soil, weather=weather, et0_mm=et0,
            start_year=1984, cycles=39 // cycle, initial_soc_t_ha=soc,
            initial_root_depletion_frac=0.3, initial_surface_depletion_frac=0.7,
        )
        years = r.total_days / 365.25
        out[label] = dict(
            yield_t_ha=r.mean_yield("wheat_winter"),
            production=sum(v.sum() for v in r.yields_by_crop().values()) / years,
            carbon=r.carbon_change_per_year(years),
            bare=r.bare_fraction,
        )
    return out


def _claim_holds(pair):
    return (
        pair["continuous"]["production"] > pair["fallow"]["production"]
        and pair["continuous"]["carbon"] > pair["fallow"]["carbon"]
        and pair["continuous"]["bare"] < pair["fallow"]["bare"]
    )


def _advantage(pair):
    return pair["fallow"]["yield_t_ha"] / pair["continuous"]["yield_t_ha"] - 1.0


def _with_crop(reg, crop_id, path, value):
    crop = reg.crop(crop_id)
    head, _, tail = path.partition(".")
    if tail:
        inner = dataclasses.replace(getattr(crop, head), **{tail: value})
        crop = dataclasses.replace(crop, **{head: inner})
    else:
        crop = dataclasses.replace(crop, **{head: value})
    return dataclasses.replace(reg, crops={**reg.crops, crop_id: crop})


# --- the baseline -----------------------------------------------------------------


def test_the_claim_holds_at_the_baseline(reg, pilot):
    weather, et0, soil, soc = pilot
    assert _claim_holds(_pair(reg, soil, pilot))


def test_the_baseline_over_predicts_the_fallow_advantage(reg, pilot):
    _, _, soil, _ = pilot
    advantage = _advantage(_pair(reg, soil, pilot))
    assert advantage > OBSERVED_ADVANTAGE, "we over-predict, and should say so"
    assert advantage < 0.30


# --- the most leveraged parameters ------------------------------------------------


@pytest.mark.parametrize(
    "crop_id,path,value,label",
    [
        ("wheat_winter", "root.p_depletion", 0.40, "p_depletion low"),
        ("wheat_winter", "root.p_depletion", 0.65, "p_depletion high"),
        ("wheat_winter", "root.max_depth_m", 1.2, "shallow roots"),
        ("wheat_winter", "root.max_depth_m", 2.2, "deep roots"),
        ("wheat_winter", "residue.cn_ratio", 70.0, "straw C:N low"),
        ("wheat_winter", "residue.cn_ratio", 110.0, "straw C:N high"),
    ],
)
def test_the_claim_survives_crop_parameter_perturbation(
    reg, pilot, crop_id, path, value, label
):
    _, _, soil, _ = pilot
    perturbed = _with_crop(reg, crop_id, path, value)
    assert _claim_holds(_pair(perturbed, soil, pilot)), label


@pytest.mark.parametrize("ky", [0.85, 1.25])
def test_the_claim_survives_the_most_leveraged_parameter(reg, pilot, ky):
    """Ky moved the advantage most of anything in the full sweep."""
    _, _, soil, _ = pilot
    crop = reg.crop("wheat_winter")
    perturbed = _with_crop(
        reg, "wheat_winter", "ky", dataclasses.replace(crop.ky, value=ky)
    )
    pair = _pair(perturbed, soil, pilot)
    assert _claim_holds(pair)
    assert _advantage(pair) > OBSERVED_ADVANTAGE


@pytest.mark.parametrize("factor", [0.8, 1.2])
def test_the_claim_survives_soil_water_uncertainty(reg, pilot, factor):
    """SSURGO's awc_r is a representative value with genuine spread."""
    _, _, soil, _ = pilot
    perturbed_soil = dataclasses.replace(
        soil, measured_awc_mm_per_m=soil.taw_mm_per_m * factor
    )
    assert _claim_holds(_pair(reg, perturbed_soil, pilot))


@pytest.mark.parametrize("factor", [0.7, 1.3])
def test_the_claim_survives_clay_uncertainty(reg, pilot, factor):
    """Clay drives both the RothC carbon partition and RUSLE erodibility."""
    _, _, soil, _ = pilot
    perturbed_soil = dataclasses.replace(soil, clay_pct=soil.clay_pct * factor)
    assert _claim_holds(_pair(reg, perturbed_soil, pilot))


# --- the diagnostic the sweep produced -------------------------------------------


def test_the_over_prediction_is_structural_not_parametric(reg, pilot):
    """No single-parameter move brings the advantage down to the observed value.

    That rules out parameter error as the explanation and leaves the structural
    cause: a modelled summer fallow is perfectly bare, while a real one loses water
    to weeds and imperfect control, so it banks less than the model assumes. The
    right response is to name the mechanism, not to tune a parameter until the
    number matches.
    """
    _, _, soil, _ = pilot
    crop = reg.crop("wheat_winter")
    advantages = [_advantage(_pair(reg, soil, pilot))]
    for path, value in (
        ("root.p_depletion", 0.40),
        ("root.max_depth_m", 2.2),
        ("kcb.mid", crop.kcb.mid * 1.1),
        ("residue.cn_ratio", 70.0),
    ):
        advantages.append(
            _advantage(_pair(_with_crop(reg, "wheat_winter", path, value), soil, pilot))
        )
    for factor in (0.8, 1.2):
        advantages.append(
            _advantage(
                _pair(
                    reg,
                    dataclasses.replace(
                        soil, measured_awc_mm_per_m=soil.taw_mm_per_m * factor
                    ),
                    pilot,
                )
            )
        )
    advantages = np.array(advantages)
    assert advantages.min() > OBSERVED_ADVANTAGE, (
        f"min advantage {advantages.min():+.1%} should still exceed the observed "
        f"{OBSERVED_ADVANTAGE:+.1%} - the bias is one-directional"
    )
    assert advantages.max() < 0.35

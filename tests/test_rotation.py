"""Rotation simulation: chaining seasons so soil state carries between crops.

The headline test here is `test_fallow_advantage_matches_usda_observation`. USDA
NASS reports dryland wheat yield separately for crops following summer fallow
(35.4 bu/ac) and continuous cropping (32.0 bu/ac) in Finney County - an observed
rotation effect of +10.8% over 25 years. Nothing in this model was fitted to that
number; the advantage has to emerge from soil water carried across the fallow year.
If the chaining is broken, it cannot appear at all.
"""

import json
import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.rotation import (
    C4_CROPS,
    RESIDUE_DECAY_PER_MONTH,
    RotationError,
    RotationPlan,
    SeasonPlan,
    above_ground_dry_matter,
    carbon_input_from_crop,
    decay_residue,
    residue_cover,
    simulate_rotation,
)
from fieldshift.agro.rothc import soc_from_organic_carbon_pct
from fieldshift.agro.waterbalance import IrrigationPolicy
from fieldshift.io.power import parse_power_daily
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
OCT1 = 274

pytestmark = pytest.mark.skipif(
    not (DATA / "power_finney_ks_1981_2024.json").exists(),
    reason="cached POWER record not present; run scripts/validate_et0.py first",
)


@pytest.fixture(scope="module")
def forcing():
    payload = json.loads((DATA / "power_finney_ks_1981_2024.json").read_text())
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    weather, _ = parse_power_daily(payload, site=SITE)
    return weather, et0_penman_monteith(SITE, weather).values_mm


@pytest.fixture(scope="module")
def site_soil(reg):
    profile = fetch_ssurgo_point(
        38.03, -100.74, cache_path=str(DATA / "ssurgo_finney_point.json")
    )
    soil = texture_from_ssurgo(profile, reg.texture)
    soc = soc_from_organic_carbon_pct(soil.organic_carbon_pct, soil.bulk_density_g_cm3)
    return soil, soc


def _run(plan, reg, forcing, site_soil, cycles=None):
    weather, et0 = forcing
    soil, soc = site_soil
    return simulate_rotation(
        plan, registry=reg, soil=soil, weather=weather, et0_mm=et0,
        start_year=1984, cycles=cycles or (39 // plan.cycle_years),
        initial_soc_t_ha=soc,
    )


# Dryland winter wheat in western Kansas receives roughly 40-70 kg N/ha. Running
# the model at zero fertiliser makes nitrogen the binding constraint in every
# season, collapses yields to 0.43 t/ha against an observed 2.15, and inflates the
# fallow advantage to +122 percent - because a fallow year's extra mineralisation
# matters enormously to a starved crop and very little to a fed one. Fertiliser is
# a management input and has to be stated.
N_RATE = 70.0

WHEAT_FALLOW = RotationPlan(
    "winter wheat - summer fallow",
    (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=N_RATE),), 2,
)
CONTINUOUS = RotationPlan(
    "continuous winter wheat",
    (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=N_RATE),), 1,
)


# --- THE validation test ---------------------------------------------------------


def test_fallow_advantage_matches_usda_observation(reg, forcing, site_soil):
    """Observed +10.8%; nothing here was fitted to it.

    The mechanism must be soil water banked through the fallow year and carried into
    the next wheat crop. A simulator that scored crops independently would show no
    difference at all between these two rotations.
    """
    fallow = _run(WHEAT_FALLOW, reg, forcing, site_soil)
    continuous = _run(CONTINUOUS, reg, forcing, site_soil)

    a = fallow.yields_by_crop()["wheat_winter"].mean()
    b = continuous.yields_by_crop()["wheat_winter"].mean()
    advantage = a / b - 1.0

    assert advantage > 0.0, "fallow must out-yield continuous cropping"
    # Observed 10.8%. We allow 5-25% and record that we currently over-predict:
    # a modelled fallow is perfectly bare, while a real one loses water to weeds
    # and imperfect control, so it banks less than the model assumes.
    assert 0.05 < advantage < 0.25, f"modelled advantage {advantage:.1%}"


def test_the_fallow_tradeoff_appears_in_full(reg, forcing, site_soil):
    """Fallow wins per crop and loses on everything else - the real trade-off."""
    fallow = _run(WHEAT_FALLOW, reg, forcing, site_soil)
    continuous = _run(CONTINUOUS, reg, forcing, site_soil)

    # Better individual crops...
    assert (fallow.yields_by_crop()["wheat_winter"].mean()
            > continuous.yields_by_crop()["wheat_winter"].mean())
    # ...but roughly half as many of them
    assert len(fallow.yields_by_crop()["wheat_winter"]) < 0.6 * len(
        continuous.yields_by_crop()["wheat_winter"]
    )
    # ...far more bare ground
    assert fallow.bare_fraction > 2 * continuous.bare_fraction
    # ...less carbon built
    assert fallow.carbon_change_t_ha < continuous.carbon_change_t_ha
    # ...and more of its water lost to evaporation rather than transpired
    assert fallow.evaporation_fraction > continuous.evaporation_fraction


def test_the_bad_year_is_reported_not_just_the_mean(reg, forcing, site_soil):
    r = _run(WHEAT_FALLOW, reg, forcing, site_soil)
    p10 = r.yield_percentile("wheat_winter", 10)
    assert p10 < r.mean_yield("wheat_winter")
    assert p10 > 0.0


# --- state actually carries between seasons --------------------------------------


def test_bare_gaps_between_crops_are_simulated_not_ignored(reg, forcing, site_soil):
    """A gap must cost evaporation and runoff, and conserve some water too."""
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    fallows = [s for s in r.seasons if s.is_fallow]
    assert fallows, "the July-October gap must be filled with an explicit fallow"
    assert all(s.water.total_soil_evaporation_mm > 0 for s in fallows)
    assert all(s.relative_yield is None for s in fallows)
    assert r.bare_days == sum(s.duration_days for s in fallows)


def test_carbon_runs_once_across_the_whole_sequence(reg, forcing, site_soil):
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    assert abs(r.carbon.closure_residual_t_ha) < 1e-8
    assert len(r.carbon) > 400  # monthly over ~39 years
    assert r.final_state.carbon.total == pytest.approx(r.carbon.final_soc)


def test_residue_cover_at_sowing_comes_from_the_previous_crop(reg, forcing, site_soil):
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    crops = [s for s in r.seasons if not s.is_fallow]
    assert crops[0].residue_cover_at_sowing >= 0.0
    # Later crops inherit straw from their predecessors, so cover should be non-zero.
    assert max(s.residue_cover_at_sowing for s in crops[1:]) > 0.1


def test_removing_residue_reduces_carbon_input_and_cover(reg, forcing, site_soil):
    baled = RotationPlan(
        "continuous wheat, straw baled",
        (SeasonPlan("wheat_winter", OCT1, 285, residue_removed_fraction=0.85,
                    fertiliser_n_kg_ha=N_RATE),), 1,
    )
    retained = _run(CONTINUOUS, reg, forcing, site_soil)
    removed = _run(baled, reg, forcing, site_soil)
    assert removed.carbon_change_t_ha < retained.carbon_change_t_ha
    assert sum(s.carbon_input_t_ha for s in removed.seasons) < sum(
        s.carbon_input_t_ha for s in retained.seasons
    )


def test_irrigation_policy_is_honoured_per_season(reg, forcing, site_soil):
    irrigated = RotationPlan(
        "irrigated continuous wheat",
        (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=N_RATE,
                    irrigation=IrrigationPolicy("refill_on_stress", efficiency=0.85)),), 1,
    )
    dry = _run(CONTINUOUS, reg, forcing, site_soil)
    wet = _run(irrigated, reg, forcing, site_soil)
    assert dry.total_irrigation_gross_mm == 0.0
    assert wet.total_irrigation_gross_mm > 0.0
    assert wet.mean_yield("wheat_winter") > dry.mean_yield("wheat_winter")


def test_multi_season_rotation_runs_and_reports_each_crop(reg, forcing, site_soil):
    """The real High Plains three-year sequence.

    Sorghum sits at year_offset 2, not 1: wheat sown in October of year 0 is not
    harvested until July of year 1, so a June planting in year 1 would overlap it.
    The overlap guard caught exactly that mistake when this test was first written.
    """
    plan = RotationPlan(
        "wheat - fallow - sorghum",
        (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=N_RATE),
         SeasonPlan("sorghum", 152, 125, year_offset=2, fertiliser_n_kg_ha=50.0)),
        cycle_years=3,
    )
    r = _run(plan, reg, forcing, site_soil, cycles=12)
    by_crop = r.yields_by_crop()
    assert {"wheat_winter", "sorghum"} <= set(by_crop)
    assert all(len(v) > 5 for v in by_crop.values())
    assert abs(r.carbon.closure_residual_t_ha) < 1e-8


# --- residue and carbon helpers ---------------------------------------------------


def test_residue_cover_saturates():
    assert residue_cover(0.0) == 0.0
    assert 0.3 < residue_cover(1.0) < 0.42   # ~36% at 1 t/ha
    assert 0.7 < residue_cover(3.0) < 0.8    # ~74% at 3 t/ha
    assert residue_cover(20.0) < 1.0
    masses = np.linspace(0, 10, 20)
    covers = [residue_cover(m) for m in masses]
    assert all(a <= b for a, b in zip(covers, covers[1:]))


def test_residue_decays_faster_for_legumes_than_cereal_straw():
    assert RESIDUE_DECAY_PER_MONTH["fast"] > RESIDUE_DECAY_PER_MONTH["slow"]
    legume = decay_residue(3.0, "fast", 6.0)
    straw = decay_residue(3.0, "slow", 6.0)
    assert legume < straw < 3.0
    assert decay_residue(3.0, "slow", 0.0) == pytest.approx(3.0)


def test_baling_reduces_carbon_input_but_never_to_zero(reg):
    """Roots are returned whatever happens to the straw."""
    wheat = reg.crop("wheat_winter")
    dm = above_ground_dry_matter(wheat, 2.6, 400.0)
    kept, straw_kept = carbon_input_from_crop(wheat, dm, 0.0)
    baled, straw_baled = carbon_input_from_crop(wheat, dm, 1.0)
    assert 0.0 < baled < kept
    assert straw_baled == pytest.approx(0.0)
    assert straw_kept > 0.0


def test_green_manure_biomass_comes_from_transpiration_not_yield(reg):
    """A cover crop has no harvest, so residue cannot be derived from one."""
    hemp = reg.crop("sunn_hemp")
    assert hemp.economics.yield_typical == 0.0
    dm = above_ground_dry_matter(hemp, None, 300.0)
    assert 2.0 < dm < 12.0
    # Nothing can be removed from a crop with no marketable product.
    carbon, straw = carbon_input_from_crop(hemp, dm, residue_removed_fraction=1.0)
    assert carbon > 0.0 and straw > 0.0


def test_c4_crops_fix_more_biomass_per_unit_water(reg):
    c4 = above_ground_dry_matter(reg.crop("sorghum_sudan_cover"), None, 300.0)
    c3 = above_ground_dry_matter(reg.crop("sunn_hemp"), None, 300.0)
    assert c4 > c3
    assert "maize" in C4_CROPS and "wheat_winter" not in C4_CROPS


def test_fallow_produces_no_biomass(reg):
    assert above_ground_dry_matter(reg.crop("fallow_bare"), None, 100.0) == 0.0


# --- plan validation --------------------------------------------------------------


def test_overlapping_seasons_are_rejected(reg, forcing, site_soil):
    """Two crops cannot occupy one field; silently double-cropping would be wrong."""
    plan = RotationPlan(
        "impossible",
        (SeasonPlan("wheat_winter", OCT1, 285), SeasonPlan("maize", 300, 150)),
        cycle_years=1,
    )
    with pytest.raises(RotationError, match="overlaps"):
        _run(plan, reg, forcing, site_soil, cycles=3)


def test_empty_plan_is_rejected():
    with pytest.raises(RotationError, match="at least one season"):
        RotationPlan("nothing", (), 1)


def test_season_outside_the_declared_cycle_is_rejected():
    with pytest.raises(RotationError, match="outside the declared"):
        RotationPlan("bad", (SeasonPlan("maize", 125, year_offset=3),), cycle_years=2)


def test_invalid_season_fields_are_rejected():
    with pytest.raises(RotationError, match="planting_doy"):
        SeasonPlan("maize", 400)
    with pytest.raises(RotationError, match="residue_removed_fraction"):
        SeasonPlan("maize", 125, residue_removed_fraction=1.5)
    with pytest.raises(RotationError, match="year_offset"):
        SeasonPlan("maize", 125, year_offset=-1)


def test_fallow_slot_without_a_duration_is_rejected(reg, forcing, site_soil):
    plan = RotationPlan("bare", (SeasonPlan("fallow_bare", 100),), 1)
    with pytest.raises(RotationError, match="must declare duration_days"):
        _run(plan, reg, forcing, site_soil, cycles=2)


def test_mismatched_et0_is_rejected(reg, forcing, site_soil):
    weather, _ = forcing
    soil, soc = site_soil
    with pytest.raises(RotationError, match="et0 series length"):
        simulate_rotation(
            CONTINUOUS, registry=reg, soil=soil, weather=weather,
            et0_mm=np.zeros(10), start_year=1984, cycles=2, initial_soc_t_ha=soc,
        )


def test_a_plan_that_does_not_fit_the_record_is_rejected(reg, forcing, site_soil):
    weather, et0 = forcing
    soil, soc = site_soil
    with pytest.raises(RotationError, match="no season fits"):
        simulate_rotation(
            CONTINUOUS, registry=reg, soil=soil, weather=weather, et0_mm=et0,
            start_year=2050, cycles=3, initial_soc_t_ha=soc,
        )


# --- reporting --------------------------------------------------------------------


def test_summary_exposes_the_comparison_fields(reg, forcing, site_soil):
    r = _run(WHEAT_FALLOW, reg, forcing, site_soil)
    s = r.summary(r.total_days / 365.25)
    assert set(s) >= {
        "bare_fraction", "transpiration_mm", "soil_evaporation_mm",
        "evaporation_fraction", "irrigation_gross_mm", "runoff_mm",
        "carbon_change_t_ha", "carbon_change_t_ha_per_year",
    }
    assert all(np.isfinite(v) for v in s.values())


def test_crop_sequence_is_exposed_for_the_pathogen_check(reg):
    plan = RotationPlan(
        "wheat - sorghum",
        (SeasonPlan("wheat_winter", OCT1, 285),
         SeasonPlan("sorghum", 152, 125, year_offset=1)),
        cycle_years=2,
    )
    assert plan.crop_sequence == ("wheat_winter", "sorghum")
    assert reg.check_rotation(list(plan.crop_sequence), cyclic=True) is not None


def test_low_confidence_ky_is_flagged_in_the_notes(reg, forcing, site_soil):
    """A yield resting on an analogue Ky must say so."""
    plan = RotationPlan("chickpea", (SeasonPlan("chickpea", OCT1, 110),), 1)
    r = _run(plan, reg, forcing, site_soil, cycles=10)
    assert any("analogue" in n for n in r.notes)


def test_carbon_initialisation_is_recorded(reg, forcing, site_soil):
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    assert any("implied historic input" in n for n in r.notes)


# --- nitrogen is now coupled into the yield ---------------------------------------


def test_nitrogen_can_be_the_binding_constraint(reg, forcing, site_soil):
    """Without fertiliser the crop starves, and the model says which limited it."""
    starved = RotationPlan(
        "unfertilised continuous wheat",
        (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=0.0),), 1,
    )
    r = _run(starved, reg, forcing, site_soil)
    crops = [s for s in r.seasons if not s.is_fallow]
    assert all(s.limiting_factor in ("nitrogen", "water", "none") for s in crops)
    assert r.nitrogen_limited_seasons > len(crops) / 2
    assert any("limited by nitrogen" in n for n in r.notes)


def test_adequate_fertiliser_shifts_the_limit_to_water(reg, forcing, site_soil):
    """The correct dryland High Plains story: water binds, not nitrogen."""
    fed = _run(CONTINUOUS, reg, forcing, site_soil)
    crops = [s for s in fed.seasons if not s.is_fallow]
    water_limited = sum(1 for s in crops if s.limiting_factor == "water")
    assert water_limited > fed.nitrogen_limited_seasons


def test_fertiliser_raises_yield_with_diminishing_returns(reg, forcing, site_soil):
    yields = []
    for rate in (0.0, 40.0, 70.0):
        plan = RotationPlan(
            f"wheat at {rate:.0f}N",
            (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=rate),), 1,
        )
        yields.append(_run(plan, reg, forcing, site_soil).mean_yield("wheat_winter"))
    assert yields[0] < yields[1] < yields[2]
    # Water eventually caps it, so the second increment buys less than the first.
    assert (yields[2] - yields[1]) < (yields[1] - yields[0])


def test_yield_uses_the_binding_constraint_not_the_product(reg, forcing, site_soil):
    """Liebig: multiplying water and nitrogen factors would charge twice."""
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    for s in (x for x in r.seasons if not x.is_fallow):
        if s.relative_yield is not None:
            expected = min(s.water_factor, s.nitrogen_factor)
            assert s.relative_yield == pytest.approx(max(expected, 0.0), abs=1e-9)
            assert s.relative_yield >= s.water_factor * s.nitrogen_factor - 1e-9


def test_a_legume_fixes_nitrogen_and_it_is_reported(reg, forcing, site_soil):
    plan = RotationPlan("chickpea", (SeasonPlan("chickpea", OCT1, 110),), 1)
    r = _run(plan, reg, forcing, site_soil, cycles=10)
    assert r.total_nitrogen_fixed_kg_ha > 0.0
    assert all(s.nitrogen_fixed_kg_ha > 0.0 for s in r.seasons if not s.is_fallow)


def test_a_cereal_fixes_nothing(reg, forcing, site_soil):
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    assert r.total_nitrogen_fixed_kg_ha == 0.0


def test_nitrogen_fluxes_are_recorded_monthly(reg, forcing, site_soil):
    r = _run(CONTINUOUS, reg, forcing, site_soil)
    assert len(r.nitrogen_fluxes) > 400  # monthly over ~39 years
    assert r.total_nitrogen_leached_kg_ha >= 0.0


def test_negative_fertiliser_is_refused():
    with pytest.raises(RotationError, match="fertiliser"):
        SeasonPlan("maize", 125, fertiliser_n_kg_ha=-10.0)


def test_residue_carbon_actually_reaches_the_pools(reg, forcing, site_soil):
    """Regression guard: residue was being written to an already-stepped month.

    The symptom was a carbon change identical whether straw was baled or retained.
    """
    retained = _run(CONTINUOUS, reg, forcing, site_soil)
    baled = RotationPlan(
        "baled", (SeasonPlan("wheat_winter", OCT1, 285, fertiliser_n_kg_ha=N_RATE,
                             residue_removed_fraction=0.9),), 1,
    )
    stripped = _run(baled, reg, forcing, site_soil)
    assert retained.carbon_change_t_ha != stripped.carbon_change_t_ha
    assert retained.carbon_change_t_ha > stripped.carbon_change_t_ha

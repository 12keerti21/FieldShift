"""The ponded paddy balance: a different model, and the reasons it has to be.

Rice under continuous submergence cannot be run through the dual-Kc balance. These
tests pin the three things that make it a separate model rather than a parameter
choice - no stress by construction, the single crop coefficient rather than the
basal one, and percolation as a driven flux rather than overflow - plus the mass
balance, which must close exactly because a residual there is a bug.
"""

from __future__ import annotations

import numpy as np
import pytest

from fieldshift.agro.climate import DailyWeather
from fieldshift.agro.waterbalance import (
    IrrigationPolicy,
    WaterBalanceError,
    kc_curve,
    kcb_curve,
    run_ponded_balance,
    run_water_balance,
)
from fieldshift.data import load_registry

REGISTRY = load_registry()
RICE = REGISTRY.crop("rice_lowland")
PADDY = IrrigationPolicy(
    mode="ponded", efficiency=0.70, percolation_mm_day=4.0,
    establishment_mm=180.0, target_ponding_mm=50.0, min_ponding_mm=20.0,
    bund_height_mm=100.0, max_application_mm=75.0,
)


def weather(n_days: int = 150, rain_mm: float = 0.0, tmax: float = 34.0):
    start = np.datetime64("1990-05-01")
    dates = start + np.arange(n_days).astype("timedelta64[D]")
    return DailyWeather(
        dates=dates,
        tmax_c=np.full(n_days, tmax),
        tmin_c=np.full(n_days, tmax - 12.0),
        precip_mm=np.full(n_days, rain_mm),
        solar_mj_m2=np.full(n_days, 21.0),
        rh_mean_pct=np.full(n_days, 65.0),
        wind_2m_ms=np.full(n_days, 2.0),
    )


def soil():
    return REGISTRY.texture("clay_loam")


def et0(n_days: int = 150, value: float = 5.0):
    return np.full(n_days, value)


# ------------------------------------------------------- it is a separate model


def test_run_water_balance_dispatches_a_ponded_policy():
    """Every caller goes through run_water_balance, so the dispatch lives there.

    A caller that had to check the mode itself would eventually forget and run a
    flooded field through a drying-soil balance.
    """
    w, e = weather(), et0()
    result = run_water_balance(
        RICE, soil(), w, e, duration_days=125, irrigation=PADDY
    )
    direct = run_ponded_balance(
        RICE, soil(), w, e, duration_days=125, irrigation=PADDY
    )
    assert result.total_irrigation_gross_mm == pytest.approx(
        direct.total_irrigation_gross_mm
    )
    assert any("ponded paddy" in n.lower() for n in result.notes)


def test_ponded_balance_refuses_a_non_ponded_policy():
    with pytest.raises(WaterBalanceError, match="needs a ponded policy"):
        run_ponded_balance(
            RICE, soil(), weather(), et0(), duration_days=125,
            irrigation=IrrigationPolicy(mode="rainfed"),
        )


def test_a_fallow_cannot_be_ponded():
    with pytest.raises(WaterBalanceError, match="fallow cannot be ponded"):
        run_ponded_balance(
            REGISTRY.crop("fallow_bare"), soil(), weather(), et0(),
            duration_days=60, irrigation=PADDY,
        )


# ------------------------------------------------------- the physics that differs


def test_a_submerged_crop_is_never_water_stressed():
    """Ks is 1 by physics here, not by assumption.

    There is no root-zone depletion under standing water, so there is nothing for
    a stress coefficient to express. If this ever fails, the paddy path has
    acquired a depletion term it should not have.
    """
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=PADDY
    )
    assert np.all(r.ks == 1.0)
    assert r.stress_days == 0
    assert r.total_transpiration_mm == pytest.approx(
        r.total_potential_transpiration_mm
    )


def test_paddy_uses_the_single_coefficient_not_the_basal_one():
    """FAO-56 Table 12, deliberately the opposite of the dual-Kc path.

    Table 12's paddy Kc already includes evaporation off the water surface, which
    is exactly the term a drying-soil curve cannot supply. Using Kcb here would
    silently drop open-water evaporation from a flooded field.
    """
    n = 125
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(n_days=150), duration_days=n, irrigation=PADDY
    )
    kc = np.maximum(kc_curve(RICE, n), kcb_curve(RICE, n))
    expected_etc = float((kc * 5.0).sum())
    actual_etc = r.total_transpiration_mm + r.total_soil_evaporation_mm
    assert actual_etc == pytest.approx(expected_etc, rel=1e-9)
    # and it is strictly more than the basal coefficient alone would give
    assert actual_etc > float((kcb_curve(RICE, n) * 5.0).sum())


def test_transpiration_plus_evaporation_equals_etc_exactly():
    """The T/E split must not change the total, only attribute it."""
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=PADDY
    )
    kc = np.maximum(kc_curve(RICE, 125), kcb_curve(RICE, 125))
    assert (r.transpiration_mm + r.soil_evaporation_mm) == pytest.approx(
        kc * 5.0, rel=1e-9
    )


def test_percolation_is_a_driven_flux_not_overflow():
    """In the dual-Kc balance percolation is what a full profile sheds.

    Under a puddled paddy it continues every day there is water above the plough
    pan, which is why it is typically the largest loss after ET and why it is the
    single most load-bearing assumption in the Punjab run.
    """
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=PADDY
    )
    # 125 days at 4 mm/day, less any days the pond could not supply it
    assert 0.8 * 125 * 4.0 <= r.total_deep_percolation_mm <= 125 * 4.0
    zero_perc = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125,
        irrigation=IrrigationPolicy(
            mode="ponded", percolation_mm_day=0.0, establishment_mm=180.0,
            efficiency=0.70,
        ),
    )
    assert zero_perc.total_deep_percolation_mm == pytest.approx(0.0)
    assert (
        zero_perc.total_irrigation_gross_mm < r.total_irrigation_gross_mm
    ), "percolation must cost irrigation water"


def test_the_bund_impounds_rain_instead_of_shedding_it():
    """A paddy's runoff is low and its percolation high - the opposite of a
    curve-number regime, which is why the paddy path does not use one."""
    dry = run_ponded_balance(
        RICE, soil(), weather(rain_mm=0.0), et0(), duration_days=125,
        irrigation=PADDY,
    )
    wet = run_ponded_balance(
        RICE, soil(), weather(rain_mm=8.0), et0(), duration_days=125,
        irrigation=PADDY,
    )
    assert wet.total_irrigation_gross_mm < dry.total_irrigation_gross_mm
    # nothing leaves until the pond is above the bund
    assert np.all(dry.runoff_mm == 0.0)
    assert np.all(dry.curve_number == 0.0)


def test_establishment_water_is_applied_once_at_transplanting():
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=PADDY
    )
    assert r.irrigation_applied_mm[0] >= PADDY.establishment_mm
    assert any("Establishment" in n for n in r.notes)


# ------------------------------------------------------- mass balance


@pytest.mark.parametrize("rain", [0.0, 3.0, 12.0, 40.0])
@pytest.mark.parametrize("percolation", [0.0, 2.0, 8.0])
def test_mass_balance_closes_exactly(rain, percolation):
    """A residual here is a bug, not an approximation.

    The pond and the draw from the saturated profile are both stores and both have
    to be counted. Leaving the profile draw out left the balance short by exactly
    that amount - an untracked store that always has water is how a model ends up
    reporting a drained paddy as unstressed.
    """
    policy = IrrigationPolicy(
        mode="ponded", efficiency=0.70, percolation_mm_day=percolation,
        establishment_mm=180.0, max_application_mm=75.0,
    )
    r = run_ponded_balance(
        RICE, soil(), weather(rain_mm=rain), et0(), duration_days=125,
        irrigation=policy,
    )
    assert abs(r.closure_residual_mm) < 1e-6


def test_a_starved_pond_is_reported_not_hidden():
    """With application capped hard the pond empties, and the model must say so.

    This is where the no-stress assumption is weakest, so it is surfaced rather
    than left for someone to infer.
    """
    starved = IrrigationPolicy(
        mode="ponded", efficiency=0.70, percolation_mm_day=12.0,
        establishment_mm=0.0, max_application_mm=1.0,
        target_ponding_mm=50.0, min_ponding_mm=20.0, bund_height_mm=100.0,
    )
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=starved
    )
    assert any("fell to zero" in n for n in r.notes)
    assert abs(r.closure_residual_mm) < 1e-6


def test_methane_absence_is_stated_on_every_run():
    """Methane is the dominant climate term for paddy and is not modelled.

    The carbon objective for a rice rotation is therefore incomplete in rice's
    favour, which a farmer comparing rice against a dry crop has to be told.
    """
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=PADDY
    )
    assert any("Methane" in n for n in r.notes)


def test_a_paddy_hands_on_a_wet_profile():
    """The real agronomic legacy of rice: wheat sown after it starts wet."""
    r = run_ponded_balance(
        RICE, soil(), weather(), et0(), duration_days=125, irrigation=PADDY
    )
    assert r.final_root_depletion_mm == pytest.approx(0.0)
    assert r.final_surface_depletion_mm == pytest.approx(0.0)


# ------------------------------------------------------- policy validation


@pytest.mark.parametrize("kwargs, match", [
    ({"percolation_mm_day": -1.0}, "percolation"),
    ({"establishment_mm": -5.0}, "establishment"),
    ({"min_ponding_mm": 80.0, "target_ponding_mm": 50.0}, "min_ponding"),
    ({"bund_height_mm": 10.0, "target_ponding_mm": 50.0}, "bund_height"),
])
def test_incoherent_ponded_policies_are_refused(kwargs, match):
    with pytest.raises(WaterBalanceError, match=match):
        IrrigationPolicy(mode="ponded", **kwargs)


def test_ponded_fields_are_ignored_outside_ponded_mode():
    """They must not silently affect a rainfed or sprinkler run."""
    policy = IrrigationPolicy(mode="rainfed", percolation_mm_day=99.0)
    r = run_water_balance(
        REGISTRY.crop("wheat"), soil(), weather(rain_mm=3.0), et0(),
        duration_days=120, irrigation=policy,
    )
    assert r.total_deep_percolation_mm < 99.0 * 120


# ─────────────────────────────────────────────── the flood window, and its drain


def flood_policy(start=0, days=None, percolation=2.0):
    return IrrigationPolicy(
        mode="ponded", efficiency=0.75, percolation_mm_day=percolation,
        establishment_mm=120.0, target_ponding_mm=75.0, min_ponding_mm=50.0,
        bund_height_mm=125.0, max_application_mm=75.0,
        flood_start_day=start, flood_days=days,
    )


def test_a_split_season_closes_its_mass_balance():
    """The hard case, and the one that exposed three separate bugs.

    A crop that is dry, then flooded, then drained has two regime changes, and
    every one of them is a chance to invent or destroy water. The drain was
    being counted as runoff AND as a release, the storage datum moved with the
    root zone so a profile that gained nothing appeared to gain, and the water
    filling newly explored saturated soil was never charged.
    """
    for start, days in ((0, None), (35, 95), (10, 40), (1, 1)):
        r = run_ponded_balance(
            RICE, soil(), weather(rain_mm=4.0), et0(), duration_days=125,
            irrigation=flood_policy(start, days),
        )
        assert abs(r.closure_residual_mm) < 1e-6, (
            f"flood from {start} for {days}: residual {r.closure_residual_mm}"
        )


def test_the_dry_phase_can_be_water_stressed_and_the_flooded_phase_cannot():
    """The whole point of splitting the season.

    Before the permanent flood a drill-seeded crop is an ordinary dryland crop
    and can run short. Under the flood it cannot, by construction.
    """
    r = run_ponded_balance(
        RICE, soil(), weather(rain_mm=0.0), et0(), duration_days=125,
        irrigation=flood_policy(start=40, days=60),
    )
    assert np.all(r.ks[40:100] == 1.0), "no stress is possible under standing water"
    assert r.ks[:40].min() < 1.0, "the dry phase must be able to feel it"


def test_flooding_the_whole_season_is_still_the_default():
    """Transplanted paddy goes into an already-flooded field.

    Punjab depends on this, so the default must not have moved.
    """
    policy = IrrigationPolicy(mode="ponded", efficiency=0.75)
    assert policy.flood_start_day == 0
    assert policy.flood_days is None
    r = run_ponded_balance(RICE, soil(), weather(), et0(), duration_days=125,
                           irrigation=policy)
    assert np.all(r.ks == 1.0)


def test_a_shorter_flood_uses_less_water_than_a_longer_one():
    """The relationship the Arkansas failure turned on."""
    long_flood = run_ponded_balance(
        RICE, soil(), weather(rain_mm=2.0), et0(), duration_days=125,
        irrigation=flood_policy(0, None))
    short_flood = run_ponded_balance(
        RICE, soil(), weather(rain_mm=2.0), et0(), duration_days=125,
        irrigation=flood_policy(35, 70))
    assert short_flood.total_deep_percolation_mm < long_flood.total_deep_percolation_mm


def test_a_flood_window_outside_the_season_is_refused():
    """Silently running an unflooded paddy would report a dryland crop as rice."""
    with pytest.raises(WaterBalanceError, match="entirely outside the season"):
        run_ponded_balance(RICE, soil(), weather(), et0(), duration_days=100,
                           irrigation=flood_policy(start=150, days=20))


@pytest.mark.parametrize("bad", [{"flood_start_day": -1}, {"flood_days": 0}])
def test_incoherent_flood_windows_are_refused(bad):
    with pytest.raises(WaterBalanceError):
        IrrigationPolicy(mode="ponded", **bad)


def test_the_drain_hands_on_a_profile_at_field_capacity():
    """What the following crop actually inherits.

    Standing water leaves at the drain; the profile keeps field capacity. A
    model that handed on a saturated profile would credit the next crop with
    water that ran off the field.
    """
    r = run_ponded_balance(
        RICE, soil(), weather(rain_mm=0.0), et0(), duration_days=125,
        irrigation=flood_policy(start=20, days=60))
    assert r.final_root_depletion_mm >= 0.0
    assert r.total_runoff_mm > 0.0, "the released pond must leave the field"

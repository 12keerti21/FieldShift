"""RothC-26.3 soil organic carbon turnover.

Validated against the model's published behaviour rather than only internal
consistency: the temperature modifier equals 1.0 at RothC's reference temperature,
the CO2 partition falls with clay by the documented relation, and carbon balance
closes exactly.

The behaviour that matters agronomically - and that these tests pin - is that the
same residue input builds MORE carbon on a heavy soil than a light one, and that a
bare soil loses carbon faster than a cropped one. Those two facts are what make a
carbon-building rotation soil-specific rather than universal.
"""

import numpy as np
import pytest

from fieldshift.agro.rothc import (
    BARE_SOIL_TSMD_DIVISOR,
    COVERED_RATE_FACTOR,
    DPM_RPM_ARABLE,
    DPM_RPM_GRASSLAND,
    K_DPM,
    K_HUM,
    MIN_MOISTURE_MODIFIER,
    RothCError,
    RothCPools,
    co2_fraction,
    equilibrium_pools,
    initialise_from_measured_soc,
    iom_from_soc,
    max_tsmd_mm,
    moisture_modifier,
    rothc_step,
    run_rothc,
    soc_from_organic_carbon_pct,
    temperature_modifier,
)

# A temperate-ish 12-month climatology for spin-up tests.
TEMP = np.array([2.0, 3.5, 7.0, 11.0, 15.5, 20.0, 22.5, 21.5, 17.5, 11.5, 6.0, 3.0])
RAIN = np.array([35, 30, 40, 45, 60, 65, 60, 55, 45, 45, 40, 38], dtype=float)
ET0 = np.array([15, 22, 42, 65, 95, 115, 125, 110, 75, 45, 22, 14], dtype=float)
COVERED = np.array([False, False, True, True, True, True, True, True, True, False, False, False])


# --- rate modifiers against published behaviour ---------------------------------


def test_temperature_modifier_is_unity_at_the_rothc_reference():
    """RothC is parameterised so that a = 1.0 at about 9.25 degC."""
    assert float(temperature_modifier(9.25)) == pytest.approx(1.0, abs=0.01)


def test_temperature_modifier_is_zero_below_minus_five():
    assert float(temperature_modifier(-10.0)) == 0.0
    assert float(temperature_modifier(-5.1)) == 0.0
    assert float(temperature_modifier(-4.9)) > 0.0


def test_temperature_modifier_rises_monotonically():
    t = np.linspace(-4.0, 40.0, 60)
    a = temperature_modifier(t)
    assert np.all(np.diff(a) > 0)
    assert float(temperature_modifier(30.0)) > 4.0


def test_moisture_modifier_anchors_and_bounds():
    cap = max_tsmd_mm(20.0)
    assert moisture_modifier(0.0, cap) == 1.0  # no deficit
    assert moisture_modifier(0.444 * cap, cap) == pytest.approx(1.0, abs=1e-6)
    assert moisture_modifier(cap, cap) == pytest.approx(MIN_MOISTURE_MODIFIER)
    for frac in np.linspace(0.0, 1.0, 21):
        b = moisture_modifier(frac * cap, cap)
        assert MIN_MOISTURE_MODIFIER <= b <= 1.0


def test_moisture_modifier_declines_with_deepening_deficit():
    cap = max_tsmd_mm(20.0)
    values = [moisture_modifier(f * cap, cap) for f in np.linspace(0.4, 1.0, 10)]
    assert all(a >= b for a, b in zip(values, values[1:]))


def test_max_tsmd_scales_with_clay_and_depth():
    assert max_tsmd_mm(40.0) < max_tsmd_mm(10.0)  # more negative: heavier soil
    assert max_tsmd_mm(20.0, depth_cm=46.0) == pytest.approx(2.0 * max_tsmd_mm(20.0))
    # The published form, checked at 20% clay over the standard 23 cm.
    assert max_tsmd_mm(20.0) == pytest.approx(-(20 + 1.3 * 20 - 0.01 * 400))


def test_bare_soil_reaches_a_smaller_maximum_deficit():
    covered, bare = max_tsmd_mm(25.0, covered=True), max_tsmd_mm(25.0, covered=False)
    assert bare == pytest.approx(covered / BARE_SOIL_TSMD_DIVISOR)
    assert bare > covered  # less negative


def test_co2_fraction_falls_as_clay_rises():
    """Clay physically protects organic matter, so less decomposed C is lost."""
    clays = np.array([2.0, 10.0, 20.0, 35.0, 60.0])
    fractions = np.array([co2_fraction(c) for c in clays])
    assert np.all(np.diff(fractions) < 0)
    assert 0.80 < co2_fraction(5.0) < 0.85
    assert 0.74 < co2_fraction(60.0) < 0.78


def test_invalid_clay_and_depth_are_rejected():
    for bad in (-1.0, 101.0):
        with pytest.raises(RothCError, match="clay percent"):
            co2_fraction(bad)
    with pytest.raises(RothCError, match="depth"):
        max_tsmd_mm(20.0, depth_cm=0.0)
    with pytest.raises(RothCError, match="negative"):
        moisture_modifier(-10.0, 5.0)


# --- stocks and the inert pool ---------------------------------------------------


def test_organic_carbon_percent_converts_to_a_stock():
    """A concentration is not a stock; skipping bulk density is a 3x error."""
    assert soc_from_organic_carbon_pct(1.0, 1.3, 23.0) == pytest.approx(29.9, abs=0.1)
    # The real SSURGO value at our pilot point.
    assert 20.0 < soc_from_organic_carbon_pct(0.93, 1.18, 23.0) < 30.0


def test_stock_scales_with_density_and_depth():
    base = soc_from_organic_carbon_pct(1.0, 1.2, 23.0)
    assert soc_from_organic_carbon_pct(1.0, 1.2, 46.0) == pytest.approx(2 * base)
    assert soc_from_organic_carbon_pct(2.0, 1.2, 23.0) == pytest.approx(2 * base)


def test_implausible_bulk_density_is_rejected():
    with pytest.raises(RothCError, match="bulk density"):
        soc_from_organic_carbon_pct(1.0, 5.0)


def test_inert_pool_is_a_small_share_of_total_carbon():
    for soc in (20.0, 40.0, 80.0):
        assert 0.0 < iom_from_soc(soc) < 0.2 * soc


# --- the monthly step -----------------------------------------------------------


def test_pools_turn_over_at_their_characteristic_rates():
    """DPM should empty in weeks; HUM should barely move in a month."""
    pools = RothCPools(dpm=10.0, rpm=10.0, bio=10.0, hum=10.0, iom=0.0)
    out, _, _, _ = rothc_step(
        pools, clay_pct=20.0, temp_c=15.0, rain_mm=60.0, et0_mm=50.0,
        covered=True, carbon_input_t_ha=0.0,
    )
    # One month at 15 degC under a crop gives rate = a*b*c = 1.898*1.0*0.6 = 1.139,
    # so DPM retains exp(-10*1.139/12) = 39% while HUM retains 99.8%.
    dpm_retained = out.dpm / 10.0
    assert 0.30 < dpm_retained < 0.45, dpm_retained
    assert out.hum > 10.0  # loses almost nothing and gains from the other pools
    assert K_DPM / K_HUM == pytest.approx(500.0)

    # With no decomposition at all the contrast is unobscured by transfers.
    frozen, _, _, _ = rothc_step(
        pools, clay_pct=20.0, temp_c=-20.0, rain_mm=0.0, et0_mm=0.0,
        covered=True, carbon_input_t_ha=0.0,
    )
    assert frozen.total == pytest.approx(pools.total)


def test_input_is_split_by_the_dpm_rpm_ratio():
    empty = RothCPools(0.0, 0.0, 0.0, 0.0, 0.0)
    out, _, _, _ = rothc_step(
        empty, clay_pct=20.0, temp_c=-20.0, rain_mm=0.0, et0_mm=0.0,
        covered=True, carbon_input_t_ha=2.44, dpm_rpm_ratio=DPM_RPM_ARABLE,
    )
    # Frozen soil means no decomposition, so the split is visible directly.
    assert out.dpm / out.rpm == pytest.approx(DPM_RPM_ARABLE, rel=1e-6)
    assert out.total == pytest.approx(2.44)


def test_grassland_input_is_more_resistant_than_arable():
    empty = RothCPools(0.0, 0.0, 0.0, 0.0, 0.0)
    kw = dict(clay_pct=20.0, temp_c=-20.0, rain_mm=0.0, et0_mm=0.0, covered=True,
              carbon_input_t_ha=1.0)
    arable, _, _, _ = rothc_step(empty, dpm_rpm_ratio=DPM_RPM_ARABLE, **kw)
    grass, _, _, _ = rothc_step(empty, dpm_rpm_ratio=DPM_RPM_GRASSLAND, **kw)
    assert grass.rpm > arable.rpm  # a larger resistant share


def test_covered_soil_decomposes_more_slowly():
    pools = RothCPools(dpm=5.0, rpm=20.0, bio=2.0, hum=40.0, iom=3.0)
    kw = dict(clay_pct=20.0, temp_c=15.0, rain_mm=80.0, et0_mm=40.0,
              carbon_input_t_ha=0.0)
    covered, _, rate_c, _ = rothc_step(pools, covered=True, **kw)
    bare, _, rate_b, _ = rothc_step(pools, covered=False, **kw)
    assert covered.total > bare.total
    assert rate_c < rate_b
    assert COVERED_RATE_FACTOR < 1.0


def test_negative_inputs_are_rejected():
    pools = RothCPools(1.0, 1.0, 1.0, 1.0, 1.0)
    kw = dict(clay_pct=20.0, temp_c=10.0, rain_mm=50.0, et0_mm=50.0, covered=True)
    with pytest.raises(RothCError, match="carbon input"):
        rothc_step(pools, carbon_input_t_ha=-1.0, **kw)
    with pytest.raises(RothCError, match="dpm_rpm_ratio"):
        rothc_step(pools, carbon_input_t_ha=0.0, dpm_rpm_ratio=0.0, **kw)


def test_pools_cannot_hold_negative_carbon():
    with pytest.raises(RothCError, match="negative carbon"):
        RothCPools(-1.0, 0.0, 0.0, 0.0, 0.0)


# --- multi-month runs and carbon closure ----------------------------------------


def _run(years=10, annual_input=3.0, clay=20.0, covered=COVERED, ratio=DPM_RPM_ARABLE):
    n = 12 * years
    tile = lambda a: np.tile(a, years)
    start = RothCPools(1.0, 10.0, 1.0, 30.0, 3.0)
    return run_rothc(
        start, clay_pct=clay, temp_c=tile(TEMP), rain_mm=tile(RAIN), et0_mm=tile(ET0),
        covered=tile(covered), carbon_input_t_ha=np.full(n, annual_input / 12.0),
        dpm_rpm_ratio=ratio,
    )


def test_carbon_balance_closes_exactly():
    """Everything in must appear as stored carbon or as CO2."""
    r = _run(years=25)
    assert abs(r.closure_residual_t_ha) < 1e-9, r.closure_residual_t_ha


def test_result_series_have_consistent_shapes():
    r = _run(years=5)
    assert len(r.soc_t_ha) == 61  # 60 months plus the initial state
    assert len(r.pools) == 61
    assert len(r.co2_t_ha) == 60
    assert np.all(np.isfinite(r.soc_t_ha))
    assert np.all(r.co2_t_ha >= 0.0)


def test_more_residue_input_builds_more_carbon():
    low = _run(years=40, annual_input=1.5)
    high = _run(years=40, annual_input=6.0)
    assert high.final_soc > low.final_soc
    assert high.change_t_ha > low.change_t_ha


def test_the_same_input_builds_more_carbon_on_clay_than_on_sand():
    """The central agronomic result: a carbon-building rotation is soil-specific."""
    sand = _run(years=50, annual_input=4.0, clay=5.0)
    clay = _run(years=50, annual_input=4.0, clay=45.0)
    assert clay.final_soc > sand.final_soc
    assert clay.co2_t_ha.sum() < sand.co2_t_ha.sum()


def test_a_permanently_bare_soil_loses_carbon_faster_than_a_cropped_one():
    bare = _run(years=40, annual_input=1.0, covered=np.zeros(12, dtype=bool))
    cropped = _run(years=40, annual_input=1.0, covered=np.ones(12, dtype=bool))
    assert bare.final_soc < cropped.final_soc


def test_zero_input_drives_carbon_down_towards_the_inert_pool():
    r = _run(years=200, annual_input=0.0)
    assert r.final_soc < r.initial_soc
    assert r.final_soc > r.pools[0].iom * 0.99  # the inert pool never decomposes
    assert r.pools[-1].iom == pytest.approx(r.pools[0].iom)


def test_mismatched_series_lengths_are_rejected():
    start = RothCPools(1.0, 1.0, 1.0, 1.0, 1.0)
    with pytest.raises(RothCError, match="length"):
        run_rothc(start, clay_pct=20.0, temp_c=TEMP, rain_mm=RAIN[:6], et0_mm=ET0,
                  covered=COVERED, carbon_input_t_ha=np.zeros(12))


def test_empty_series_is_rejected():
    start = RothCPools(1.0, 1.0, 1.0, 1.0, 1.0)
    with pytest.raises(RothCError, match="empty"):
        run_rothc(start, clay_pct=20.0, temp_c=np.array([]), rain_mm=np.array([]),
                  et0_mm=np.array([]), covered=np.array([], dtype=bool),
                  carbon_input_t_ha=np.array([]))


# --- initialisation --------------------------------------------------------------


def test_equilibrium_is_actually_stable():
    """A short spin-up leaves HUM still filling, which looks like a false trend."""
    pools = equilibrium_pools(
        3.0, clay_pct=20.0, temp_c=TEMP, rain_mm=RAIN, et0_mm=ET0, covered=COVERED,
        iom_t_ha=3.0, years=600,
    )
    n = 12 * 30
    checked = run_rothc(
        pools, clay_pct=20.0, temp_c=np.tile(TEMP, 30), rain_mm=np.tile(RAIN, 30),
        et0_mm=np.tile(ET0, 30), covered=np.tile(COVERED, 30),
        carbon_input_t_ha=np.full(n, 3.0 / 12.0),
    )
    assert abs(checked.change_t_ha) < 0.5  # t C/ha over 30 years


def test_equilibrium_rises_with_input():
    kw = dict(clay_pct=20.0, temp_c=TEMP, rain_mm=RAIN, et0_mm=ET0, covered=COVERED,
              iom_t_ha=3.0, years=400)
    assert equilibrium_pools(6.0, **kw).total > equilibrium_pools(2.0, **kw).total


def test_spin_up_requires_a_twelve_month_climatology():
    with pytest.raises(RothCError, match="12-month"):
        equilibrium_pools(3.0, clay_pct=20.0, temp_c=TEMP[:6], rain_mm=RAIN[:6],
                          et0_mm=ET0[:6], covered=COVERED[:6])


def test_inverse_mode_recovers_a_measured_soc():
    """Starting a run from a measured total requires knowing the distribution.

    A measured stock says nothing about how carbon is split between a pool with a
    five-week turnover and one with fifty years, and that split controls how fast
    the soil can respond to management.
    """
    target = 32.0
    pools, annual_input = initialise_from_measured_soc(
        target, clay_pct=20.0, temp_c=TEMP, rain_mm=RAIN, et0_mm=ET0, covered=COVERED,
    )
    assert pools.total == pytest.approx(target, abs=0.1)
    assert 0.0 < annual_input < 20.0
    assert pools.iom == pytest.approx(iom_from_soc(target))
    assert pools.hum > pools.dpm  # the slow pool dominates a steady-state soil


def test_inverse_mode_rejects_impossible_targets():
    with pytest.raises(RothCError, match="must be positive"):
        initialise_from_measured_soc(0.0, clay_pct=20.0, temp_c=TEMP, rain_mm=RAIN,
                                     et0_mm=ET0, covered=COVERED)
    with pytest.raises(RothCError, match="could not match"):
        initialise_from_measured_soc(500.0, clay_pct=20.0, temp_c=TEMP, rain_mm=RAIN,
                                     et0_mm=ET0, covered=COVERED, max_iterations=12)


# --- irrigation in the moisture term --------------------------------------------


def test_irrigation_speeds_decomposition_and_reduces_carbon_gain():
    """Standard RothC assumes rainfed; an irrigated field is wetter and turns over
    faster. Omitting irrigation flatters an irrigated rotation's carbon story."""
    n = 12 * 30
    start = RothCPools(1.0, 10.0, 1.0, 30.0, 3.0)
    kw = dict(
        clay_pct=20.0, temp_c=np.tile(TEMP, 30), rain_mm=np.tile(RAIN, 30),
        et0_mm=np.tile(ET0, 30), covered=np.tile(COVERED, 30),
        carbon_input_t_ha=np.full(n, 4.0 / 12.0),
    )
    rainfed = run_rothc(start, **kw)
    irrigated = run_rothc(start, irrigation_mm=np.full(n, 55.0), **kw)
    assert irrigated.final_soc < rainfed.final_soc
    assert irrigated.rate_modifier.mean() > rainfed.rate_modifier.mean()
    assert abs(irrigated.closure_residual_t_ha) < 1e-9


def test_irrigation_shallows_the_moisture_deficit():
    pools = RothCPools(1.0, 10.0, 1.0, 30.0, 3.0)
    kw = dict(clay_pct=20.0, temp_c=20.0, rain_mm=10.0, et0_mm=120.0,
              covered=True, carbon_input_t_ha=0.0, previous_tsmd_mm=-20.0)
    _, dry_tsmd, dry_rate, _ = rothc_step(pools, **kw)
    _, wet_tsmd, wet_rate, _ = rothc_step(pools, irrigation_mm=120.0, **kw)
    assert wet_tsmd > dry_tsmd  # less negative: a shallower deficit
    assert wet_rate > dry_rate


def test_negative_irrigation_is_rejected():
    pools = RothCPools(1.0, 1.0, 1.0, 1.0, 1.0)
    with pytest.raises(RothCError, match="irrigation"):
        rothc_step(pools, clay_pct=20.0, temp_c=10.0, rain_mm=50.0, et0_mm=50.0,
                   covered=True, carbon_input_t_ha=0.0, irrigation_mm=-5.0)

"""Soil nitrogen, coupled to RothC's carbon flows.

Three canonical rotation behaviours must EMERGE from the arithmetic rather than be
asserted anywhere:

1. high-C:N straw locks nitrogen up before releasing it,
2. a legume or green manure leaves a usable credit,
3. a fallow mineralises into an empty field and then leaches.

The suite also pins the design decision that fixed a real defect: nitrogen is held
PER POOL, so each pool's C:N follows from its history. An earlier version used one
assumed residue C:N for every pool, which meant changing the incoming residue also
changed the assumed composition of carbon already in the soil - moving the answer by
nearly 200 kg N/ha and swamping the effect being measured.
"""

import numpy as np
import pytest

from fieldshift.agro.nitrogen import (
    CN_BIO,
    CN_HUM,
    NitrogenError,
    NitrogenPools,
    NitrogenState,
    fixation,
    initial_nitrogen_pools,
    leaching,
    mineralisation_from_flows,
    nitrogen_step,
    nitrogen_stress_factor,
)
from fieldshift.agro.rothc import RothCPools, rothc_step_detailed

CARBON0 = RothCPools(dpm=0.5, rpm=6.0, bio=1.0, hum=20.0, iom=3.0)
WARM = dict(clay_pct=18.0, temp_c=18.0, rain_mm=70.0, et0_mm=60.0, covered=False)


def _run(residue_cn, carbon_added, months=12, demand=0.0, start_mineral=20.0):
    carbon = CARBON0
    state = NitrogenState(start_mineral, initial_nitrogen_pools(CARBON0))
    net, fluxes = [], []
    for m in range(months):
        before = carbon
        carbon, flows = rothc_step_detailed(
            carbon, carbon_input_t_ha=(carbon_added if m == 0 else 0.0), **WARM
        )
        state, flux = nitrogen_step(
            state, flows, before, residue_cn=residue_cn, demand_kg_n_ha=demand
        )
        net.append(flux.net_mineralised)
        fluxes.append(flux)
    return np.array(net), state, fluxes


# --- per-pool nitrogen: the design that fixed the defect -------------------------


def test_pool_cn_is_derived_not_assumed():
    pools = initial_nitrogen_pools(CARBON0)
    assert pools.cn_ratio("bio", CARBON0.bio) == pytest.approx(CN_BIO)
    assert pools.cn_ratio("hum", CARBON0.hum) == pytest.approx(CN_HUM)
    # The plant pools carry the historic residue ratio they were given.
    assert pools.cn_ratio("rpm", CARBON0.rpm) == pytest.approx(60.0)


def test_an_empty_pool_has_infinite_cn_and_releases_nothing():
    pools = NitrogenPools(0.0, 0.0, 0.0, 0.0)
    assert pools.cn_ratio("dpm", 1.0) == float("inf")
    _, flows = rothc_step_detailed(CARBON0, carbon_input_t_ha=0.0, **WARM)
    gross, _, released = mineralisation_from_flows(flows, CARBON0, pools)
    assert gross == 0.0
    assert all(v == 0.0 for v in released.values())


def test_whole_soil_cn_is_plausible():
    """A cultivated topsoil should land near 10-14, not 60 or 5."""
    pools = initial_nitrogen_pools(CARBON0)
    cn = (CARBON0.active * 1000.0) / pools.total
    assert 9.0 < cn < 15.0


def test_baseline_mineralisation_is_physically_sensible():
    """The defect produced 251 kg N/ha/month here; a real soil gives tens."""
    net, _, _ = _run(60.0, 0.0, months=1)
    assert 2.0 < net[0] < 40.0


# --- 1. straw immobilises, then releases ------------------------------------------


def test_high_cn_straw_immobilises_nitrogen_in_the_month_it_decomposes():
    """The classic effect, and it appears with a lag - month one is too early."""
    net, _, _ = _run(90.0, 3.0)
    assert net[1] < 0.0, f"expected immobilisation in month 2, got {net[1]:+.1f}"
    assert net[0] > 0.0, "month 1 cannot show it; the residue has not decomposed yet"
    assert net[3] > 0.0, "nitrogen must come back once the straw is worked through"


def test_straw_costs_the_following_crop_nitrogen_over_a_year():
    straw, _, _ = _run(90.0, 3.0)
    nothing, _, _ = _run(90.0, 0.0)
    delta = straw.sum() - nothing.sum()
    assert delta < 0.0, f"straw should lock up nitrogen on balance, got {delta:+.1f}"
    # Literature puts straw immobilisation in the tens of kg N/ha.
    assert -40.0 < delta < -3.0


def test_the_immobilisation_gradient_follows_cn():
    nothing, _, _ = _run(60.0, 0.0)
    deltas = {
        cn: _run(cn, 3.0)[0].sum() - nothing.sum()
        for cn in (18.0, 25.0, 65.0, 90.0)
    }
    ordered = [deltas[cn] for cn in (90.0, 65.0, 25.0, 18.0)]
    assert ordered == sorted(ordered), deltas
    assert deltas[90.0] < 0.0 < deltas[25.0]


# --- 2. legumes and green manures leave a credit ---------------------------------


def test_green_manure_leaves_a_usable_nitrogen_credit():
    nothing, _, _ = _run(60.0, 0.0)
    manure, _, _ = _run(18.0, 3.0)
    credit = manure.sum() - nothing.sum()
    # Reported credits for a legume cover crop run to 50-100 kg N/ha.
    assert 40.0 < credit < 150.0


def test_fixation_collapses_under_water_stress():
    """Fixation is energy-expensive and fails faster than growth does."""
    assert fixation(100.0, 1.0) == pytest.approx(100.0)
    assert fixation(100.0, 0.5) == pytest.approx(50.0)
    assert fixation(100.0, 0.0) == 0.0
    assert fixation(100.0, 1.0, season_fraction=0.25) == pytest.approx(25.0)
    assert fixation(100.0, 2.0) == pytest.approx(100.0)  # clipped


def test_negative_fixation_is_refused():
    with pytest.raises(NitrogenError, match="fixation"):
        fixation(-10.0, 1.0)


# --- 3. fallow mineralises into an empty field, then leaches ---------------------


def test_nitrogen_accumulates_with_no_crop_to_take_it_up():
    _, state, _ = _run(60.0, 0.0, months=12, demand=0.0)
    assert state.mineral_n_kg_ha > 100.0


def test_accumulated_nitrogen_is_exposed_to_leaching():
    """Banking water and banking nitrogen are not the same thing."""
    high = leaching(150.0, deep_percolation_mm=60.0, rootzone_water_mm=140.0)
    low = leaching(150.0, deep_percolation_mm=0.0, rootzone_water_mm=140.0)
    assert low == 0.0
    assert high > 40.0
    assert high < 150.0  # cannot lose more than is there


def test_leaching_scales_with_drainage_share():
    for dp in (0.0, 20.0, 60.0, 200.0):
        lost = leaching(100.0, dp, 100.0)
        assert 0.0 <= lost <= 100.0
    assert leaching(100.0, 100.0, 100.0) == pytest.approx(50.0)
    assert leaching(0.0, 50.0, 100.0) == 0.0


def test_leaching_rejects_negative_water():
    with pytest.raises(NitrogenError, match="negative"):
        leaching(50.0, -1.0, 100.0)


# --- supply limits, not negative pools -------------------------------------------


def test_a_crop_cannot_take_up_nitrogen_that_is_not_there():
    _, state, fluxes = _run(90.0, 3.0, months=6, demand=200.0, start_mineral=5.0)
    assert state.mineral_n_kg_ha >= 0.0
    assert all(f.taken_up <= 200.0 + 1e-9 for f in fluxes)
    assert any(f.taken_up < 200.0 for f in fluxes), "demand should go unmet somewhere"


def test_immobilisation_outcompetes_the_crop():
    """Microbes are in contact with the residue; the crop is not."""
    _, _, fluxes = _run(90.0, 6.0, months=4, demand=150.0, start_mineral=10.0)
    tight = fluxes[1]
    assert tight.immobilised > 0.0
    assert tight.taken_up < 150.0


def test_negative_state_is_refused():
    with pytest.raises(NitrogenError, match="mineral nitrogen"):
        NitrogenState(-5.0, initial_nitrogen_pools(CARBON0))
    with pytest.raises(NitrogenError, match="negative nitrogen"):
        NitrogenPools(-1.0, 0.0, 0.0, 0.0)


# --- the budget must close -------------------------------------------------------


def test_nitrogen_balance_closes_exactly():
    """Everything in must appear as stored nitrogen, uptake, leaching or gas."""
    carbon = CARBON0
    state = NitrogenState(30.0, initial_nitrogen_pools(CARBON0))
    start_total = state.total_n_kg_ha
    residue_cn = 45.0
    inputs = outputs = 0.0

    for m in range(24):
        before = carbon
        c_in = 0.3 if m % 12 < 6 else 0.0
        carbon, flows = rothc_step_detailed(carbon, carbon_input_t_ha=c_in, **WARM)
        state, flux = nitrogen_step(
            state, flows, before, residue_cn=residue_cn,
            fixed_kg_n_ha=2.0, fertiliser_kg_n_ha=1.0, demand_kg_n_ha=8.0,
            deep_percolation_mm=5.0, rootzone_water_mm=120.0, soil_is_wet=True,
        )
        residue_n = (flows.input_to_dpm + flows.input_to_rpm) * 1000.0 / residue_cn
        inputs += flux.fixed + flux.fertilised + residue_n
        outputs += flux.taken_up + flux.leached + flux.denitrified

    change = state.total_n_kg_ha - start_total
    assert change == pytest.approx(inputs - outputs, abs=1e-6)


# --- combining with water stress -------------------------------------------------


def test_stress_factor_is_the_fraction_of_demand_met():
    assert nitrogen_stress_factor(50.0, 100.0) == pytest.approx(0.5)
    assert nitrogen_stress_factor(150.0, 100.0) == 1.0
    assert nitrogen_stress_factor(0.0, 100.0) == 0.0
    assert nitrogen_stress_factor(0.0, 0.0) == 1.0  # no demand, no stress


def test_liebig_minimum_not_product_is_documented():
    """Multiplying water and nitrogen stress would double-count the same lost growth.

    A crop capped by nitrogen is not additionally penalised for also lacking water;
    the binding constraint already determines what it can build.
    """
    import pathlib
    source = pathlib.Path(
        __file__
    ).resolve().parents[1].joinpath("src/fieldshift/agro/nitrogen.py").read_text()
    assert "Liebig" in source
    assert "MINIMUM" in source
    water, nitro = 0.8, 0.5
    assert min(water, nitro) > water * nitro  # the choice is not cosmetic

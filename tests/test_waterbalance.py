"""FAO-56 dual crop coefficient water balance.

The strongest test here is mass balance closure. Every drop of rain and irrigation
must end up transpired, evaporated, run off, drained, or still in the profile; a
non-zero residual is a bug, not an approximation. It is asserted across crops,
soils and irrigation policies, because a water balance that leaks produces
plausible-looking seasonal totals that are quietly wrong.

The rest pins the behaviour the rotation comparison will rest on: that bare ground
spends its water on evaporation and runoff, that a drought-tolerant crop is less
stressed than a thirsty one on the same weather, and that stored soil water carries
from one season to the next.
"""

import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import DailyWeather, Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.synthetic import synthetic_weather
from fieldshift.agro.waterbalance import (
    KC_MIN,
    IrrigationPolicy,
    RAINFED,
    WaterBalanceError,
    _canopy_fraction,
    _kc_max,
    kcb_curve,
    root_depth_curve,
    run_water_balance,
)

SITE = Site(17.4, 78.5, 500.0, "Deccan")
MONSOON_SOWING = 166  # about 15 June


@pytest.fixture(scope="module")
def monsoon():
    w = synthetic_weather(SITE, "semi_arid_monsoon", start="2000-01-01", years=3, seed=7)
    return w, et0_penman_monteith(SITE, w).values_mm


@pytest.fixture(scope="module")
def dry_constant():
    """No rain, constant demand: lets depletion trajectories be checked exactly."""
    n = 400
    dates = np.arange(np.datetime64("2000-01-01", "D"), np.datetime64("2000-01-01", "D") + n)
    w = DailyWeather(
        dates=dates,
        tmax_c=np.full(n, 32.0),
        tmin_c=np.full(n, 20.0),
        precip_mm=np.zeros(n),
        rh_mean_pct=np.full(n, 50.0),
        wind_2m_ms=np.full(n, 2.0),
    )
    return w, np.full(n, 5.0)


# --- Kcb and root curves ---------------------------------------------------------


def test_kcb_curve_has_the_four_fao56_stages(reg):
    crop = reg.crop("wheat")
    kcb = kcb_curve(crop, crop.duration_days)
    s = crop.stages_days
    assert len(kcb) == crop.duration_days
    assert np.allclose(kcb[: s.ini], crop.kcb.ini)
    rise = kcb[s.ini : s.ini + s.dev]
    assert np.all(np.diff(rise) > 0)
    assert np.allclose(kcb[s.ini + s.dev : s.ini + s.dev + s.mid], crop.kcb.mid)
    decline = kcb[s.ini + s.dev + s.mid :]
    assert np.all(np.diff(decline) < 0)
    assert kcb.max() == pytest.approx(crop.kcb.mid)


def test_kcb_curve_ends_near_the_declared_end_value(reg):
    for cid in ("wheat", "maize", "chickpea", "cotton", "potato"):
        crop = reg.crop(cid)
        kcb = kcb_curve(crop, crop.duration_days)
        step = (crop.kcb.mid - crop.kcb.end) / max(crop.stages_days.late, 1)
        assert kcb[-1] == pytest.approx(crop.kcb.end, abs=abs(step) + 1e-9)


def test_the_dual_model_uses_basal_not_single_coefficients(reg):
    """Regression guard on a bug that overstated seasonal ET by ~7 percent.

    FAO-56 Table 12 gives SINGLE coefficients, which already contain soil
    evaporation; Table 17 gives BASAL coefficients, to which the dual model adds Ke
    separately. An earlier revision stored Table 12 values and fed them to the dual
    model, counting soil evaporation twice. Nothing in the water balance may read
    `crop.kc`.
    """
    crop = reg.crop("maize")
    assert crop.kcb.mid < crop.kc.mid
    assert crop.kcb.ini < crop.kc.ini

    curve = kcb_curve(crop, crop.duration_days)
    assert curve.max() == pytest.approx(crop.kcb.mid)
    assert curve.max() < crop.kc.mid

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src" / "fieldshift" / "agro" / "waterbalance.py"
    ).read_text()
    assert "crop.kc." not in source, (
        "the water balance must read crop.kcb, never crop.kc"
    )


def test_every_crop_produces_a_bounded_kcb_curve(reg):
    for crop in reg.crops.values():
        if crop.is_fallow:
            continue
        kcb = kcb_curve(crop, crop.duration_days)
        assert np.all(kcb >= 0.0) and np.all(kcb <= 1.4), crop.id
        assert np.isfinite(kcb).all(), crop.id


def test_roots_deepen_to_the_maximum_by_the_end_of_development(reg):
    crop = reg.crop("maize")
    zr = root_depth_curve(crop, crop.duration_days, 1.0)
    s = crop.stages_days
    assert zr[0] == pytest.approx(crop.root.initial_depth_m)
    assert zr[s.ini + s.dev - 1] == pytest.approx(crop.root.max_depth_m, rel=0.02)
    assert np.allclose(zr[s.ini + s.dev :], crop.root.max_depth_m)
    assert np.all(np.diff(zr) >= -1e-12)


def test_fallow_tracks_a_fixed_profile_rather_than_roots(reg):
    """Without a reservoir there is no way to credit the water a fallow conserves."""
    zr = root_depth_curve(reg.crop("fallow_bare"), 90, 1.2)
    assert np.allclose(zr, 1.2)


# --- coefficient helpers ---------------------------------------------------------


def test_canopy_fraction_is_zero_for_bare_soil_and_rises_with_kcb():
    assert _canopy_fraction(KC_MIN, 1.15, 1.0, KC_MIN) == 0.0
    assert _canopy_fraction(0.10, 1.15, 1.0, KC_MIN) == 0.0
    low = _canopy_fraction(0.4, 1.15, 1.0, KC_MIN)
    high = _canopy_fraction(1.1, 1.15, 1.0, KC_MIN)
    assert 0.0 < low < high < 1.0


def test_kc_max_never_falls_below_kcb_plus_margin():
    for kcb in (0.2, 0.8, 1.2, 1.3):
        assert _kc_max(kcb, 2.0, 45.0, 1.0) >= kcb + 0.05 - 1e-12


def test_kc_max_responds_to_wind_and_humidity():
    calm_humid = _kc_max(0.5, 1.0, 85.0, 1.0)
    windy_dry = _kc_max(0.5, 6.0, 20.0, 1.0)
    assert windy_dry > calm_humid
    assert _kc_max(0.5, 2.0, 45.0, 1.0) == pytest.approx(1.2)


# --- mass balance closure --------------------------------------------------------


@pytest.mark.parametrize(
    "crop_id", ["wheat", "maize", "sorghum", "pearl_millet", "chickpea", "cotton", "potato"]
)
@pytest.mark.parametrize("texture_id", ["sand", "loam", "clay"])
def test_mass_balance_closes_across_crops_and_soils(reg, monsoon, crop_id, texture_id):
    w, e = monsoon
    r = run_water_balance(
        reg.crop(crop_id), reg.texture(texture_id), w, e, planting_index=MONSOON_SOWING
    )
    assert abs(r.closure_residual_mm) < 1e-6, r.summary()


def test_mass_balance_closes_with_irrigation(reg, monsoon):
    w, e = monsoon
    policy = IrrigationPolicy(mode="refill_on_stress", efficiency=0.6)
    r = run_water_balance(
        reg.crop("potato"), reg.texture("sandy_loam"), w, e,
        planting_index=300, irrigation=policy,
    )
    assert abs(r.closure_residual_mm) < 1e-6
    assert r.total_irrigation_gross_mm > 0.0


def test_mass_balance_closes_with_a_seasonal_cap(reg, monsoon):
    w, e = monsoon
    policy = IrrigationPolicy(
        mode="refill_on_stress", efficiency=0.7, seasonal_cap_mm=120.0
    )
    r = run_water_balance(
        reg.crop("cotton"), reg.texture("clay_loam"), w, e,
        planting_index=MONSOON_SOWING, irrigation=policy,
    )
    assert abs(r.closure_residual_mm) < 1e-6
    assert r.total_irrigation_gross_mm <= 120.0 + 1e-9


def test_mass_balance_closes_for_a_bare_window(reg, monsoon):
    w, e = monsoon
    r = run_water_balance(
        reg.crop("fallow_bare"), reg.texture("silt_loam"), w, e,
        planting_index=MONSOON_SOWING, duration_days=120,
    )
    assert abs(r.closure_residual_mm) < 1e-6


def test_no_extraction_shortfall_under_normal_operation(reg, monsoon):
    """Ks should prevent the model ever drawing water the profile does not hold."""
    w, e = monsoon
    for cid in ("sorghum", "cotton", "chickpea"):
        r = run_water_balance(
            reg.crop(cid), reg.texture("sand"), w, e, planting_index=MONSOON_SOWING
        )
        assert r.extraction_shortfall_mm < 1e-6, cid


# --- the bare-ground claim -------------------------------------------------------


def test_bare_ground_spends_all_its_water_on_evaporation(reg, monsoon):
    w, e = monsoon
    r = run_water_balance(
        reg.crop("fallow_bare"), reg.texture("loam"), w, e,
        planting_index=MONSOON_SOWING, duration_days=120,
    )
    assert r.total_transpiration_mm == 0.0
    assert r.evaporation_fraction == 1.0
    assert r.stress_days == 0  # nothing growing can be stressed


def test_bare_ground_loses_far_more_to_runoff_than_a_covered_field(reg, monsoon):
    """The quantitative basis for recommending a crop over a bare window."""
    w, e = monsoon
    bare = run_water_balance(
        reg.crop("fallow_bare"), reg.texture("loam"), w, e,
        planting_index=MONSOON_SOWING, duration_days=120,
    )
    covered = run_water_balance(
        reg.crop("sunn_hemp"), reg.texture("loam"), w, e,
        planting_index=MONSOON_SOWING, duration_days=120,
    )
    assert bare.total_runoff_mm > covered.total_runoff_mm * 2.0
    assert bare.total_soil_evaporation_mm > covered.total_soil_evaporation_mm
    assert covered.evaporation_fraction < 0.6


def test_a_cropped_field_converts_more_water_into_transpiration(reg, monsoon):
    w, e = monsoon
    crop = run_water_balance(
        reg.crop("sorghum"), reg.texture("loam"), w, e, planting_index=MONSOON_SOWING
    )
    assert crop.evaporation_fraction < 0.35
    assert crop.total_transpiration_mm > crop.total_soil_evaporation_mm


# --- stress behaviour ------------------------------------------------------------


def test_drought_tolerant_crop_is_less_stressed_than_a_thirsty_one(reg, monsoon):
    w, e = monsoon
    millet = run_water_balance(
        reg.crop("pearl_millet"), reg.texture("loam"), w, e, planting_index=MONSOON_SOWING
    )
    cotton = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e, planting_index=MONSOON_SOWING
    )
    assert millet.relative_transpiration_deficit < cotton.relative_transpiration_deficit
    assert millet.stress_days < cotton.stress_days


def test_stress_coefficient_stays_at_one_until_readily_available_water_is_gone(
    reg, dry_constant
):
    w, e = dry_constant
    r = run_water_balance(
        reg.crop("maize"), reg.texture("loam"), w, e,
        initial_root_depletion_frac=0.0, planting_index=0,
    )
    # Ks is evaluated on the soil state at the start of the day, after roots have
    # deepened into any new (and partly depleted) subsoil, while depletion is
    # recorded at the end of the day. The start-of-day value is therefore not
    # reconstructable from the series alone, so assert the invariants that are:
    # the crop begins unstressed, every stressed day closes past readily available
    # water, and the two states never overlap.
    unstressed = r.ks >= 0.999
    assert unstressed[0]
    assert unstressed.any() and (~unstressed).any()
    assert np.all(r.root_depletion_mm[~unstressed] > r.raw_mm[~unstressed])
    assert np.all(r.ks[~unstressed] < 1.0)
    # With no rainfall the profile only dries, so stress onset is a single crossing.
    first_stress = int(np.argmax(~unstressed))
    assert np.all(unstressed[:first_stress])
    assert np.all(~unstressed[first_stress:])


def test_stress_coefficient_declines_as_the_profile_empties(reg, dry_constant):
    w, e = dry_constant
    r = run_water_balance(
        reg.crop("maize"), reg.texture("sand"), w, e,
        initial_root_depletion_frac=0.3, planting_index=0,
    )
    stressed_days = np.flatnonzero(r.ks < 0.999)
    assert len(stressed_days) > 10
    ks_run = r.ks[stressed_days]
    assert ks_run[0] > ks_run[-1]
    assert np.all((r.ks >= 0.0) & (r.ks <= 1.0))


def test_consecutive_stress_is_distinguished_from_scattered_stress(reg, dry_constant):
    """A single long dry spell does more damage than the same days spread out."""
    w, e = dry_constant
    r = run_water_balance(
        reg.crop("maize"), reg.texture("sand"), w, e, planting_index=0
    )
    assert r.max_consecutive_stress_days > 0
    assert r.max_consecutive_stress_days <= r.stress_days


def test_a_sandy_soil_stresses_a_crop_sooner_than_a_loam(reg, monsoon):
    w, e = monsoon
    sand = run_water_balance(
        reg.crop("maize"), reg.texture("sand"), w, e, planting_index=MONSOON_SOWING
    )
    loam = run_water_balance(
        reg.crop("maize"), reg.texture("loam"), w, e, planting_index=MONSOON_SOWING
    )
    assert sand.relative_transpiration_deficit > loam.relative_transpiration_deficit
    assert sand.total_deep_percolation_mm >= loam.total_deep_percolation_mm


def test_a_heavy_soil_sheds_more_rain_as_runoff(reg, monsoon):
    w, e = monsoon
    clay = run_water_balance(
        reg.crop("maize"), reg.texture("clay"), w, e, planting_index=MONSOON_SOWING
    )
    sand = run_water_balance(
        reg.crop("maize"), reg.texture("sand"), w, e, planting_index=MONSOON_SOWING
    )
    assert clay.runoff_fraction > sand.runoff_fraction


# --- irrigation ------------------------------------------------------------------


def test_rainfed_applies_nothing(reg, monsoon):
    w, e = monsoon
    r = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        planting_index=MONSOON_SOWING, irrigation=RAINFED,
    )
    assert r.total_irrigation_gross_mm == 0.0
    assert np.all(r.irrigation_applied_mm == 0.0)


def test_irrigation_removes_stress(reg, monsoon):
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING)
    rainfed = run_water_balance(reg.crop("cotton"), reg.texture("loam"), w, e, **kw)
    irrigated = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        irrigation=IrrigationPolicy(mode="refill_on_stress"), **kw,
    )
    assert irrigated.stress_days < rainfed.stress_days
    assert irrigated.relative_transpiration_deficit < 0.05
    assert irrigated.total_transpiration_mm > rainfed.total_transpiration_mm


def test_gross_irrigation_exceeds_net_by_the_efficiency(reg, monsoon):
    """The reported requirement is what the farmer must find, not what roots receive."""
    w, e = monsoon
    policy = IrrigationPolicy(mode="refill_on_stress", efficiency=0.5)
    r = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        planting_index=MONSOON_SOWING, irrigation=policy,
    )
    net = float(r.irrigation_applied_mm.sum())
    assert r.total_irrigation_gross_mm == pytest.approx(net / 0.5, rel=1e-9)


def test_earlier_trigger_applies_more_water(reg, monsoon):
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING)
    late = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        irrigation=IrrigationPolicy("refill_on_stress", trigger_depletion_frac=1.0), **kw,
    )
    early = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        irrigation=IrrigationPolicy("refill_on_stress", trigger_depletion_frac=0.5), **kw,
    )
    assert early.total_irrigation_gross_mm > late.total_irrigation_gross_mm


def test_seasonal_cap_binds_and_stress_returns(reg, monsoon):
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING)
    uncapped = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        irrigation=IrrigationPolicy("refill_on_stress"), **kw,
    )
    capped = run_water_balance(
        reg.crop("cotton"), reg.texture("loam"), w, e,
        irrigation=IrrigationPolicy("refill_on_stress", seasonal_cap_mm=80.0), **kw,
    )
    assert capped.total_irrigation_gross_mm <= 80.0 + 1e-9
    assert capped.total_irrigation_gross_mm < uncapped.total_irrigation_gross_mm
    assert capped.stress_days > uncapped.stress_days


def test_invalid_policies_are_rejected():
    with pytest.raises(WaterBalanceError, match="irrigation mode"):
        IrrigationPolicy(mode="flood_always")
    with pytest.raises(WaterBalanceError, match="efficiency"):
        IrrigationPolicy(mode="refill_on_stress", efficiency=0.0)
    with pytest.raises(WaterBalanceError, match="wetted fraction"):
        IrrigationPolicy(wetted_fraction=1.5)
    with pytest.raises(WaterBalanceError, match="refill_to_depletion_frac"):
        IrrigationPolicy(refill_to_depletion_frac=1.0)


# --- carry-over between seasons --------------------------------------------------


def test_starting_wetter_reduces_stress(reg, monsoon):
    w, e = monsoon
    kw = dict(planting_index=320)  # post-monsoon sowing, living on stored water
    dry_start = run_water_balance(
        reg.crop("chickpea"), reg.texture("clay_loam"), w, e,
        initial_root_depletion_frac=0.95, **kw,
    )
    wet_start = run_water_balance(
        reg.crop("chickpea"), reg.texture("clay_loam"), w, e,
        initial_root_depletion_frac=0.05, **kw,
    )
    assert wet_start.stress_days < dry_start.stress_days
    assert wet_start.total_transpiration_mm > dry_start.total_transpiration_mm


def test_closing_depletion_can_be_chained_into_the_next_season(reg, monsoon):
    """How the rotation simulator will carry stored water across a sequence."""
    w, e = monsoon
    texture = reg.texture("clay_loam")
    first = run_water_balance(
        reg.crop("sorghum"), texture, w, e, planting_index=MONSOON_SOWING
    )
    assert 0.0 <= first.final_root_depletion_mm <= first.taw_mm[-1] + 1e-9

    carried = first.final_root_depletion_mm / first.taw_mm[-1]
    second = run_water_balance(
        reg.crop("chickpea"), texture, w, e,
        planting_index=MONSOON_SOWING + 125,
        initial_root_depletion_frac=carried,
        initial_surface_depletion_frac=first.final_surface_depletion_mm
        / texture.tew_mm(0.10),
    )
    assert abs(second.closure_residual_mm) < 1e-6
    assert second.initial_root_depletion_mm > 0.0


def test_a_thirsty_crop_leaves_the_profile_drier_for_the_next_one(reg, monsoon):
    w, e = monsoon
    texture = reg.texture("clay_loam")
    kw = dict(planting_index=MONSOON_SOWING, duration_days=120)
    after_cotton = run_water_balance(reg.crop("cotton"), texture, w, e, **kw)
    after_millet = run_water_balance(reg.crop("pearl_millet"), texture, w, e, **kw)
    assert after_cotton.final_root_depletion_mm > after_millet.final_root_depletion_mm


def test_subsoil_depletion_assumption_changes_the_answer(reg, monsoon):
    """A named simplification with a real effect, so it must not be buried."""
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING)
    optimistic = run_water_balance(
        reg.crop("maize"), reg.texture("loam"), w, e,
        subsoil_depletion_frac=0.0, **kw,
    )
    pessimistic = run_water_balance(
        reg.crop("maize"), reg.texture("loam"), w, e,
        subsoil_depletion_frac=1.0, **kw,
    )
    assert optimistic.stress_days < pessimistic.stress_days
    assert abs(optimistic.closure_residual_mm) < 1e-6
    assert abs(pessimistic.closure_residual_mm) < 1e-6


# --- deep percolation and bounds -------------------------------------------------


def test_deep_percolation_only_occurs_when_the_profile_is_full(reg, monsoon):
    w, e = monsoon
    r = run_water_balance(
        reg.crop("rice_lowland"), reg.texture("clay"), w, e, planting_index=MONSOON_SOWING
    )
    draining = r.deep_percolation_mm > 0.0
    if draining.any():
        assert np.all(r.root_depletion_mm[draining] < 1e-6)


def test_depletion_never_leaves_physical_bounds(reg, monsoon):
    w, e = monsoon
    for cid in ("wheat", "maize", "cotton", "potato", "alfalfa"):
        r = run_water_balance(
            reg.crop(cid), reg.texture("sandy_loam"), w, e,
            planting_index=MONSOON_SOWING,
            duration_days=min(reg.crop(cid).duration_days, 300),
        )
        assert np.all(r.root_depletion_mm >= -1e-9), cid
        assert np.all(r.root_depletion_mm <= r.taw_mm + 1e-9), cid
        assert np.all(r.surface_depletion_mm >= -1e-9), cid
        assert np.all((r.ke >= 0.0) & (r.ke <= 1.4)), cid


def test_summary_exposes_the_decision_relevant_numbers(reg, monsoon):
    w, e = monsoon
    r = run_water_balance(
        reg.crop("sorghum"), reg.texture("loam"), w, e, planting_index=MONSOON_SOWING
    )
    s = r.summary()
    assert set(s) >= {
        "rainfall_mm", "transpiration_mm", "soil_evaporation_mm",
        "evaporation_fraction", "irrigation_gross_mm", "runoff_mm",
        "deep_percolation_mm", "relative_transpiration_deficit", "stress_days",
    }
    assert all(np.isfinite(v) for v in s.values())


# --- input validation ------------------------------------------------------------


def test_mismatched_et0_length_is_rejected(reg, monsoon):
    w, _ = monsoon
    with pytest.raises(WaterBalanceError, match="et0 series length"):
        run_water_balance(reg.crop("wheat"), reg.texture("loam"), w, np.zeros(5))


def test_fallow_requires_an_explicit_duration(reg, monsoon):
    w, e = monsoon
    with pytest.raises(WaterBalanceError, match="no intrinsic duration"):
        run_water_balance(reg.crop("fallow_bare"), reg.texture("loam"), w, e)


def test_season_running_past_the_series_is_rejected(reg, monsoon):
    w, e = monsoon
    with pytest.raises(WaterBalanceError, match="runs past"):
        run_water_balance(
            reg.crop("wheat"), reg.texture("loam"), w, e, planting_index=len(w) - 10
        )


def test_negative_planting_index_is_rejected(reg, monsoon):
    w, e = monsoon
    with pytest.raises(WaterBalanceError, match="runs past|positive"):
        run_water_balance(
            reg.crop("wheat"), reg.texture("loam"), w, e, planting_index=-5
        )


def test_out_of_range_subsoil_assumption_is_rejected(reg, monsoon):
    w, e = monsoon
    with pytest.raises(WaterBalanceError, match="subsoil_depletion_frac"):
        run_water_balance(
            reg.crop("wheat"), reg.texture("loam"), w, e, subsoil_depletion_frac=1.5
        )


# --- residue mulch: how one crop's residue pays forward to the next ------------


def test_residue_cover_reduces_soil_evaporation(reg, monsoon):
    """The mechanism by which a high-residue crop saves water for its successor."""
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING)
    bare = run_water_balance(reg.crop("maize"), reg.texture("silt_loam"), w, e, **kw)
    mulched = run_water_balance(
        reg.crop("maize"), reg.texture("silt_loam"), w, e,
        residue_cover_fraction=0.6, **kw,
    )
    assert mulched.total_soil_evaporation_mm < bare.total_soil_evaporation_mm
    assert any("residue cover" in n for n in mulched.notes)

    # FAO-56 suppresses the evaporation RATE by 5% per 10% cover, so 60% cover cuts
    # the daily rate by 30%. The SEASONAL total falls by less than that, and the
    # reason is a real feedback rather than an error: slower evaporation leaves the
    # surface layer wetter, so Kr stays higher for longer and some of the suppressed
    # water evaporates later instead of never. The cumulative effect is therefore
    # sub-proportional to the rate effect.
    ratio = mulched.total_soil_evaporation_mm / bare.total_soil_evaporation_mm
    assert 0.70 < ratio < 0.90, ratio
    assert ratio > 0.70, "a 30% rate cut cannot remove more than 30% of the total"


def test_residue_cover_leaves_more_water_for_transpiration(reg, monsoon):
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING, crop=reg.crop("maize"),
              texture=reg.texture("silt_loam"), weather=w, et0_mm=e)
    bare = run_water_balance(**kw)
    mulched = run_water_balance(residue_cover_fraction=0.6, **kw)
    assert mulched.total_transpiration_mm >= bare.total_transpiration_mm
    assert mulched.evaporation_fraction < bare.evaporation_fraction


def test_mulch_effect_is_monotonic_and_bounded(reg, monsoon):
    """More cover always means less evaporation, but never less than half.

    Full cover halves the evaporation RATE; because of the Kr feedback the seasonal
    total falls by somewhat less, so the cumulative ratio stays above 0.5.
    """
    w, e = monsoon
    kw = dict(planting_index=MONSOON_SOWING)
    totals = [
        run_water_balance(
            reg.crop("maize"), reg.texture("loam"), w, e,
            residue_cover_fraction=f, **kw,
        ).total_soil_evaporation_mm
        for f in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert all(a > b for a, b in zip(totals, totals[1:])), totals
    assert 0.5 < totals[-1] / totals[0] < 0.75


def test_residue_mulch_preserves_mass_balance(reg, monsoon):
    w, e = monsoon
    r = run_water_balance(
        reg.crop("maize"), reg.texture("silt_loam"), w, e,
        planting_index=MONSOON_SOWING, residue_cover_fraction=0.7,
    )
    assert abs(r.closure_residual_mm) < 1e-6


def test_out_of_range_residue_cover_is_rejected(reg, monsoon):
    w, e = monsoon
    with pytest.raises(WaterBalanceError, match="residue_cover_fraction"):
        run_water_balance(
            reg.crop("maize"), reg.texture("loam"), w, e, residue_cover_fraction=1.5
        )


# --- irrigation management depth ------------------------------------------------


def test_scheduling_uses_the_design_profile_not_the_seedling_root_zone(reg, monsoon):
    """Triggering on the instantaneous root zone irrigates a bare seedbed on day 3.

    Three days after sowing roots hold ~20 mm, so any depletion threshold is crossed
    at once. Managing against the design root depth is what real irrigators do, and
    it removed ~50 mm of spurious early irrigation in the Finney County run.
    """
    w, e = monsoon
    shallow = IrrigationPolicy(
        mode="refill_on_stress", efficiency=0.85, management_depth_m=0.15
    )
    design = IrrigationPolicy(mode="refill_on_stress", efficiency=0.85)  # defaults to max root depth

    kw = dict(planting_index=MONSOON_SOWING, duration_days=150)
    a = run_water_balance(reg.crop("maize"), reg.texture("silt_loam"), w, e,
                          irrigation=shallow, **kw)
    b = run_water_balance(reg.crop("maize"), reg.texture("silt_loam"), w, e,
                          irrigation=design, **kw)

    first_a = int(np.flatnonzero(a.irrigation_applied_mm > 0)[0])
    first_b = int(np.flatnonzero(b.irrigation_applied_mm > 0)[0])
    assert first_b > first_a
    assert b.total_soil_evaporation_mm < a.total_soil_evaporation_mm


def test_management_depth_defaults_to_max_rooting_depth(reg, monsoon):
    w, e = monsoon
    explicit = IrrigationPolicy(
        mode="refill_on_stress", management_depth_m=reg.crop("maize").root.max_depth_m
    )
    default = IrrigationPolicy(mode="refill_on_stress")
    kw = dict(planting_index=MONSOON_SOWING, duration_days=150)
    a = run_water_balance(reg.crop("maize"), reg.texture("loam"), w, e,
                          irrigation=explicit, **kw)
    b = run_water_balance(reg.crop("maize"), reg.texture("loam"), w, e,
                          irrigation=default, **kw)
    assert a.total_irrigation_gross_mm == pytest.approx(b.total_irrigation_gross_mm)


def test_non_positive_management_depth_is_rejected():
    with pytest.raises(WaterBalanceError, match="management_depth_m"):
        IrrigationPolicy(mode="refill_on_stress", management_depth_m=0.0)

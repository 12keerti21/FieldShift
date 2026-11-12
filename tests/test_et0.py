"""FAO-56 reference evapotranspiration.

Three layers of checking:

1. Sub-functions against published reference values I can verify independently -
   the saturation vapour pressure table, the psychrometric constant, and
   extraterrestrial radiation against FAO-56 Annex 2 Table 2.6.
2. Physical invariants and sensitivity signs: ET0 must respond to temperature,
   wind, humidity and season in the right direction and by a sane magnitude.
3. Cross-method agreement between Penman-Monteith and Hargreaves-Samani, which
   catches gross errors that a one-method test cannot see.

Reproducing the FAO-56 worked examples line by line needs the publication's
intermediate values, which are not available offline - that remains an open
validation gap, recorded in docs/DECISIONS.md.
"""

import numpy as np
import pytest

from rdss.agro.climate import DailyWeather, Site
from rdss.agro.et0 import (
    ET0Method,
    atmospheric_pressure,
    clear_sky_radiation,
    et0_hargreaves,
    et0_penman_monteith,
    extraterrestrial_radiation,
    method_agreement,
    net_radiation,
    psychrometric_constant,
    saturation_vapour_pressure,
    solar_from_temperature_range,
    svp_slope,
    wind_to_2m,
)


def _weather(n=365, start="2020-01-01", tmax=33.0, tmin=19.0, **over):
    dates = np.arange(np.datetime64(start, "D"), np.datetime64(start, "D") + n)
    base = dict(
        dates=dates,
        tmax_c=np.full(n, float(tmax)),
        tmin_c=np.full(n, float(tmin)),
        precip_mm=np.zeros(n),
    )
    base.update(over)
    return DailyWeather(**base)


SEMI_ARID = Site(13.0, 77.6, 900.0, "semi-arid tropical")


# --- 1. sub-functions against published values ----------------------------------


@pytest.mark.parametrize(
    "t_c,expected", [(0.0, 0.6108), (10.0, 1.228), (20.0, 2.338), (25.0, 3.168), (30.0, 4.243)]
)
def test_saturation_vapour_pressure_matches_fao56_table(t_c, expected):
    assert saturation_vapour_pressure(t_c) == pytest.approx(expected, abs=1e-3)


def test_saturation_vapour_pressure_is_monotonic_and_convex():
    t = np.linspace(-5, 45, 51)
    e = saturation_vapour_pressure(t)
    assert np.all(np.diff(e) > 0)
    assert np.all(np.diff(e, 2) > 0)  # convex: slope itself increases with temperature


def test_svp_slope_matches_fao56_table():
    assert svp_slope(20.0) == pytest.approx(0.145, abs=2e-3)
    assert svp_slope(30.0) == pytest.approx(0.243, abs=3e-3)


def test_atmospheric_pressure_and_psychrometric_constant_at_sea_level():
    assert atmospheric_pressure(0.0) == pytest.approx(101.3, abs=0.01)
    assert psychrometric_constant(0.0) == pytest.approx(0.0674, abs=1e-4)


def test_pressure_falls_with_elevation():
    assert atmospheric_pressure(2000.0) == pytest.approx(79.8, abs=0.5)
    assert atmospheric_pressure(2000.0) < atmospheric_pressure(0.0)
    assert psychrometric_constant(2000.0) < psychrometric_constant(0.0)


def test_extraterrestrial_radiation_matches_fao56_annex2():
    """FAO-56 Annex 2 Table 2.6: Ra at the equator in mid-January is about 36.2."""
    ra = extraterrestrial_radiation(0.0, np.array([15]))
    assert float(ra[0]) == pytest.approx(36.2, abs=0.3)


def test_extraterrestrial_radiation_at_20n_in_january():
    ra = extraterrestrial_radiation(np.deg2rad(20.0), np.array([15]))
    assert float(ra[0]) == pytest.approx(26.8, abs=0.4)


def test_polar_night_returns_zero_not_nan():
    """The sunset-hour-angle argument leaves [-1,1] inside the polar circles."""
    ra = extraterrestrial_radiation(np.deg2rad(75.0), np.array([15]))
    assert float(ra[0]) == 0.0
    assert np.isfinite(ra).all()


def test_polar_day_is_large_and_finite():
    ra = extraterrestrial_radiation(np.deg2rad(75.0), np.array([180]))
    assert 35.0 < float(ra[0]) < 50.0


def test_radiation_is_hemispherically_symmetric_apart_from_eccentricity():
    """Mid-January in the north matches mid-July in the south, but not exactly.

    The solar-geometry terms are symmetric; the inverse-square distance term is not.
    Perihelion falls in early January, so the northern winter receives about 7 percent
    more top-of-atmosphere radiation than the matching southern winter. The residual
    is real orbital physics and the test pins its sign and size rather than
    pretending it away.
    """
    north = float(extraterrestrial_radiation(np.deg2rad(45.0), np.array([15]))[0])
    south = float(extraterrestrial_radiation(np.deg2rad(-45.0), np.array([15 + 182]))[0])
    assert north == pytest.approx(south, rel=0.08)
    assert north > south  # perihelion in January


def test_equatorial_radiation_peaks_near_the_equinoxes():
    doy = np.arange(1, 366)
    ra = extraterrestrial_radiation(0.0, doy)
    peak = int(doy[np.argmax(ra)])
    assert min(abs(peak - 80), abs(peak - 264)) < 15


def test_clear_sky_radiation_is_a_fraction_of_extraterrestrial():
    ra = extraterrestrial_radiation(np.deg2rad(13.0), np.array([100]))
    rso_sea = clear_sky_radiation(ra, 0.0)
    rso_high = clear_sky_radiation(ra, 3000.0)
    assert float(rso_sea[0]) == pytest.approx(0.75 * float(ra[0]))
    assert float(rso_high[0]) > float(rso_sea[0])  # thinner atmosphere transmits more
    assert np.all(rso_high < ra)


def test_estimated_solar_radiation_responds_to_diurnal_range():
    ra = extraterrestrial_radiation(np.deg2rad(13.0), np.array([100, 100]))
    clear = solar_from_temperature_range(ra, np.array([16.0, 16.0]))
    cloudy = solar_from_temperature_range(ra, np.array([3.0, 3.0]))
    assert np.all(clear > cloudy)
    zero = solar_from_temperature_range(ra, np.array([0.0, 0.0]))
    assert np.allclose(zero, 0.0)


def test_net_radiation_is_less_than_absorbed_shortwave():
    """Longwave loss to the sky is always a debit."""
    rs = np.array([22.0])
    rso = np.array([26.0])
    rn = net_radiation(rs, rso, np.array([33.0]), np.array([19.0]), np.array([1.6]))
    assert 0.0 < float(rn[0]) < 0.77 * 22.0


def test_net_radiation_survives_polar_night_without_dividing_by_zero():
    rn = net_radiation(
        np.array([0.0]), np.array([0.0]), np.array([-20.0]), np.array([-30.0]),
        np.array([0.05]),
    )
    assert np.isfinite(rn).all()
    assert float(rn[0]) < 0.0  # net loss, as it must be with no incoming sun


def test_wind_adjustment_to_two_metres():
    assert wind_to_2m(3.0, 2.0) == 3.0
    assert wind_to_2m(3.0, 10.0) < 3.0  # wind is faster aloft
    assert wind_to_2m(3.0, 10.0) == pytest.approx(3.0 * 4.87 / np.log(67.8 * 10 - 5.42))
    with pytest.raises(ValueError):
        wind_to_2m(3.0, 0.0)


# --- 2. physical invariants and sensitivities -----------------------------------


def test_et0_is_positive_and_in_a_plausible_range():
    r = et0_penman_monteith(SEMI_ARID, _weather())
    assert r.method is ET0Method.PENMAN_MONTEITH
    assert np.all(r.values_mm >= 0.0)
    assert np.all(r.values_mm < 15.0)
    assert 3.0 < float(r.values_mm.mean()) < 8.0


def test_et0_rises_with_temperature():
    cool = et0_penman_monteith(SEMI_ARID, _weather(tmax=25.0, tmin=15.0))
    hot = et0_penman_monteith(SEMI_ARID, _weather(tmax=40.0, tmin=26.0))
    assert hot.values_mm.mean() > cool.values_mm.mean()


def test_et0_rises_with_wind_when_the_air_is_dry():
    n = 90
    calm = et0_penman_monteith(
        SEMI_ARID, _weather(n, rh_mean_pct=np.full(n, 30.0), wind_2m_ms=np.full(n, 0.5))
    )
    windy = et0_penman_monteith(
        SEMI_ARID, _weather(n, rh_mean_pct=np.full(n, 30.0), wind_2m_ms=np.full(n, 6.0))
    )
    assert windy.values_mm.mean() > calm.values_mm.mean() * 1.2


def test_et0_falls_as_humidity_rises():
    n = 90
    dry = et0_penman_monteith(SEMI_ARID, _weather(n, rh_mean_pct=np.full(n, 25.0)))
    humid = et0_penman_monteith(SEMI_ARID, _weather(n, rh_mean_pct=np.full(n, 90.0)))
    assert humid.values_mm.mean() < dry.values_mm.mean()


def test_saturated_air_does_not_produce_a_negative_aerodynamic_term():
    """Estimated vapour pressure can exceed saturation; ET0 must not go backwards."""
    n = 30
    r = et0_penman_monteith(SEMI_ARID, _weather(n, rh_mean_pct=np.full(n, 100.0)))
    assert np.all(r.values_mm >= 0.0)
    assert np.all(np.isfinite(r.values_mm))


def test_et0_follows_the_seasons_at_mid_latitude():
    site = Site(45.0, 5.0, 200.0, "temperate north")
    w = _weather(365, tmax=25.0, tmin=10.0)
    r = et0_penman_monteith(site, w)
    winter = r.values_mm[(w.month == 1)].mean()
    summer = r.values_mm[(w.month == 7)].mean()
    assert summer > winter * 2.0


def test_southern_hemisphere_seasons_are_inverted():
    w = _weather(365, tmax=25.0, tmin=10.0)
    north = et0_penman_monteith(Site(35.0, 0.0, 100.0), w)
    south = et0_penman_monteith(Site(-35.0, 0.0, 100.0), w)
    jan = w.month == 1
    assert south.values_mm[jan].mean() > north.values_mm[jan].mean()


def test_polar_night_et0_is_near_zero_but_stays_positive():
    """With no sun the energy term is negative, yet dry air still drives a trickle.

    Worth pinning: the aerodynamic term is what keeps ET0 positive here, so a model
    that dropped it would read as plausible in the tropics and fail at high latitude.
    """
    site = Site(68.0, 20.0, 100.0, "arctic")
    w = _weather(60, start="2020-12-01", tmax=-15.0, tmin=-25.0)
    r = et0_penman_monteith(site, w)
    assert np.all(r.values_mm >= 0.0)
    assert float(r.values_mm.mean()) < 0.5


def test_negative_et0_is_clipped_and_the_clipping_is_reported():
    """Saturated air removes the aerodynamic term, leaving only net longwave loss."""
    site = Site(68.0, 20.0, 100.0, "arctic")
    n = 60
    w = _weather(
        n, start="2020-12-01", tmax=-15.0, tmin=-25.0, rh_mean_pct=np.full(n, 100.0)
    )
    r = et0_penman_monteith(site, w)
    assert np.all(r.values_mm == 0.0)
    assert any("negative ET0" in note for note in r.notes)


def test_rh_extremes_are_preferred_over_rh_mean_but_agree_closely():
    n = 60
    extremes = et0_penman_monteith(
        SEMI_ARID,
        _weather(n, rh_max_pct=np.full(n, 85.0), rh_min_pct=np.full(n, 35.0)),
    )
    mean_only = et0_penman_monteith(SEMI_ARID, _weather(n, rh_mean_pct=np.full(n, 60.0)))
    assert extremes.values_mm.mean() == pytest.approx(
        mean_only.values_mm.mean(), rel=0.15
    )
    assert "humidity" not in extremes.substitutions
    assert "humidity" not in mean_only.substitutions


def test_measured_radiation_is_used_in_preference_to_the_estimate():
    n = 60
    measured = _weather(n, solar_mj_m2=np.full(n, 23.0))
    r = et0_penman_monteith(SEMI_ARID, measured)
    assert "solar_mj_m2" not in r.substitutions
    estimated = et0_penman_monteith(SEMI_ARID, _weather(n))
    assert "solar_mj_m2" in estimated.substitutions
    assert not np.allclose(r.values_mm, estimated.values_mm)


def test_elevation_changes_et0():
    n = 60
    low = et0_penman_monteith(Site(13.0, 77.6, 50.0), _weather(n))
    high = et0_penman_monteith(Site(13.0, 77.6, 3000.0), _weather(n))
    assert not np.isclose(low.values_mm.mean(), high.values_mm.mean())


# --- substitution accounting ----------------------------------------------------


def test_fully_measured_inputs_record_nothing_and_rate_high_confidence():
    n = 60
    w = _weather(
        n,
        rh_max_pct=np.full(n, 85.0),
        rh_min_pct=np.full(n, 35.0),
        wind_2m_ms=np.full(n, 2.2),
        solar_mj_m2=np.full(n, 22.0),
    )
    r = et0_penman_monteith(SEMI_ARID, w)
    assert r.substitutions == {}
    assert r.confidence == "high"
    assert "all inputs measured" in r.describe()


def test_estimated_radiation_downgrades_confidence_to_medium():
    n = 60
    w = _weather(n, rh_mean_pct=np.full(n, 60.0), wind_2m_ms=np.full(n, 2.2))
    r = et0_penman_monteith(SEMI_ARID, w)
    assert set(r.substitutions) == {"solar_mj_m2"}
    assert r.confidence == "medium"


def test_temperature_only_input_downgrades_confidence_to_low():
    r = et0_penman_monteith(SEMI_ARID, _weather(60))
    assert set(r.substitutions) == {"solar_mj_m2", "humidity", "wind_2m_ms"}
    assert r.confidence == "low"
    assert any("Radiation was estimated" in n for n in r.notes)
    assert "FAO-56 eq 50" in r.describe()


def test_arid_dewpoint_offset_lowers_assumed_humidity_and_raises_et0():
    """Assuming overnight saturation is wrong in arid climates; the offset corrects it."""
    n = 60
    humid_assumption = et0_penman_monteith(SEMI_ARID, _weather(n))
    arid_assumption = et0_penman_monteith(
        SEMI_ARID, _weather(n), arid_dewpoint_offset_c=3.0
    )
    assert arid_assumption.values_mm.mean() > humid_assumption.values_mm.mean()
    assert "arid adjustment" in arid_assumption.substitutions["humidity"]


def test_wind_measured_at_ten_metres_is_adjusted_not_used_raw():
    n = 60
    w = _weather(n, wind_2m_ms=np.full(n, 5.0))
    at_2m = et0_penman_monteith(SEMI_ARID, w)
    at_10m = et0_penman_monteith(SEMI_ARID, w, wind_measurement_height_m=10.0)
    assert at_10m.values_mm.mean() < at_2m.values_mm.mean()
    assert "wind_2m_ms" not in at_10m.substitutions


def test_coastal_krs_gives_more_radiation_than_interior():
    n = 60
    interior = et0_penman_monteith(SEMI_ARID, _weather(n), krs=0.16)
    coastal = et0_penman_monteith(SEMI_ARID, _weather(n), krs=0.19)
    assert coastal.values_mm.mean() > interior.values_mm.mean()


# --- 3. cross-method agreement ---------------------------------------------------


def test_hargreaves_runs_on_temperature_alone():
    r = et0_hargreaves(SEMI_ARID, _weather(365))
    assert r.method is ET0Method.HARGREAVES
    assert np.all(r.values_mm >= 0.0)
    assert 2.0 < float(r.values_mm.mean()) < 9.0
    assert "cross-check" in " ".join(r.notes)


def test_the_two_methods_agree_within_a_quarter():
    """Gross errors in either implementation show up here."""
    w = _weather(365)
    pm = et0_penman_monteith(SEMI_ARID, w)
    hg = et0_hargreaves(SEMI_ARID, w)
    stats = method_agreement(pm, hg)
    assert abs(stats["relative_bias"]) < 0.25
    assert stats["mean_absolute_difference_mm"] < 1.5


def test_methods_agree_across_contrasting_climates():
    cases = [
        (Site(13.0, 77.6, 900.0), 33.0, 19.0),
        (Site(45.0, 5.0, 200.0), 22.0, 9.0),
        (Site(-25.0, 135.0, 300.0), 36.0, 20.0),
        (Site(28.0, 77.0, 220.0), 40.0, 27.0),
    ]
    for site, tmax, tmin in cases:
        w = _weather(365, tmax=tmax, tmin=tmin)
        stats = method_agreement(
            et0_penman_monteith(site, w), et0_hargreaves(site, w)
        )
        assert abs(stats["relative_bias"]) < 0.35, (site, stats)


def test_method_agreement_rejects_mismatched_lengths():
    pm = et0_penman_monteith(SEMI_ARID, _weather(60))
    hg = et0_hargreaves(SEMI_ARID, _weather(30))
    with pytest.raises(ValueError, match="lengths differ"):
        method_agreement(pm, hg)


def test_result_length_tracks_the_input_series():
    assert len(et0_penman_monteith(SEMI_ARID, _weather(123))) == 123

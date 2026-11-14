"""NASA POWER adapter.

Tested against a real recorded response (tests/fixtures/power_finney_40d.json)
rather than only hand-written payloads, because real responses carry the actual key
shapes, unit strings and fill values that invented fixtures drift away from.

The behaviour that matters most here is refusal. POWER's solar record starts years
after its meteorology, and silently interpolating across a three-year hole would
have produced a plausible, invented 1981-1983 water balance. That refusal is the
reason the gap was discovered at all.
"""

import copy
import json
import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import Site
from fieldshift.io.power import (
    PARAMETER_MAP,
    PowerError,
    parse_power_daily,
    power_request_url,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "power_finney_40d.json"
SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


def _blank(payload, name, keys):
    for k in keys:
        payload["properties"]["parameter"][name][k] = payload["header"]["fill_value"]


# --- the real response parses -----------------------------------------------------


def test_real_response_parses_into_the_contract(payload):
    w, report = parse_power_daily(payload, site=SITE)
    assert len(w) == 40
    assert str(w.dates[0]) == "2020-06-01"
    assert str(w.dates[-1]) == "2020-07-10"
    # All six contract variables present means ET0 needs no substitutions at all.
    assert w.available() == {"rh_mean_pct", "wind_2m_ms", "solar_mj_m2"}
    assert report.fills == ()
    assert report.confidence == "high"
    assert set(report.variables_present) == set(PARAMETER_MAP)


def test_values_are_physically_plausible_for_a_kansas_summer(payload):
    w, _ = parse_power_daily(payload, site=SITE)
    assert 20.0 < w.tmax_c.mean() < 42.0
    assert 5.0 < w.tmin_c.mean() < 25.0
    assert np.all(w.tmax_c > w.tmin_c)
    assert np.all(w.precip_mm >= 0.0)
    assert 10.0 < w.solar_mj_m2.mean() < 35.0
    assert 0.0 < w.wind_2m_ms.mean() < 12.0


def test_provenance_names_the_power_parameter_and_api_version(payload):
    w, report = parse_power_daily(payload, site=SITE)
    assert "PRECTOTCORR" in w.source_of("precip_mm")
    assert "NASA POWER" in w.source_of("tmax_c")
    assert report.api_version.startswith("v")
    assert "NASA POWER" in report.describe()


def test_site_is_recovered_from_geometry_including_elevation(payload):
    w, report = parse_power_daily(payload)  # no explicit site
    assert report.site.latitude_deg == pytest.approx(38.03)
    assert report.site.longitude_deg == pytest.approx(-100.74)
    assert report.site.elevation_m > 500.0  # POWER returns it as the third coordinate


def test_missing_geometry_demands_an_explicit_site(payload):
    payload.pop("geometry")
    with pytest.raises(PowerError, match="elevation"):
        parse_power_daily(payload)


# --- refusal behaviour: the part that found the real bug -------------------------


def test_a_long_gap_is_refused_rather_than_interpolated(payload):
    """The guard that discovered POWER's solar record starting in 1984, not 1981."""
    keys = sorted(payload["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"])
    _blank(payload, "ALLSKY_SFC_SW_DWN", keys[5:25])
    with pytest.raises(PowerError, match="beyond max_gap_days"):
        parse_power_daily(payload, site=SITE, max_gap_days=10)


def test_short_gaps_are_interpolated_and_recorded(payload):
    keys = sorted(payload["properties"]["parameter"]["RH2M"])
    _blank(payload, "RH2M", keys[10:13])
    w, report = parse_power_daily(payload, site=SITE)
    assert len(report.fills) == 1
    fill = report.fills[0]
    assert fill.variable == "rh_mean_pct"
    assert fill.filled_days == 3
    assert fill.longest_gap_days == 3
    assert "interpolation" in fill.method
    assert np.isfinite(w.rh_mean_pct).all()
    assert "gap-filled" in report.describe()


def test_fill_strategy_fail_refuses_any_gap(payload):
    keys = sorted(payload["properties"]["parameter"]["WS2M"])
    _blank(payload, "WS2M", keys[4:6])
    with pytest.raises(PowerError, match="fill_strategy='fail'"):
        parse_power_daily(payload, site=SITE, fill_strategy="fail")


def test_too_much_filling_is_refused(payload):
    keys = sorted(payload["properties"]["parameter"]["RH2M"])
    _blank(payload, "RH2M", keys[0:8])
    with pytest.raises(PowerError, match="max_filled_fraction"):
        parse_power_daily(payload, site=SITE, max_gap_days=20, max_filled_fraction=0.10)


def test_an_entirely_missing_optional_is_dropped_not_filled(payload):
    keys = sorted(payload["properties"]["parameter"]["WS2M"])
    _blank(payload, "WS2M", keys)
    w, report = parse_power_daily(payload, site=SITE)
    assert w.wind_2m_ms is None
    assert "WS2M" in report.variables_absent
    assert report.fills == ()  # dropping is not filling


def test_a_missing_required_parameter_is_refused(payload):
    del payload["properties"]["parameter"]["PRECTOTCORR"]
    with pytest.raises(PowerError, match="requires"):
        parse_power_daily(payload, site=SITE)


def test_confidence_degrades_with_filling(payload):
    heavy = copy.deepcopy(payload)
    keys = sorted(heavy["properties"]["parameter"]["RH2M"])
    _blank(heavy, "RH2M", keys[5:8])  # 3 of 40 days = 7.5%, under the 10% refusal
    _, report = parse_power_daily(heavy, site=SITE, max_gap_days=10)
    assert report.confidence == "low"  # >5% of a variable reconstructed
    assert report.total_filled_days == 3


# --- units -----------------------------------------------------------------------


def test_radiation_in_kilowatt_hours_is_converted(payload):
    """POWER endpoints differ on radiation units; guessing is not acceptable."""
    base, _ = parse_power_daily(copy.deepcopy(payload), site=SITE)
    payload["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "kW-hr/m^2/day"
    series = payload["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
    for k in series:
        series[k] = series[k] / 3.6
    converted, report = parse_power_daily(payload, site=SITE)
    assert np.allclose(converted.solar_mj_m2, base.solar_mj_m2, rtol=1e-6)
    assert any("ALLSKY_SFC_SW_DWN" in c for c in report.unit_conversions)


def test_temperature_in_kelvin_is_converted(payload):
    payload["parameters"]["T2M_MAX"]["units"] = "K"
    series = payload["properties"]["parameter"]["T2M_MAX"]
    for k in series:
        series[k] = series[k] + 273.15
    w, report = parse_power_daily(payload, site=SITE)
    assert 20.0 < w.tmax_c.mean() < 42.0
    assert any("T2M_MAX" in c for c in report.unit_conversions)


def test_unrecognised_units_are_refused_not_guessed(payload):
    payload["parameters"]["T2M_MAX"]["units"] = "degrees Rankine"
    with pytest.raises(PowerError, match="refusing to guess"):
        parse_power_daily(payload, site=SITE)


# --- malformed input --------------------------------------------------------------


def test_a_non_power_payload_is_rejected_clearly():
    with pytest.raises(PowerError, match="does not look like"):
        parse_power_daily({"hello": "world"})


def test_an_empty_parameter_block_is_rejected():
    with pytest.raises(PowerError, match="no parameters"):
        parse_power_daily({"properties": {"parameter": {}}})


def test_unknown_fill_strategy_is_rejected(payload):
    with pytest.raises(PowerError, match="unknown fill strategy"):
        parse_power_daily(payload, site=SITE, fill_strategy="magic")


def test_a_day_omitted_entirely_becomes_an_explicit_gap(payload):
    """POWER skipping a date must not silently shorten the series."""
    for name in payload["properties"]["parameter"]:
        payload["properties"]["parameter"][name].pop("20200615", None)
    w, report = parse_power_daily(payload, site=SITE)
    assert len(w) == 40  # the date axis is rebuilt, not truncated
    assert report.total_filled_days >= 3


# --- request construction ---------------------------------------------------------


def test_request_url_is_reproducible_and_citable():
    url = power_request_url(SITE, "1984-01-01", "2024-12-31")
    assert url.startswith("https://power.larc.nasa.gov/api/temporal/daily/point?")
    assert "start=19840101" in url and "end=20241231" in url
    assert "latitude=38.03000" in url and "longitude=-100.74000" in url
    assert "community=AG" in url
    for p in PARAMETER_MAP:
        assert p in url

"""The weather contract every model depends on.

Strictness here is deliberate. A water balance carries soil moisture from one day
to the next, so a silently dropped day does not cost one day of accuracy - it
corrupts every day after it. These tests pin the failure modes that would
otherwise surface as a plausible-looking but wrong seasonal water deficit.
"""

import numpy as np
import pytest

from fieldshift.agro.climate import DailyWeather, Site, WeatherError


def _dates(start: str, n: int) -> np.ndarray:
    return np.arange(np.datetime64(start, "D"), np.datetime64(start, "D") + n)


def _weather(n: int = 30, start: str = "2020-01-01", **over) -> DailyWeather:
    base = dict(
        dates=_dates(start, n),
        tmax_c=np.full(n, 30.0),
        tmin_c=np.full(n, 18.0),
        precip_mm=np.zeros(n),
    )
    base.update(over)
    return DailyWeather(**base)


# --- site ------------------------------------------------------------------------


def test_site_accepts_plausible_locations():
    s = Site(13.0, 77.6, 900.0, "Bengaluru")
    assert s.latitude_rad == pytest.approx(np.deg2rad(13.0))


@pytest.mark.parametrize(
    "lat,lon,elev", [(95.0, 0.0, 0.0), (0.0, 200.0, 0.0), (0.0, 0.0, 12000.0)]
)
def test_site_rejects_impossible_coordinates(lat, lon, elev):
    with pytest.raises(WeatherError):
        Site(lat, lon, elev)


def test_site_allows_below_sea_level():
    """Dead Sea and Turfan basin agriculture exist; -500 m is the floor, not zero."""
    assert Site(31.5, 35.5, -400.0).elevation_m == -400.0


# --- contract enforcement --------------------------------------------------------


def test_gap_in_the_series_is_rejected():
    dates = np.concatenate([_dates("2020-01-01", 10), _dates("2020-01-15", 10)])
    with pytest.raises(WeatherError, match="gap-free"):
        DailyWeather(
            dates=dates,
            tmax_c=np.full(20, 30.0),
            tmin_c=np.full(20, 18.0),
            precip_mm=np.zeros(20),
        )


def test_non_finite_values_are_rejected_with_a_pointer_to_the_adapter():
    tmax = np.full(10, 30.0)
    tmax[4] = np.nan
    with pytest.raises(WeatherError, match="adapter"):
        _weather(10, tmax_c=tmax)


def test_fill_values_must_not_slip_through_as_numbers():
    """NASA products use sentinels like -9999; those are not caught by a NaN check."""
    tmin = np.full(10, 18.0)
    tmin[2] = -9999.0
    with pytest.raises(WeatherError, match="terrestrial records"):
        _weather(10, tmin_c=tmin)


def test_tmax_below_tmin_is_rejected():
    with pytest.raises(WeatherError, match="tmax below tmin"):
        _weather(10, tmax_c=np.full(10, 10.0), tmin_c=np.full(10, 20.0))


def test_negative_precipitation_is_rejected():
    p = np.zeros(10)
    p[3] = -1.0
    with pytest.raises(WeatherError, match="negative precipitation"):
        _weather(10, precip_mm=p)


def test_humidity_bounds_are_enforced():
    with pytest.raises(WeatherError, match="rh_mean_pct"):
        _weather(10, rh_mean_pct=np.full(10, 140.0))


def test_inverted_humidity_extremes_are_rejected():
    with pytest.raises(WeatherError, match="rh_max below rh_min"):
        _weather(10, rh_max_pct=np.full(10, 30.0), rh_min_pct=np.full(10, 80.0))


def test_wrong_date_dtype_is_rejected():
    with pytest.raises(WeatherError, match="datetime64"):
        DailyWeather(
            dates=np.array(["2020-01-01", "2020-01-02"], dtype="datetime64[ns]"),
            tmax_c=np.full(2, 30.0),
            tmin_c=np.full(2, 18.0),
            precip_mm=np.zeros(2),
        )


def test_mismatched_array_length_is_rejected():
    with pytest.raises(WeatherError, match="shape"):
        _weather(10, precip_mm=np.zeros(9))


def test_empty_series_is_rejected():
    with pytest.raises(WeatherError, match="empty"):
        DailyWeather(
            dates=np.array([], dtype="datetime64[D]"),
            tmax_c=np.array([]),
            tmin_c=np.array([]),
            precip_mm=np.array([]),
        )


def test_solar_radiation_bound_is_physical():
    with pytest.raises(WeatherError, match="solar"):
        _weather(10, solar_mj_m2=np.full(10, 80.0))


# --- derived quantities ----------------------------------------------------------


def test_tmean_follows_fao56_definition():
    """FAO-56 uses the mean of max and min, not a mean of hourly values."""
    w = _weather(5, tmax_c=np.full(5, 30.0), tmin_c=np.full(5, 10.0))
    assert np.allclose(w.tmean_c, 20.0)
    assert np.allclose(w.trange_c, 20.0)


def test_day_of_year_handles_leap_years():
    w = _weather(366, start="2020-01-01")
    assert w.doy[0] == 1
    assert w.doy[59] == 60  # 29 February 2020
    assert w.doy[-1] == 366
    non_leap = _weather(365, start="2021-01-01")
    assert non_leap.doy[-1] == 365


def test_month_and_year_extraction():
    w = _weather(400, start="2020-12-01")
    assert w.month[0] == 12
    assert w.year[0] == 2020
    assert w.month[31] == 1
    assert w.year[31] == 2021


def test_available_reports_only_present_optionals():
    w = _weather(10)
    assert w.available() == set()
    w2 = _weather(10, wind_2m_ms=np.full(10, 2.0), rh_mean_pct=np.full(10, 60.0))
    assert w2.available() == {"wind_2m_ms", "rh_mean_pct"}


def test_provenance_is_queryable_and_defaults_to_unrecorded():
    w = _weather(10, provenance={"precip_mm": "GPM IMERG"})
    assert w.source_of("precip_mm") == "GPM IMERG"
    assert w.source_of("tmax_c") == "unrecorded"


# --- slicing ---------------------------------------------------------------------


def test_between_is_inclusive_and_preserves_optionals_and_provenance():
    w = _weather(
        100, wind_2m_ms=np.arange(100.0), provenance={"tmax_c": "NASA POWER"}
    )
    s = w.between("2020-01-10", "2020-01-20")
    assert len(s) == 11
    assert str(s.dates[0]) == "2020-01-10"
    assert str(s.dates[-1]) == "2020-01-20"
    assert s.wind_2m_ms[0] == 9.0
    assert s.source_of("tmax_c") == "NASA POWER"


def test_between_rejects_inverted_or_empty_ranges():
    w = _weather(30)
    with pytest.raises(WeatherError, match="end before start"):
        w.between("2020-01-20", "2020-01-10")
    with pytest.raises(WeatherError, match="no data"):
        w.between("2019-01-01", "2019-06-01")


def test_water_year_can_start_in_any_month():
    """An agricultural year rarely aligns with a calendar year."""
    w = _weather(800, start="2020-01-01")
    june = w.water_year(2020, start_month=6)
    assert str(june.dates[0]) == "2020-06-01"
    assert str(june.dates[-1]) == "2021-05-31"
    assert len(june) == 365

    calendar = w.water_year(2020, start_month=1)
    assert len(calendar) == 366  # 2020 is a leap year


def test_water_year_validates_month_and_coverage():
    w = _weather(30)
    with pytest.raises(WeatherError, match="start_month"):
        w.water_year(2020, start_month=13)
    with pytest.raises(WeatherError, match="no data"):
        w.water_year(1999)


# --- monthly aggregation (RothC's timestep) -------------------------------------


def test_monthly_sums_rain_and_averages_temperature():
    n = 31 + 28  # exactly January and February 2021
    w = _weather(
        n,
        start="2021-01-01",
        precip_mm=np.ones(n) * 2.0,
        tmax_c=np.full(n, 25.0),
        tmin_c=np.full(n, 15.0),
    )
    m = w.monthly()
    assert len(m) == 2
    assert m.days.tolist() == [31, 28]
    assert m.precip_mm[0] == pytest.approx(62.0)  # summed, not averaged
    assert m.precip_mm[1] == pytest.approx(56.0)
    assert np.allclose(m.tmean_c, 20.0)  # averaged, not summed
    assert m.month_number.tolist() == [1, 2]
    assert m.year.tolist() == [2021, 2021]


def test_partial_months_are_counted_and_can_be_dropped():
    """Averaging a 9-day month as if it were whole would bias a carbon run."""
    w = _weather(40, start="2021-01-20")
    m = w.monthly()
    assert m.days.tolist() == [12, 28]  # partial January, whole February
    whole = m.complete()
    assert whole.days.tolist() == [28]
    assert whole.month_number.tolist() == [2]


def test_monthly_handles_leap_february():
    w = _weather(29, start="2020-02-01")
    assert w.monthly().complete().days.tolist() == [29]


# --- construction from adapter-shaped records -----------------------------------


def test_from_records_sorts_and_builds():
    records = [
        {"date": "2020-01-03", "tmax_c": 31, "tmin_c": 19, "precip_mm": 0},
        {"date": "2020-01-01", "tmax_c": 30, "tmin_c": 18, "precip_mm": 5},
        {"date": "2020-01-02", "tmax_c": 32, "tmin_c": 20, "precip_mm": 1},
    ]
    w = DailyWeather.from_records(records, provenance={"precip_mm": "GPM"})
    assert len(w) == 3
    assert str(w.dates[0]) == "2020-01-01"
    assert w.tmax_c.tolist() == [30.0, 32.0, 31.0]
    assert w.source_of("precip_mm") == "GPM"
    assert w.available() == set()


def test_from_records_accepts_a_fully_present_optional():
    records = [
        {"date": f"2020-01-{d:02d}", "tmax_c": 30, "tmin_c": 18, "precip_mm": 0,
         "wind_2m_ms": 2.5}
        for d in range(1, 6)
    ]
    w = DailyWeather.from_records(records)
    assert w.available() == {"wind_2m_ms"}


def test_from_records_rejects_a_partially_present_optional():
    """Half a wind series would silently change the ET0 method mid-run."""
    records = [
        {"date": "2020-01-01", "tmax_c": 30, "tmin_c": 18, "precip_mm": 0, "wind_2m_ms": 2.0},
        {"date": "2020-01-02", "tmax_c": 30, "tmin_c": 18, "precip_mm": 0},
    ]
    with pytest.raises(WeatherError, match="1/2 days"):
        DailyWeather.from_records(records)


def test_from_records_rejects_a_missing_required_variable():
    records = [{"date": "2020-01-01", "tmax_c": 30, "tmin_c": 18}]
    with pytest.raises(WeatherError, match="required"):
        DailyWeather.from_records(records)


def test_from_records_rejects_empty_input():
    with pytest.raises(WeatherError, match="no records"):
        DailyWeather.from_records([])

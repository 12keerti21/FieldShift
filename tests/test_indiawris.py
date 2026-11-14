"""India-WRIS adapter: the sign trap, the truncation trap, and the screening.

Two bugs in this adapter would have produced confident wrong answers rather than
errors, and both are pinned here. A dataset that silently reverses its sign halfway
through, and an endpoint that silently truncates at a page limit, are exactly the
failure modes that survive casual testing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fieldshift.io.indiawris import (
    DEPTH_SIGN,
    IndiaWrisError,
    WellSeries,
    annual_series,
    district_decline_m_per_year,
)

CACHED = Path("data/raw/indiawris_pb_sangrur.json")


def reading(station="S1", year=2000, value=10.0, mode="Manual", block="X",
            lat=30.2, lon=76.1, month=5):
    return {
        "stationCode": station, "stationName": f"well {station}", "block": block,
        "latitude": lat, "longitude": lon, "dataValue": value,
        "dataTime": f"{year}-{month:02d}-15T00:00:00", "dataAcquisitionMode": mode,
        "unit": "m",
    }


def rising_well(station="S1", start=1996, n=20, first=6.0, per_year=1.2, mode="Manual"):
    """A well whose water table falls steadily, in that mode's own sign convention."""
    sign = DEPTH_SIGN[mode]
    return [
        reading(station=station, year=start + i,
                value=sign * (first + per_year * i), mode=mode)
        for i in range(n)
    ]


# ------------------------------------------------------------- the sign trap


def test_manual_and_telemetric_sign_conventions_are_both_normalised():
    """The single most dangerous thing about this dataset.

    Manual wells report depth POSITIVE below ground; telemetric wells report it
    NEGATIVE. The same physical behaviour therefore arrives with opposite signs,
    and both must come out meaning the same thing.
    """
    manual, _ = annual_series(rising_well("M1", mode="Manual"), mode=None)
    telem, _ = annual_series(rising_well("T1", mode="Telemetric"), mode=None)
    assert manual[0].trend_m_per_year == pytest.approx(1.2, abs=1e-6)
    assert telem[0].trend_m_per_year == pytest.approx(1.2, abs=1e-6)


def test_mixing_modes_unnormalised_would_have_cancelled():
    """Proof the trap was real: without normalising, these two cancel to nothing.

    A naive combination does not produce a noisy trend, it produces a meaningless
    one - which is far worse, because a noisy trend looks wrong and a cancelled
    one looks like stability.
    """
    rows = rising_well("M1", mode="Manual") + rising_well("T1", mode="Telemetric")
    series, _ = annual_series(rows, mode=None)
    assert len(series) == 2
    normalised = [s.trend_m_per_year for s in series]
    assert all(t > 0 for t in normalised), "both wells are falling"

    # what an unnormalised reader would have computed, per station
    by = {}
    for r in rows:
        by.setdefault(r["stationCode"], []).append(r["dataValue"])
    slopes = [np.polyfit(np.arange(len(v), dtype=float), v, 1)[0] for v in by.values()]
    assert slopes[0] * slopes[1] < 0, "unnormalised, the two wells disagree in sign"
    assert abs(sum(slopes)) < 1e-9, "and they cancel exactly"


def test_a_depth_outside_the_plausible_range_is_rejected():
    rows = rising_well("M1") + [reading(station="M1", year=2030, value=-204.0)]
    series, rejected = annual_series(rows)
    assert rejected["implausible depth"] == 1
    assert series[0].years.max() < 2030


def test_an_unknown_acquisition_mode_is_refused_not_guessed():
    """Guessing the sign for an unknown mode is how the trap would come back."""
    rows = [reading(station="X", year=y, mode="Satellite") for y in range(2000, 2012)]
    series, rejected = annual_series(rows, mode=None)
    assert rejected["unknown sign convention"] == 12
    assert series == []


# --------------------------------------------------------------- screening


def test_short_records_are_screened_out():
    """A well installed in 2019 cannot speak to a twenty-year trend."""
    rows = rising_well("LONG", n=20) + rising_well("SHORT", start=2019, n=3)
    series, rejected = annual_series(rows)
    assert {s.station_code for s in series} == {"LONG"}
    assert rejected["record too short"] == 1


def test_manual_filter_excludes_telemetric_by_default():
    rows = rising_well("M1", mode="Manual") + rising_well("T1", mode="Telemetric")
    series, rejected = annual_series(rows)
    assert {s.station_code for s in series} == {"M1"}
    assert rejected["wrong acquisition mode"] == 20


def test_readings_within_a_year_are_averaged_not_concatenated():
    """CGWB wells are read pre- and post-monsoon.

    The seasonal swing in a pumped aquifer is larger than the annual trend, so
    mixing readings without collapsing to annual means puts the seasonal cycle
    into the trend.
    """
    rows = []
    for i, year in enumerate(range(2000, 2012)):
        rows.append(reading(station="S", year=year, value=10.0 + i, month=5))   # pre
        rows.append(reading(station="S", year=year, value=6.0 + i, month=11))   # post
    series, _ = annual_series(rows)
    assert len(series) == 1
    assert len(series[0].years) == 12
    assert series[0].depth_m[0] == pytest.approx(8.0)  # mean of 10 and 6
    assert series[0].trend_m_per_year == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------- district


def test_district_trend_uses_the_median_and_reports_the_spread():
    """One well beside a canal must not move the district figure."""
    # D is a well beside a recharge source: its table is genuinely rising, which
    # is a plausible reading and must not be screened out as bad data - only
    # prevented from dominating the district figure.
    rows = (rising_well("A", per_year=1.0) + rising_well("B", per_year=1.1)
            + rising_well("C", per_year=1.2)
            + rising_well("D", first=60.0, per_year=-2.0))
    series, _ = annual_series(rows)
    median, iqr, n = district_decline_m_per_year(series)
    assert n == 4
    assert median == pytest.approx(1.05, abs=0.01), "the outlier must not dominate"
    assert iqr > 0


def test_a_district_with_no_usable_well_refuses_rather_than_returning_zero():
    with pytest.raises(IndiaWrisError, match="no well"):
        district_decline_m_per_year([])


def test_distance_is_great_circle():
    s = WellSeries("A", "a", "b", 30.0, 76.0, np.array([2000]), np.array([5.0]), 1)
    assert s.distance_km(30.0, 76.0) == pytest.approx(0.0, abs=1e-6)
    # one degree of latitude is about 111 km anywhere
    assert s.distance_km(31.0, 76.0) == pytest.approx(111.2, abs=1.0)


# --------------------------------------------------------- the real record


@pytest.mark.skipif(not CACHED.exists(), reason="no cached Sangrur record")
def test_the_real_sangrur_record_shows_a_falling_table():
    """The result the Punjab validation rests on.

    Not a tolerance check - every well in a district that pumps this hard should
    agree on the direction, and if they ever stop agreeing the validation claim
    has to change with them.
    """
    rows = json.loads(CACHED.read_text())["data"]
    series, rejected = annual_series(rows)
    assert len(series) >= 15, "too few long-record wells to make a district claim"
    median, iqr, n = district_decline_m_per_year(series)
    assert median > 0.5, "Sangrur's water table is falling, and fast"
    falling = sum(1 for s in series if s.trend_m_per_year > 0)
    assert falling == n, "every well should agree on the direction"
    assert max(len(s.years) for s in series) >= 20


@pytest.mark.skipif(not CACHED.exists(), reason="no cached Sangrur record")
def test_the_telemetric_flood_is_what_made_year_by_year_necessary():
    """Most of the raw payload is telemetric; the manual wells are a thin slice."""
    rows = json.loads(CACHED.read_text())["data"]
    manual = [r for r in rows if r.get("dataAcquisitionMode") == "Manual"]
    assert len(manual) < 0.1 * len(rows), (
        "manual readings are a small minority, which is why a single bulk request "
        "hit the page cap with telemetric data and returned no manual wells at all"
    )

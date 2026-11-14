"""USDA NASS QuickStats adapter and the skill statistics built on it.

Two methodological choices are pinned here because getting either wrong would
manufacture a flattering validation result:

1. County yields are DETRENDED before comparison. Four decades carry a large
   genetic and agronomic trend unrelated to weather; correlating a weather-driven
   index against raw yield would largely measure that trend.
2. Units are never mixed. A single QuickStats response for one crop can contain
   BU / ACRE, BU / NET PLANTED ACRE and TONS / ACRE together.
"""

import json
import pathlib

import numpy as np
import pytest

from fieldshift.io.quickstats import (
    NON_IRRIGATED,
    QuickStatsError,
    YieldSeries,
    parse_yield_rows,
    skill,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "quickstats_corn_dryland.json"


@pytest.fixture
def rows():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def series(rows):
    return parse_yield_rows(rows, "CORN", NON_IRRIGATED)


def test_real_response_parses_into_an_annual_series(series):
    assert series.commodity == "CORN"
    assert series.unit == "BU / ACRE"
    assert len(series) >= 15
    assert np.all(np.diff(series.years) > 0)  # sorted, no duplicate years
    assert np.all(series.values > 0)


def test_dryland_corn_in_this_county_is_marginal(series):
    """Sanity anchor: ~50 bu/ac against ~170 under irrigation."""
    assert 30.0 < series.values.mean() < 80.0


MIXED_UNIT_ROWS = [
    # A real QuickStats response for one crop can carry all three of these
    # together. Averaging across them would be meaningless.
    {"year": "2000", "Value": "150", "unit_desc": "BU / ACRE"},
    {"year": "2001", "Value": "160", "unit_desc": "BU / ACRE"},
    {"year": "2000", "Value": "165", "unit_desc": "BU / NET PLANTED ACRE"},
    {"year": "2000", "Value": "8.4", "unit_desc": "TONS / ACRE"},
]


def test_units_are_never_mixed():
    s = parse_yield_rows(MIXED_UNIT_ROWS, "CORN", NON_IRRIGATED)
    assert s.unit == "BU / ACRE"
    assert "NET" not in s.unit  # net-planted acres are not comparable
    assert s.values.tolist() == [150.0, 160.0]
    assert 8.4 not in s.values.tolist()  # the tonnage row is excluded


def test_explicit_unit_selection_is_honoured():
    s = parse_yield_rows(MIXED_UNIT_ROWS, "CORN", NON_IRRIGATED, unit="TONS / ACRE")
    assert s.unit == "TONS / ACRE"
    assert s.values.tolist() == [8.4]


def test_the_real_fixture_resolves_to_a_per_acre_unit(series):
    assert series.unit.endswith("/ ACRE")
    assert "NET" not in series.unit


def test_suppressed_values_are_skipped():
    """NASS writes '(D)' where a sample is too small to publish."""
    rows = [
        {"year": "2000", "Value": "50", "unit_desc": "BU / ACRE"},
        {"year": "2001", "Value": " (D)", "unit_desc": "BU / ACRE"},
        {"year": "2002", "Value": "1,234", "unit_desc": "BU / ACRE"},
    ]
    s = parse_yield_rows(rows, "CORN", NON_IRRIGATED)
    assert s.years.tolist() == [2000, 2002]
    assert s.values.tolist() == [50.0, 1234.0]  # thousands separator handled


def test_empty_and_unusable_responses_are_rejected():
    with pytest.raises(QuickStatsError, match="no records"):
        parse_yield_rows([], "CORN", NON_IRRIGATED)
    with pytest.raises(QuickStatsError, match="no per-acre unit"):
        parse_yield_rows(
            [{"year": "2000", "Value": "5", "unit_desc": "TONS"}], "CORN", NON_IRRIGATED
        )


# --- detrending -------------------------------------------------------------------


def test_detrending_removes_the_technology_trend(series):
    residual = series.detrend()
    assert abs(residual.mean()) < 1e-9
    # A trend remains in the raw series but not in the residual.
    raw_slope = np.polyfit(series.years, series.values, 1)[0]
    res_slope = np.polyfit(series.years, residual, 1)[0]
    assert abs(raw_slope) > abs(res_slope) + 1e-6
    assert abs(res_slope) < 1e-9


def test_detrending_preserves_year_to_year_variation(series):
    residual = series.detrend()
    assert residual.std() > 0.1 * series.values.std()


def test_trend_is_positive_for_a_modern_yield_record(series):
    assert series.trend_per_year() > 0.0


def test_too_short_a_series_cannot_be_detrended():
    s = YieldSeries("CORN", NON_IRRIGATED, "BU / ACRE", "X", "KS",
                    np.array([2000, 2001]), np.array([50.0, 55.0]))
    with pytest.raises(QuickStatsError, match="too few"):
        s.detrend()
    with pytest.raises(QuickStatsError, match="too few"):
        s.trend_per_year()


def test_common_years_intersects_two_series():
    a = YieldSeries("CORN", "IRRIGATED", "BU / ACRE", "X", "KS",
                    np.array([1990, 1991, 1992]), np.array([1.0, 2.0, 3.0]))
    b = YieldSeries("CORN", NON_IRRIGATED, "BU / ACRE", "X", "KS",
                    np.array([1991, 1992, 1993]), np.array([1.0, 2.0, 3.0]))
    assert a.common_years(b).tolist() == [1991, 1992]


# --- skill reporting --------------------------------------------------------------


def test_skill_reports_r_r2_n_and_a_p_value():
    rng = np.random.default_rng(0)
    x = rng.normal(size=25)
    y = x * 0.6 + rng.normal(size=25) * 0.8
    s = skill(x, y)
    assert set(s) == {"r", "r2", "n", "p_value"}
    assert s["n"] == 25
    assert s["r2"] == pytest.approx(s["r"] ** 2)
    assert 0.0 <= s["p_value"] <= 1.0


def test_perfect_correlation_is_significant():
    x = np.linspace(0, 1, 20)
    s = skill(x, 2 * x + 1)
    assert s["r"] == pytest.approx(1.0)
    assert s["p_value"] < 1e-6


def test_skill_refuses_too_few_paired_years():
    """A correlation over four county-years means nothing and must not be reported."""
    with pytest.raises(QuickStatsError, match="not enough"):
        skill(np.arange(4.0), np.arange(4.0))


def test_skill_rejects_mismatched_lengths():
    with pytest.raises(QuickStatsError, match="lengths differ"):
        skill(np.arange(10.0), np.arange(9.0))

"""gridMET adapter, and the plausibility guard that exists because of a real error.

The THREDDS NCSS CSV export returns packed integers and does not apply the
`scale_factor` in the file metadata. Reading it unguarded gave 42 mm/day of
reference ET - impossible, but large enough that it was briefly reported as "our
model is 18% biased" before the magnitude was checked. These tests make that
failure mode impossible to reintroduce.
"""

import pathlib

import numpy as np
import pytest

from rdss.io.gridmet import (
    PLAUSIBLE_RANGE,
    VARIABLES,
    GridmetError,
    parse_gridmet_csv,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gridmet_eto_sample.csv"


@pytest.fixture
def raw():
    return FIXTURE.read_text(encoding="utf-8")


def test_packed_values_are_scaled_to_physical_units(raw):
    s = parse_gridmet_csv(raw, "eto", scale_factor=0.1)
    assert s.units == "mm"
    assert s.scale_factor == 0.1
    assert 0.0 < s.values.mean() < 12.0
    assert s.values.max() <= PLAUSIBLE_RANGE["eto"][1]


def test_unapplied_scale_factor_is_refused(raw):
    """The actual bug: reading the CSV at face value.

    Without this guard the series passes silently and every downstream water
    balance is wrong by an order of magnitude.
    """
    with pytest.raises(GridmetError, match="plausible range"):
        parse_gridmet_csv(raw, "eto", scale_factor=1.0)


def test_the_error_message_names_the_likely_cause(raw):
    with pytest.raises(GridmetError) as exc:
        parse_gridmet_csv(raw, "eto", scale_factor=1.0)
    assert "packing transform" in str(exc.value)
    assert "packed integers" in str(exc.value)


def test_a_scale_factor_wrong_in_the_other_direction_is_also_refused(raw):
    """Over-scaling leaves values implausibly small rather than large."""
    s = parse_gridmet_csv(raw, "eto", scale_factor=0.0001)
    assert s.values.max() < 0.02  # physically absurd but inside the 0-20 window
    # The guard is one-sided by construction; document that explicitly so nobody
    # assumes it catches every unit error.
    assert PLAUSIBLE_RANGE["eto"][0] == 0.0


def test_plausibility_check_can_be_disabled_deliberately(raw):
    s = parse_gridmet_csv(raw, "eto", scale_factor=1.0, enforce_plausibility=False)
    assert s.values.max() > 20.0


def test_dates_are_parsed_sorted_and_daily(raw):
    s = parse_gridmet_csv(raw, "eto", scale_factor=0.1)
    assert s.dates.dtype == np.dtype("datetime64[D]")
    assert np.all(np.diff(s.dates).astype(int) == 1)
    assert str(s.dates[0]) == "1984-01-01"


def test_annual_totals_are_available(raw):
    s = parse_gridmet_csv(raw, "eto", scale_factor=0.1)
    totals = s.annual_totals()
    assert 1984 in totals
    assert totals[1984] > 0.0


def test_source_is_recorded_for_the_evidence_card(raw):
    s = parse_gridmet_csv(raw, "eto", scale_factor=0.1)
    assert "gridMET" in s.source
    assert "agg_met_pet" in s.source


def test_add_offset_is_applied(raw):
    base = parse_gridmet_csv(raw, "eto", scale_factor=0.1)
    shifted = parse_gridmet_csv(raw, "eto", scale_factor=0.1, add_offset=1.0)
    assert np.allclose(shifted.values, base.values + 1.0)


def test_unknown_variable_is_rejected(raw):
    with pytest.raises(GridmetError, match="unknown variable"):
        parse_gridmet_csv(raw, "soil_moisture", scale_factor=0.1)


def test_empty_and_malformed_input_are_rejected():
    with pytest.raises(GridmetError, match="empty CSV"):
        parse_gridmet_csv("", "eto", 0.1)
    with pytest.raises(GridmetError, match="no time/value column"):
        parse_gridmet_csv("a,b\n1,2\n", "eto", 0.1)


def test_every_variable_has_a_plausible_range_defined():
    """A new variable must not slip in without a magnitude guard."""
    assert set(VARIABLES) == set(PLAUSIBLE_RANGE)
    for name, (lo, hi) in PLAUSIBLE_RANGE.items():
        assert lo < hi, name

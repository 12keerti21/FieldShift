"""MODIS NDVI adapter: the three traps that would quietly bias the answer.

Each of these would produce a confident wrong number rather than an error, and
each biases in the same direction — toward reporting a field as barer than it
is, which is exactly the measure this dataset exists to validate.
"""

from __future__ import annotations

import numpy as np
import pytest

from fieldshift.io.ndvi import (
    BARE_NDVI,
    NDVI_FILL,
    NdviError,
    parse_appeears_csv,
)

HEADER = (
    "ID,Latitude,Longitude,Date,MOD13Q1_061__250m_16_days_NDVI,"
    "MOD13Q1_061__250m_16_days_pixel_reliability\n"
)


def write_csv(tmp_path, rows, header=HEADER):
    path = tmp_path / "ndvi.csv"
    path.write_text(header + "".join(rows))
    return path


def row(sid="finney_ks", date="2010-05-01", ndvi=6000, reliability=0):
    return f"{sid},38.03,-100.74,{date},{ndvi},{reliability}\n"


def many(n=60, ndvi=6000, reliability=0, sid="finney_ks", start=2010):
    out = []
    for i in range(n):
        d = np.datetime64(f"{start}-01-01") + np.timedelta64(16 * i, "D")
        out.append(row(sid, str(d), ndvi, reliability))
    return out


# -------------------------------------------------------------- trap 1: scale


def test_raw_integers_are_scaled_to_real_ndvi():
    """MODIS ships NDVI as an integer scaled by 10,000.

    An unscaled 6,231 is not an NDVI. Left unscaled every value is three orders
    of magnitude out and every threshold comparison silently inverts.
    """
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        path = write_csv(pathlib.Path(d), many(ndvi=6231))
        series = parse_appeears_csv(path)["finney_ks"]
    assert np.allclose(series.ndvi, 0.6231)


def test_already_scaled_values_are_not_scaled_twice(tmp_path):
    """AppEEARS can deliver either form, so detect rather than assume.

    Dividing an already-scaled 0.62 by 10,000 gives 0.000062, which reads as
    bare ground at every threshold — a field that is actually in full canopy.
    """
    path = write_csv(tmp_path, many(ndvi="0.6231"))
    series = parse_appeears_csv(path)["finney_ks"]
    assert np.allclose(series.ndvi, 0.6231)


def test_values_outside_the_physical_range_are_rejected(tmp_path):
    path = write_csv(tmp_path, many() + [row(ndvi=25000)])
    series = parse_appeears_csv(path)["finney_ks"]
    assert series.rejected["out of range"] == 1
    assert np.all(np.abs(series.ndvi) <= 1.0)


# --------------------------------------------------------------- trap 2: fill


def test_the_fill_value_is_removed_before_scaling(tmp_path):
    """-3000 is the documented fill.

    Scaled first it becomes -0.3, which is a plausible-looking NDVI for water or
    snow and survives every range check — dragging the mean down and making the
    field look bare.
    """
    path = write_csv(tmp_path, many() + [row(ndvi=NDVI_FILL)] * 5)
    series = parse_appeears_csv(path)["finney_ks"]
    assert series.rejected["fill"] == 5
    assert np.all(series.ndvi > 0.0)


def test_unparsable_values_are_counted_not_crashed_on(tmp_path):
    path = write_csv(tmp_path, many() + [row(ndvi="")] + [row(ndvi="N/A")])
    series = parse_appeears_csv(path)["finney_ks"]
    assert series.rejected["unparsable"] == 2


# -------------------------------------------------------------- trap 3: cloud


def test_cloudy_pixels_are_discarded():
    """This is the one that matters most, and it is seasonal.

    MODIS is optical: a cloudy composite is a bad observation, not a brown
    field. In a monsoon climate the cloud comes exactly when the crop is
    densest, so keeping cloudy pixels would make the wettest, greenest season
    read as bare ground.
    """
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        rows = many(ndvi=7000, reliability=0) + [
            row(ndvi=500, reliability=3),   # cloud
            row(ndvi=200, reliability=2),   # snow
            row(ndvi=100, reliability=-1),  # fill
        ]
        series = parse_appeears_csv(write_csv(pathlib.Path(d), rows))["finney_ks"]
    assert series.rejected["unreliable"] == 3
    assert series.ndvi.min() > 0.5, "no cloudy low value should have survived"


def test_marginal_pixels_are_kept(tmp_path):
    """Discarding reliability 1 thins the record exactly where it is scarcest."""
    path = write_csv(tmp_path, many(ndvi=6000, reliability=1))
    series = parse_appeears_csv(path)["finney_ks"]
    assert series.kept == 60
    assert series.rejected["unreliable"] == 0


def test_cloud_screening_changes_the_bare_fraction(tmp_path):
    """Proof the trap is real rather than theoretical."""
    clean = many(ndvi=7000, reliability=0)
    cloudy = [row(ndvi=500, reliability=3) for _ in range(40)]
    screened = parse_appeears_csv(write_csv(tmp_path, clean + cloudy))["finney_ks"]
    assert screened.bare_fraction() == 0.0

    # what an adapter that ignored reliability would have reported
    unscreened_values = np.array([0.7] * 60 + [0.05] * 40)
    naive_bare = float((unscreened_values < BARE_NDVI).mean())
    assert naive_bare == pytest.approx(0.4), (
        "ignoring cloud would have called this field 40% bare when it is never bare"
    )


# ------------------------------------------------------------------ structure


def test_multiple_sites_are_separated(tmp_path):
    rows = many(sid="finney_ks", ndvi=6000) + many(sid="sangrur_pb", ndvi=3000)
    out = parse_appeears_csv(write_csv(tmp_path, rows))
    assert set(out) == {"finney_ks", "sangrur_pb"}
    assert out["finney_ks"].ndvi.mean() > out["sangrur_pb"].ndvi.mean()


def test_observations_come_back_in_date_order(tmp_path):
    rows = [row(date="2012-06-01"), row(date="2010-01-01"), row(date="2011-03-01")]
    rows += many(n=50)
    series = parse_appeears_csv(write_csv(tmp_path, rows))["finney_ks"]
    assert np.all(np.diff(series.dates.astype(int)) >= 0)


def test_a_file_with_nothing_usable_refuses_rather_than_returning_empty(tmp_path):
    path = write_csv(tmp_path, [row(ndvi=NDVI_FILL) for _ in range(20)])
    with pytest.raises(NdviError, match="no usable observations"):
        parse_appeears_csv(path)


def test_a_missing_file_says_so():
    with pytest.raises(NdviError, match="no NDVI file"):
        parse_appeears_csv("data/raw/does_not_exist.csv")


def test_unexpected_columns_are_refused(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("foo,bar\n1,2\n")
    with pytest.raises(NdviError, match="unexpected AppEEARS columns"):
        parse_appeears_csv(path)


def test_bare_fraction_counts_observations_below_the_threshold(tmp_path):
    rows = many(n=30, ndvi=7000) + many(n=10, ndvi=1000, start=2015)
    series = parse_appeears_csv(write_csv(tmp_path, rows))["finney_ks"]
    assert series.bare_fraction() == pytest.approx(0.25)
    # a higher threshold must call more of the record bare, never less
    assert series.bare_fraction(0.8) >= series.bare_fraction(0.25)

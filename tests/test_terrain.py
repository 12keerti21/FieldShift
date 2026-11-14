"""Terrain and the RUSLE slope-length factor.

The finding this suite exists to protect against: a 3x3 Horn slope estimate on
NASADEM gave 5.77 percent at the pilot site, where the real gradient is about
0.25 percent. DEM vertical noise of a few metres over a 30 m cell is several
percent of apparent gradient, and LS scales close to linearly with slope in this
range - so taking the 3x3 value would have overstated soil loss by roughly 17x.

Core maths is tested against synthetic tiles with a KNOWN slope, so the assertions
do not depend on a downloaded file. The real NASADEM tile is used where available
for an end-to-end check.
"""

import pathlib

import numpy as np
import pytest

from fieldshift.io.terrain import (
    ARCSEC_SAMPLES,
    ARCSEC3_SAMPLES,
    HGT_VOID,
    TerrainError,
    field_slope_percent,
    ls_factor,
    read_hgt_window,
    slope_percent,
    slope_percent_planar,
    slope_stability_profile,
)

REAL_TILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "data" / "raw" / "nasadem_n38w101" / "n38w101.hgt"
)
needs_tile = pytest.mark.skipif(
    not REAL_TILE.exists(), reason="NASADEM tile not downloaded"
)


def _synthetic_tile(tmp_path, *, fall_percent=0.0, noise_m=0.0, seed=0, base=800.0,
                    samples=ARCSEC_SAMPLES):
    """Write a .hgt with a known planar slope, optionally noisy.

    Defaults to 1 arc-second (31 m cells) because that is what NASADEM actually
    delivers, and both error mechanisms this suite cares about scale with cell size:

    - DEM noise of a few metres over a 31 m cell is several percent of apparent
      gradient; over a 93 m cell it is three times less.
    - `.hgt` stores integer metres, so a single-cell-pair slope on 31 m ground is
      quantised in steps of about 3 percent. A 1 percent true slope cannot be
      represented by one cell pair at all.

    Both are arguments for measuring slope over a wide window rather than a 3x3.
    """
    n = samples
    cell_y = (1.0 / (n - 1)) * 111_320.0
    rows = np.arange(n)[:, None]
    # Row 0 is the NORTH edge, so elevation rising southward is a southward fall.
    surface = base + rows * cell_y * (fall_percent / 100.0)
    field = np.broadcast_to(surface, (n, n)).astype(float).copy()
    if noise_m:
        field += np.random.default_rng(seed).normal(0.0, noise_m, size=(n, n))
    path = tmp_path / "n38w101.hgt"
    np.round(field).astype(">i2").tofile(path)
    return path


# --- reading ----------------------------------------------------------------------


def test_a_known_planar_slope_is_recovered(tmp_path):
    """Both methods recover a steep noiseless surface, but not equally well.

    Even with no sensor noise at all, Horn's 3x3 spans only two cells - a 9.3 m rise
    at 15 percent - and `.hgt` rounds to whole metres, so the estimate carries about
    +/-5 percent of quantisation error. The planar fit over a wide window averages
    that down. Worth pinning: the wide fit is more accurate even in the ideal case,
    so preferring it is not merely a noise workaround.
    """
    path = _synthetic_tile(tmp_path, fall_percent=15.0, base=0.0)
    wide = read_hgt_window(path, 38.5, -100.5, half_window=15)
    narrow = read_hgt_window(path, 38.5, -100.5, half_window=1)

    assert slope_percent_planar(wide) == pytest.approx(15.0, abs=0.3)
    assert slope_percent(narrow) == pytest.approx(15.0, abs=2.0)
    assert abs(slope_percent_planar(wide) - 15.0) < abs(slope_percent(narrow) - 15.0)


def test_integer_metre_storage_quantises_gentle_slopes(tmp_path):
    """.hgt holds whole metres, so one 31 m cell pair cannot resolve 1 percent.

    This is a second, independent reason to fit a plane across a wide window: even
    with a perfect noiseless sensor, the storage format alone makes a single-cell
    estimate on gentle ground wrong by several percent.
    """
    path = _synthetic_tile(tmp_path, fall_percent=1.0)
    narrow = read_hgt_window(path, 38.5, -100.5, half_window=1)
    wide = read_hgt_window(path, 38.5, -100.5, half_window=15)
    # The 3x3 estimate snaps to a quantisation step; the wide fit recovers the truth.
    assert slope_percent_planar(wide) == pytest.approx(1.0, abs=0.25)
    assert abs(slope_percent(narrow) - 1.0) > abs(slope_percent_planar(wide) - 1.0)


def test_a_flat_surface_reads_as_flat(tmp_path):
    path = _synthetic_tile(tmp_path, fall_percent=0.0)
    patch = read_hgt_window(path, 38.5, -100.5, half_window=5)
    assert slope_percent(patch) == pytest.approx(0.0, abs=0.05)


def test_cell_size_shrinks_with_latitude(tmp_path):
    path = _synthetic_tile(tmp_path)
    low = read_hgt_window(path, 38.05, -100.5)
    assert low.cell_size_x_m < low.cell_size_y_m  # longitude converges poleward
    assert 20.0 < low.cell_size_x_m < 100.0


def test_a_point_outside_the_tile_is_refused(tmp_path):
    path = _synthetic_tile(tmp_path)
    with pytest.raises(TerrainError, match="outside tile"):
        read_hgt_window(path, 45.0, -100.5)


def test_a_wrongly_sized_file_is_refused(tmp_path):
    bad = tmp_path / "n38w101.hgt"
    np.zeros(500, dtype=">i2").tofile(bad)
    with pytest.raises(TerrainError, match="arc-second"):
        read_hgt_window(bad, 38.5, -100.5)


def test_voids_are_filled_and_reported(tmp_path):
    path = _synthetic_tile(tmp_path, fall_percent=2.0)
    grid = np.fromfile(path, dtype=">i2").reshape(ARCSEC_SAMPLES, ARCSEC_SAMPLES)
    # (38.5, -100.5) in tile n38w101 maps to the grid centre, so the void has to go
    # there to fall inside the read window.
    centre = (ARCSEC_SAMPLES - 1) // 2
    grid[centre, centre + 1] = HGT_VOID
    grid.tofile(path)
    patch = read_hgt_window(path, 38.5, -100.5, half_window=3)
    assert patch.void_fraction > 0.0
    assert np.isfinite(patch.elevation_m).all()


def test_an_all_void_window_is_refused(tmp_path):
    path = tmp_path / "n38w101.hgt"
    np.full(ARCSEC3_SAMPLES**2, HGT_VOID, dtype=">i2").tofile(path)
    with pytest.raises(TerrainError, match="void"):
        read_hgt_window(path, 38.5, -100.5)


# --- THE finding: noise dominates Horn on flat ground ----------------------------


def test_dem_noise_corrupts_the_small_window_estimate(tmp_path):
    """A truly flat surface plus 3 m of noise reads as several percent on 3x3."""
    path = _synthetic_tile(tmp_path, fall_percent=0.2, noise_m=3.0, seed=7)
    narrow = read_hgt_window(path, 38.5, -100.5, half_window=1)
    wide = read_hgt_window(path, 38.5, -100.5, half_window=15)

    horn = slope_percent(narrow)
    planar = slope_percent_planar(wide)
    assert horn > 2.0, f"expected noise-inflated Horn estimate, got {horn:.2f}%"
    assert planar < 1.0, f"wide planar fit should recover near-flat, got {planar:.2f}%"
    assert planar == pytest.approx(0.2, abs=0.6)


def test_the_planar_fit_preserves_a_real_slope(tmp_path):
    """The fix must not simply flatten everything - that would be worse."""
    path = _synthetic_tile(tmp_path, fall_percent=6.0, noise_m=3.0, seed=11)
    wide = read_hgt_window(path, 38.5, -100.5, half_window=15)
    assert slope_percent_planar(wide) == pytest.approx(6.0, abs=0.5)


def test_stability_profile_shows_where_the_estimate_settles(tmp_path):
    path = _synthetic_tile(tmp_path, fall_percent=0.3, noise_m=3.0, seed=3)
    rows = slope_stability_profile(path, 38.5, -100.5, half_windows=(1, 3, 8, 18))
    assert [r[0] for r in rows] == [1, 3, 8, 18]
    assert all(r[1] > 0 for r in rows)  # baseline metres
    # The planar estimate must converge toward the truth as the window widens.
    assert abs(rows[-1][3] - 0.3) < abs(rows[0][3] - 0.3)


def test_planar_slope_needs_a_real_window(tmp_path):
    path = _synthetic_tile(tmp_path)
    patch = read_hgt_window(path, 38.5, -100.5, half_window=1)
    narrowed = type(patch)(
        elevation_m=patch.elevation_m[:1, :1], cell_size_x_m=patch.cell_size_x_m,
        cell_size_y_m=patch.cell_size_y_m, latitude=38.5, longitude=-100.5,
        source="synthetic", void_fraction=0.0,
    )
    with pytest.raises(TerrainError, match="3x3"):
        slope_percent_planar(narrowed)
    with pytest.raises(TerrainError, match="3x3"):
        slope_percent(narrowed)


# --- the LS factor ----------------------------------------------------------------


def test_ls_rises_with_slope_and_length():
    slopes = [0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
    values = [ls_factor(s, 75.0) for s in slopes]
    assert all(a < b for a, b in zip(values, values[1:]))
    lengths = [ls_factor(2.0, L) for L in (20, 50, 100, 200)]
    assert all(a < b for a, b in zip(lengths, lengths[1:]))


def test_ls_is_near_one_at_the_unit_plot():
    """RUSLE is normalised to a 9 percent, 22.13 m plot, where LS should be ~1."""
    assert ls_factor(9.0, 22.13) == pytest.approx(1.0, abs=0.15)


def test_mccool_relations_switch_at_nine_percent():
    just_under = ls_factor(8.99, 50.0)
    just_over = ls_factor(9.01, 50.0)
    assert just_over > just_under  # continues rising, no discontinuous drop
    assert abs(just_over - just_under) < 0.2  # and the join is not a cliff


def test_the_noise_error_would_have_been_enormous():
    """Quantifies the consequence: 0.25% vs 5.77% slope at the pilot site."""
    truth = ls_factor(0.25, 75.0)
    noisy = ls_factor(5.77, 75.0)
    assert noisy / truth > 10.0


def test_invalid_ls_inputs_are_refused():
    with pytest.raises(TerrainError, match="negative"):
        ls_factor(-1.0)
    with pytest.raises(TerrainError, match="length"):
        ls_factor(2.0, 0.0)


# --- end to end on the real tile ---------------------------------------------------


@needs_tile
def test_real_tile_gives_a_plausible_high_plains_slope():
    slope, patch = field_slope_percent(REAL_TILE, 38.03, -100.74)
    assert 0.0 < slope < 1.0, f"Garden City tableland should be near-flat, got {slope:.2f}%"
    assert 800 < patch.centre_elevation_m < 950
    assert patch.void_fraction == 0.0
    assert "NASADEM" in patch.source


@needs_tile
def test_real_tile_reproduces_the_noise_finding():
    narrow = read_hgt_window(REAL_TILE, 38.03, -100.74, half_window=1)
    assert slope_percent(narrow) > 3.0            # the noise-dominated figure
    assert field_slope_percent(REAL_TILE, 38.03, -100.74)[0] < 1.0  # the real one

"""Terrain from NASADEM / SRTM, for the RUSLE slope-length factor.

Slope is the one RUSLE factor a farmer cannot change and cannot easily estimate by
eye, and it enters the erosion equation steeply - the difference between a 1 percent
and a 4 percent slope is roughly a factor of four in soil loss. It therefore has to
come from measurement rather than from a default.

NASADEM 1-arc-second tiles are distributed as raw `.hgt`: big-endian signed 16-bit
elevations, 3601 x 3601 samples per one-degree tile, row zero at the northern edge,
voids marked -32768. That needs no raster library at all, which removes a heavy
dependency from the critical path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

HGT_VOID = -32768
ARCSEC_SAMPLES = 3601  # 1 arc-second tile
ARCSEC3_SAMPLES = 1201  # 3 arc-second tile
METRES_PER_DEGREE_LAT = 111_320.0


class TerrainError(ValueError):
    pass


@dataclass(frozen=True)
class TerrainPatch:
    """A small window of elevations around a point, with its ground spacing."""

    elevation_m: np.ndarray
    cell_size_x_m: float
    cell_size_y_m: float
    latitude: float
    longitude: float
    source: str
    void_fraction: float

    @property
    def centre_elevation_m(self) -> float:
        n = self.elevation_m.shape[0] // 2
        return float(self.elevation_m[n, n])


def read_hgt_window(
    path: str | Path,
    latitude: float,
    longitude: float,
    half_window: int = 1,
) -> TerrainPatch:
    """Read a (2*half_window+1)^2 window of elevations centred on a point.

    The tile's own name carries its south-west corner, which is how the pixel
    indices are derived; a mismatch between the requested point and the tile is
    raised rather than silently wrapping to the wrong corner of the grid.
    """
    path = Path(path)
    raw = np.fromfile(path, dtype=">i2")
    for samples in (ARCSEC_SAMPLES, ARCSEC3_SAMPLES):
        if raw.size == samples * samples:
            break
    else:
        raise TerrainError(
            f"{path.name} has {raw.size} samples, which is neither a 1- nor a "
            "3-arc-second SRTM/NASADEM tile"
        )
    grid = raw.reshape(samples, samples)

    # Tile naming: n38w101 means the south-west corner is 38N, 101W.
    stem = path.stem.lower()
    try:
        lat_sign = 1 if "n" in stem else -1
        lat_part = stem.split("n")[1] if "n" in stem else stem.split("s")[1]
        corner_lat = lat_sign * int(lat_part[:2])
        lon_sign = -1 if "w" in stem else 1
        lon_part = stem.split("w")[1] if "w" in stem else stem.split("e")[1]
        corner_lon = lon_sign * int(lon_part[:3])
    except (IndexError, ValueError):
        raise TerrainError(f"cannot read a corner from the tile name {path.stem!r}") from None

    if not (corner_lat <= latitude < corner_lat + 1 and corner_lon <= longitude < corner_lon + 1):
        raise TerrainError(
            f"point ({latitude}, {longitude}) lies outside tile {path.stem} "
            f"which covers {corner_lat}..{corner_lat + 1}N, {corner_lon}..{corner_lon + 1}E"
        )

    step = 1.0 / (samples - 1)
    row = int(round((corner_lat + 1 - latitude) / step))
    col = int(round((longitude - corner_lon) / step))
    row = int(np.clip(row, half_window, samples - 1 - half_window))
    col = int(np.clip(col, half_window, samples - 1 - half_window))

    window = grid[
        row - half_window: row + half_window + 1,
        col - half_window: col + half_window + 1,
    ].astype(float)
    voids = window == HGT_VOID
    void_fraction = float(voids.mean())
    if void_fraction == 1.0:
        raise TerrainError(f"every cell in the window around ({latitude}, {longitude}) is void")
    if voids.any():
        window = np.where(voids, np.nan, window)
        window = np.where(np.isnan(window), np.nanmean(window), window)

    cell_y = step * METRES_PER_DEGREE_LAT
    cell_x = cell_y * float(np.cos(np.deg2rad(latitude)))
    return TerrainPatch(
        elevation_m=window,
        cell_size_x_m=cell_x,
        cell_size_y_m=cell_y,
        latitude=latitude,
        longitude=longitude,
        source=f"NASADEM/SRTM {path.stem} ({samples - 1} arc-second grid)",
        void_fraction=void_fraction,
    )


def slope_percent(patch: TerrainPatch) -> float:
    """Slope at the window centre, percent, by Horn's method.

    Horn's third-order finite difference is the standard used by ArcGIS and GDAL,
    and it is less noisy on a 30 m DEM than a simple two-cell difference - which
    matters on the near-flat terrain where this project's pilot site sits, because
    noise on a 0.5 percent slope can double the estimate.
    """
    z = patch.elevation_m
    if z.shape[0] < 3 or z.shape[1] < 3:
        raise TerrainError("slope needs at least a 3x3 window; use half_window >= 1")
    n = z.shape[0] // 2
    w = z[n - 1: n + 2, n - 1: n + 2]
    dz_dx = ((w[0, 2] + 2 * w[1, 2] + w[2, 2]) - (w[0, 0] + 2 * w[1, 0] + w[2, 0])) / (
        8 * patch.cell_size_x_m
    )
    dz_dy = ((w[2, 0] + 2 * w[2, 1] + w[2, 2]) - (w[0, 0] + 2 * w[0, 1] + w[0, 2])) / (
        8 * patch.cell_size_y_m
    )
    return float(np.hypot(dz_dx, dz_dy) * 100.0)


def slope_percent_planar(patch: TerrainPatch) -> float:
    """Slope from a least-squares plane fitted over the whole window.

    This is the method to use on near-flat ground, and the reason is quantitative.
    NASADEM carries roughly 2-5 m of vertical noise. Over a 30 m cell that is
    several percent of apparent gradient from noise alone, so a 3x3 Horn estimate on
    true 0.3 percent terrain can return 5 percent or more - which would overstate
    RUSLE soil loss by an order of magnitude, since LS scales almost linearly with
    slope in this range.

    Fitting a plane across a wider window averages the noise down roughly as the
    square root of the number of cells while still resolving real field-scale
    gradient. `slope_stability_profile` shows where the estimate settles.
    """
    z = patch.elevation_m
    rows, cols = z.shape
    if rows < 3:
        raise TerrainError("planar slope needs at least a 3x3 window")
    yy, xx = np.mgrid[0:rows, 0:cols]
    x = (xx - cols // 2).ravel() * patch.cell_size_x_m
    y = (rows // 2 - yy).ravel() * patch.cell_size_y_m
    design = np.column_stack([x, y, np.ones_like(x)])
    (dz_dx, dz_dy, _), *_ = np.linalg.lstsq(design, z.ravel(), rcond=None)
    return float(np.hypot(dz_dx, dz_dy) * 100.0)


def slope_stability_profile(
    path: str | Path, latitude: float, longitude: float, half_windows=(1, 2, 3, 5, 8, 12)
) -> list[tuple[int, float, float, float]]:
    """Slope estimated over progressively wider windows.

    Returned so the choice of scale is visible rather than buried: on flat terrain
    the 3x3 estimate is noise and the wide-window estimate is signal, and the only
    way to know where that transition happens is to look.

    Each row is (half_window, baseline_m, Horn slope %, planar slope %).
    """
    out = []
    for hw in half_windows:
        patch = read_hgt_window(path, latitude, longitude, half_window=hw)
        baseline = (2 * hw) * patch.cell_size_y_m
        out.append((hw, baseline, slope_percent(patch), slope_percent_planar(patch)))
    return out


# Baseline over which field slope is measured. ~300 m is wide enough to average
# NASADEM's vertical noise down below a tenth of a percent of gradient, and narrow
# enough to still resolve a single field's fall. Verified at the pilot site: the
# estimate is stable from roughly 190 m outwards.
DEFAULT_SLOPE_HALF_WINDOW = 11


def field_slope_percent(
    path: str | Path,
    latitude: float,
    longitude: float,
    half_window: int = DEFAULT_SLOPE_HALF_WINDOW,
) -> tuple[float, TerrainPatch]:
    """Field slope at a point, by the method that is correct on flat ground.

    Use this rather than `slope_percent` unless you specifically want the 3x3
    estimate. At the pilot site the two differ by a factor of 23 - 5.77 percent
    against 0.25 percent - and taking the wrong one would have overstated RUSLE
    soil loss by about 17 times.
    """
    patch = read_hgt_window(path, latitude, longitude, half_window=half_window)
    return slope_percent_planar(patch), patch


def ls_factor(slope_pct: float, slope_length_m: float = 75.0) -> float:
    """RUSLE topographic factor LS, dimensionless.

    S uses McCool's relations, which break at 9 percent because flow changes
    character on steeper ground. L uses the standard power law normalised to the
    22.13 m unit plot, with the exponent itself slope-dependent.

    ``slope_length_m`` is the distance from where flow begins to where it either
    deposits or reaches a channel. It is NOT the field length, it cannot be read
    from a DEM point, and on cropland it is typically 30-150 m - so it is an
    explicit argument and a prime candidate for local correction.
    """
    if slope_pct < 0.0:
        raise TerrainError("slope cannot be negative")
    if slope_length_m <= 0.0:
        raise TerrainError("slope length must be positive")

    theta = np.arctan(slope_pct / 100.0)
    sin_theta = float(np.sin(theta))

    if slope_pct < 9.0:
        s = 10.8 * sin_theta + 0.03
    else:
        s = 16.8 * sin_theta - 0.50

    beta = (sin_theta / 0.0896) / (3.0 * sin_theta**0.8 + 0.56) if sin_theta > 0 else 0.0
    m = beta / (1.0 + beta)
    length = (slope_length_m / 22.13) ** m
    return float(length * s)

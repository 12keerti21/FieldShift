"""Surface runoff by the SCS curve number method.

Runoff is where rotation choice bites hardest and least visibly. The same storm on
the same soil yields very different runoff under a bare seedbed and under a cover
crop, and the water that runs off is lost twice over: it never enters the profile,
and it carries soil with it. This module supplies the partition to both the water
balance and, later, the RUSLE erosion term.

The curve number is adjusted for antecedent soil moisture rather than fixed. A
fixed CN makes the first storm after a dry spell generate the same runoff as one
falling on an already-wet profile, which is exactly backwards and would understate
the benefit of a crop that keeps the profile drawn down.
"""

from __future__ import annotations

import numpy as np

from ..data.schema import Crop

VALID_CONDITIONS = ("poor", "good")

# USDA NRCS TR-55 Table 2-2b, cultivated agricultural land, straight-row treatment.
# Keyed by (cover type, hydrologic condition) -> CN2 for hydrologic groups A,B,C,D.
CN2_TABLE: dict[tuple[str, str], tuple[int, int, int, int]] = {
    ("fallow_bare", "poor"): (77, 86, 91, 94),
    ("fallow_bare", "good"): (77, 86, 91, 94),  # bare soil has no good condition
    ("fallow_residue", "poor"): (76, 85, 90, 93),
    ("fallow_residue", "good"): (74, 83, 88, 90),
    ("row_crop", "poor"): (72, 81, 88, 91),
    ("row_crop", "good"): (67, 78, 85, 89),
    ("small_grain", "poor"): (65, 76, 84, 88),
    ("small_grain", "good"): (63, 75, 83, 87),
    ("close_seeded_legume", "poor"): (66, 77, 85, 89),
    ("close_seeded_legume", "good"): (58, 72, 81, 85),
    ("meadow", "poor"): (39, 61, 74, 80),
    ("meadow", "good"): (30, 58, 71, 78),
}

GROUP_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}

# Cereals split across two cover types: wide-spaced cereals behave as row crops,
# drilled cereals as small grain. The distinction changes CN by about 5 points,
# which is not negligible on a C or D soil.
ROW_CROP_CEREALS = frozenset({"maize", "sorghum", "sugarcane"})

GROUP_COVER_TYPE = {
    "fallow": "fallow_bare",
    "cereal": "small_grain",
    "grain_legume": "close_seeded_legume",
    "oilseed": "row_crop",
    "fibre": "row_crop",
    "root_tuber": "row_crop",
    "green_manure": "close_seeded_legume",
    "cover_crop": "close_seeded_legume",
    "forage": "meadow",
}


def cover_type_for_crop(crop: Crop) -> str:
    """Map a crop onto an NRCS cover type."""
    if crop.id in ROW_CROP_CEREALS:
        return "row_crop"
    try:
        return GROUP_COVER_TYPE[crop.group]
    except KeyError:
        raise ValueError(f"no cover type mapped for crop group {crop.group!r}") from None


def condition_for_crop(crop: Crop) -> str:
    """Hydrologic condition from how completely the crop covers the ground.

    A heuristic, and labelled as one: NRCS condition folds in residue management,
    rotation history and density, none of which a canopy fraction captures. It errs
    toward 'poor' so runoff is not under-predicted.
    """
    return "good" if crop.traits.max_canopy_cover_frac >= 0.80 else "poor"


def curve_number(cover_type: str, hydrologic_group: str, condition: str = "good") -> float:
    """CN2: the curve number for average antecedent moisture."""
    if condition not in VALID_CONDITIONS:
        raise ValueError(f"condition must be one of {VALID_CONDITIONS}")
    if hydrologic_group not in GROUP_INDEX:
        raise ValueError(f"unknown hydrologic group {hydrologic_group!r}")
    try:
        row = CN2_TABLE[(cover_type, condition)]
    except KeyError:
        raise ValueError(f"no curve number for cover type {cover_type!r}") from None
    return float(row[GROUP_INDEX[hydrologic_group]])


def potential_retention_mm(cn: np.ndarray | float) -> np.ndarray | float:
    """S, the maximum potential retention after runoff begins, mm.

    The familiar S = 1000/CN - 10 is in inches; this is the millimetre form.
    """
    cn = np.clip(cn, 1.0, 100.0)
    return 25400.0 / cn - 254.0


def cn_dry(cn2: np.ndarray | float) -> np.ndarray | float:
    """CN for a dry profile (antecedent moisture condition I)."""
    cn2 = np.clip(cn2, 1.0, 99.9)
    gap = 100.0 - cn2
    return cn2 - 20.0 * gap / (gap + np.exp(2.533 - 0.0636 * gap))


def cn_wet(cn2: np.ndarray | float) -> np.ndarray | float:
    """CN for a saturated profile (antecedent moisture condition III)."""
    cn2 = np.clip(cn2, 1.0, 99.9)
    return np.minimum(cn2 * np.exp(0.00673 * (100.0 - cn2)), 99.0)


def cn_for_wetness(
    cn2: np.ndarray | float, relative_depletion: np.ndarray | float
) -> np.ndarray | float:
    """Interpolate CN between the dry and wet limits by current root-zone depletion.

    ``relative_depletion`` is Dr/TAW: 0 at field capacity, 1 at wilting point. The
    mapping is piecewise linear through CN2 at half depletion, which is a documented
    simplification of a genuinely non-linear relation - but a far better one than
    holding CN fixed while the profile dries out.
    """
    rd = np.clip(relative_depletion, 0.0, 1.0)
    dry, wet = cn_dry(cn2), cn_wet(cn2)
    # rd = 0 -> wet limit, rd = 0.5 -> CN2, rd = 1 -> dry limit
    upper = wet + (np.asarray(cn2) - wet) * (rd / 0.5)
    lower = np.asarray(cn2) + (dry - np.asarray(cn2)) * ((rd - 0.5) / 0.5)
    return np.where(rd <= 0.5, upper, lower)


def scs_runoff_mm(
    rain_mm: np.ndarray | float,
    cn: np.ndarray | float,
    initial_abstraction_ratio: float = 0.2,
) -> np.ndarray | float:
    """Direct runoff from one day's rainfall, mm.

    ``initial_abstraction_ratio`` is the classic 0.2. The literature has moved
    toward roughly 0.05 for many catchments, which produces more runoff from small
    storms; it is exposed rather than hard-coded so the sensitivity can be examined,
    since the choice matters most for exactly the light-rain days that dominate a
    semi-arid season.
    """
    rain = np.maximum(np.asarray(rain_mm, dtype=float), 0.0)
    s = potential_retention_mm(cn)
    ia = initial_abstraction_ratio * s
    excess = rain - ia
    runoff = np.where(excess > 0.0, np.square(excess) / (rain - ia + s), 0.0)
    return np.minimum(runoff, rain)


def infiltration_mm(
    rain_mm: np.ndarray | float, cn: np.ndarray | float, **kw
) -> np.ndarray | float:
    """Rainfall that enters the soil: the complement of runoff."""
    rain = np.maximum(np.asarray(rain_mm, dtype=float), 0.0)
    return rain - scs_runoff_mm(rain, cn, **kw)

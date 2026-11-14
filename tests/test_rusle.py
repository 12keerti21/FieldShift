"""RUSLE soil loss, and the wind-erosion gap it cannot see.

Two findings this suite protects.

**A unit error worth 7.6x.** The Williams/EPIC erodibility equation returns K in US
customary units. RUSLE in SI needs t ha h / (ha MJ mm), a factor of 0.1317 smaller.
Without it, K for our pilot soil came out at 0.3526 against a published silt-loam
range of 0.03-0.045 - and soil loss with it, which would have read as a serious
erosion problem on a field that has none.

**RUSLE reports on the wrong mechanism at this site.** The pilot field slopes 0.25
percent, so LS is 0.060 and water erosion is inherently negligible: even a bare
fallow reaches only a third of tolerable soil loss. This is the Dust Bowl region and
the dominant process is wind, which RUSLE does not model at all. A low soil loss
here is a statement about topography, not about management, and the module says so
in the result rather than in a footnote.
"""

import numpy as np
import pytest

from fieldshift.agro.rusle import (
    K_PLAUSIBLE_SI,
    K_US_TO_SI,
    PRACTICE_FACTORS,
    WIND_THRESHOLD_MS,
    RusleError,
    cover_management_factor,
    erosivity,
    erosivity_from_annual_precip,
    erosivity_from_mfi,
    modified_fournier_index,
    practice_factor,
    soil_erodibility,
    soil_loss,
    wind_exposure_index,
)

# The real Finney County monthly rainfall climatology, 1984-2024 POWER record.
FINNEY_MONTHLY = np.array(
    [12.5, 13.2, 31.4, 46.3, 76.1, 82.8, 87.2, 74.3, 43.0, 39.3, 16.6, 17.3]
)
FINNEY_SOIL = dict(sand_pct=29.0, silt_pct=53.0, clay_pct=18.0, organic_carbon_pct=0.93)
FLAT_LS = 0.060


# --- R, erosivity -----------------------------------------------------------------


def test_mfi_weights_concentrated_rainfall_more_heavily():
    """Two sites with the same annual total are not equally erosive."""
    even = np.full(12, 50.0)
    concentrated = np.array([0, 0, 0, 0, 0, 300.0, 300.0, 0, 0, 0, 0, 0])
    assert even.sum() == concentrated.sum() == 600.0
    assert modified_fournier_index(concentrated) > 3 * modified_fournier_index(even)


def test_mfi_handles_a_dry_year_and_rejects_bad_input():
    assert modified_fournier_index(np.zeros(12)) == 0.0
    with pytest.raises(RusleError, match="12 monthly"):
        modified_fournier_index(np.zeros(6))
    with pytest.raises(RusleError, match="negative"):
        modified_fournier_index(np.array([-1.0] + [10.0] * 11))


def test_erosivity_rises_with_rainfall():
    values = [erosivity_from_annual_precip(p) for p in (200, 400, 600, 800)]
    assert all(a < b for a, b in zip(values, values[1:]))
    assert all(v > 0 for v in values)


def test_erosivity_is_continuous_across_the_regression_break():
    """Renard & Freimund switch form at 850 mm; the join must not be a cliff."""
    below = erosivity_from_annual_precip(849.0)
    above = erosivity_from_annual_precip(851.0)
    assert abs(above - below) / below < 0.25


def test_both_estimates_are_returned_with_their_spread():
    r, method, (lo, hi) = erosivity(FINNEY_MONTHLY)
    assert lo <= r <= hi
    assert lo < hi, "the two methods should not coincide exactly"
    assert "not true EI30" in method
    assert "Renard" in method


def test_the_site_erosivity_is_plausible_for_the_high_plains():
    r, _, _ = erosivity(FINNEY_MONTHLY)
    # Western Kansas R is roughly 1000-1800 in SI units.
    assert 800.0 < r < 2000.0


def test_mfi_is_preferred_but_annual_is_available():
    mfi_r, mfi_label, _ = erosivity(FINNEY_MONTHLY, prefer="mfi")
    ann_r, ann_label, _ = erosivity(FINNEY_MONTHLY, prefer="annual")
    assert mfi_r != ann_r
    assert "MFI" in mfi_label and "annual" in ann_label
    with pytest.raises(RusleError, match="prefer"):
        erosivity(FINNEY_MONTHLY, prefer="hourly")


# --- K, and THE unit error --------------------------------------------------------


def test_k_is_returned_in_si_and_the_conversion_is_applied():
    k = soil_erodibility(**FINNEY_SOIL)
    assert K_US_TO_SI == pytest.approx(0.1317)
    # Published silt-loam K in SI is 0.03-0.045; without the conversion this was
    # 0.3526, which would have inflated soil loss 7.6-fold.
    assert 0.03 <= k <= 0.050, k


def test_every_texture_lands_in_the_published_si_range():
    cases = {
        "sand": (92, 5, 3, 0.5),
        "loamy sand": (82, 12, 6, 0.6),
        "sandy loam": (65, 25, 10, 0.8),
        "loam": (42, 40, 18, 1.0),
        "silt loam": (29, 53, 18, 0.93),
        "silt": (7, 87, 6, 0.9),
        "clay loam": (32, 34, 34, 1.0),
        "clay": (22, 20, 58, 1.2),
    }
    lo, hi = K_PLAUSIBLE_SI
    for name, (sa, si, cl, oc) in cases.items():
        k = soil_erodibility(sa, si, cl, oc)
        assert lo <= k <= hi, f"{name}: {k:.4f}"


def test_relative_erodibility_follows_texture():
    """Silt is the most erodible, sand the least - the classic ordering."""
    sand = soil_erodibility(92, 5, 3, 0.5)
    silt = soil_erodibility(7, 87, 6, 0.9)
    clay = soil_erodibility(22, 20, 58, 1.2)
    assert sand < clay < silt


def test_organic_carbon_is_a_second_route_to_less_erosion():
    """A carbon-building rotation lowers erodibility as well as raising cover.

    Because RothC moves this carbon over the simulation, K is not a static
    property of the field.
    """
    low = soil_erodibility(29, 53, 18, 0.5)
    high = soil_erodibility(29, 53, 18, 2.5)
    assert high < low
    assert (low - high) / low > 0.15  # a real effect, not a rounding artefact


def test_an_implausible_k_is_refused_rather_than_returned():
    """The guard that would have caught the missing unit conversion."""
    with pytest.raises(RusleError, match="fractions sum"):
        soil_erodibility(50, 50, 50, 1.0)
    with pytest.raises(RusleError, match="negative organic carbon"):
        soil_erodibility(29, 53, 18, -1.0)


def test_the_guard_message_names_the_likely_cause():
    try:
        soil_erodibility(50, 50, 50, 1.0)
    except RusleError as exc:
        assert "sum" in str(exc)


# --- C, cover ---------------------------------------------------------------------


def test_surface_residue_matters_more_than_canopy():
    """Residue intercepts drops where detachment happens; a canopy does not."""
    residue_only = cover_management_factor(0.0, 0.60)
    canopy_only = cover_management_factor(0.60, 0.0, canopy_height_m=0.5)
    assert residue_only < canopy_only


def test_c_falls_monotonically_with_residue_and_reaches_one_when_bare():
    assert cover_management_factor(0.0, 0.0) == pytest.approx(1.0)
    values = [cover_management_factor(0.0, f) for f in (0.0, 0.3, 0.6, 0.9)]
    assert all(a > b for a, b in zip(values, values[1:]))
    assert values[-1] < 0.06  # 90% residue cuts C by more than 94%


def test_a_growing_crop_over_residue_is_the_strongest_protection():
    assert cover_management_factor(0.95, 0.60) < cover_management_factor(0.0, 0.60)


def test_invalid_cover_inputs_are_refused():
    with pytest.raises(RusleError, match="canopy cover"):
        cover_management_factor(1.5, 0.3)
    with pytest.raises(RusleError, match="residue cover"):
        cover_management_factor(0.3, -0.1)
    with pytest.raises(RusleError, match="canopy height"):
        cover_management_factor(0.3, 0.3, canopy_height_m=-1.0)


def test_practice_factors_are_ordered_and_validated():
    assert practice_factor("up_and_down_slope") == 1.0
    ordered = [
        PRACTICE_FACTORS[p]
        for p in ("up_and_down_slope", "cross_slope", "contour",
                  "contour_strip_crop", "terraced")
    ]
    assert ordered == sorted(ordered, reverse=True)
    with pytest.raises(RusleError, match="unknown practice"):
        practice_factor("prayer")


# --- assembly ---------------------------------------------------------------------


def _loss(residue, canopy=0.0, ls=FLAT_LS, **kw):
    return soil_loss(
        monthly_precip_mm=FINNEY_MONTHLY, ls=ls, canopy_cover_fraction=canopy,
        residue_cover_fraction=residue, **FINNEY_SOIL, **kw
    )


def test_soil_loss_is_the_product_of_the_five_factors():
    f = _loss(0.30)
    assert f.soil_loss_t_ha_yr == pytest.approx(
        f.r_mj_mm_ha_h_yr * f.k_t_ha_h_per_ha_mj_mm * f.ls * f.c * f.p
    )


def test_cover_reduces_soil_loss_steeply():
    bare = _loss(0.05).soil_loss_t_ha_yr
    covered = _loss(0.60).soil_loss_t_ha_yr
    assert covered < bare / 4.0


def test_the_erosivity_spread_is_reported_as_a_range():
    f = _loss(0.30)
    lo, hi = f.soil_loss_range_t_ha_yr
    assert lo < f.soil_loss_t_ha_yr <= hi + 1e-9
    assert lo > 0.0


def test_every_result_carries_the_ei30_and_water_only_caveats():
    f = _loss(0.30)
    joined = " ".join(f.notes)
    assert "EI30" in joined
    assert "water erosion only" in joined


def test_flat_terrain_raises_an_explicit_note():
    """A low soil loss on flat ground is topography, not good management."""
    f = _loss(0.05, ls=FLAT_LS)
    assert any("inherently small" in n for n in f.notes)
    assert any("wind should be checked" in n for n in f.notes)
    steep = _loss(0.05, ls=0.72)
    assert not any("inherently small" in n for n in steep.notes)


def test_slope_dominates_the_answer_at_this_site():
    """Why the DEM slope error mattered: LS carries the result."""
    flat = _loss(0.05, ls=FLAT_LS).soil_loss_t_ha_yr
    moderate = _loss(0.05, ls=0.72).soil_loss_t_ha_yr
    assert moderate / flat > 10.0


def test_rusle_declares_this_field_safe():
    """The misleading result, pinned - it is the reason the wind flag exists."""
    worst = _loss(0.05).soil_loss_t_ha_yr
    tolerable = 11.0
    assert worst < tolerable / 2.0


# --- wind: the mechanism RUSLE misses --------------------------------------------


def test_wind_exposure_counts_erosive_days_on_bare_ground():
    wind = np.array([2.0, 7.0, 8.0, 3.0, 9.0])
    cover = np.array([0.8, 0.8, 0.1, 0.1, 0.1])
    ex = wind_exposure_index(wind, cover)
    assert ex.erosive_wind_days == 3           # 7, 8, 9 m/s
    assert ex.bare_erosive_wind_days == 2      # the 8 and 9 on bare ground
    assert ex.total_days == 5
    assert ex.fraction_of_erosive_wind_on_bare_soil == pytest.approx(2 / 3)


def test_the_index_rises_with_both_wind_and_exposure():
    wind = np.full(100, 9.0)
    bare = wind_exposure_index(wind, np.full(100, 0.05)).exposure_index
    covered = wind_exposure_index(wind, np.full(100, 0.90)).exposure_index
    assert bare > covered
    calm = wind_exposure_index(np.full(100, 3.0), np.full(100, 0.05)).exposure_index
    assert calm == 0.0  # below the saltation threshold, nothing moves


def test_cover_ranks_rotations_where_rusle_cannot():
    """The decision content: RUSLE calls all three safe, wind separates them."""
    rng = np.random.default_rng(0)
    wind = np.abs(rng.normal(3.6, 2.0, 3650))
    always_bare = wind_exposure_index(wind, np.full(3650, 0.05)).exposure_index
    sometimes = wind_exposure_index(wind, np.full(3650, 0.40)).exposure_index
    never_bare = wind_exposure_index(wind, np.full(3650, 0.85)).exposure_index
    assert always_bare > sometimes > never_bare
    assert always_bare / never_bare > 3.0


def test_the_warning_fires_only_when_bare_and_windy_coincide():
    wind = np.array([9.0, 9.0, 9.0])
    assert wind_exposure_index(wind, np.full(3, 0.05)).warning() is not None
    assert wind_exposure_index(wind, np.full(3, 0.95)).warning() is None
    assert wind_exposure_index(np.full(3, 2.0), np.full(3, 0.05)).warning() is None


def test_the_warning_states_what_the_model_cannot_do():
    ex = wind_exposure_index(np.full(5, 9.0), np.full(5, 0.05))
    text = ex.warning()
    assert "WATER erosion only" in text
    assert "Wind Erosion Equation" in text
    assert "does not have" in text


def test_wind_inputs_are_validated():
    with pytest.raises(RusleError, match="same length"):
        wind_exposure_index(np.zeros(5), np.zeros(4))
    with pytest.raises(RusleError, match="ground cover"):
        wind_exposure_index(np.zeros(3), np.array([0.5, 1.5, 0.5]))


def test_threshold_matches_dry_bare_medium_soil():
    assert WIND_THRESHOLD_MS == pytest.approx(6.0)

"""RUSLE soil loss, and an honest statement of what it does not cover.

    A = R x K x LS x C x P

Each factor is sourced differently and carries different confidence, which matters
more than the product: R comes from a regional regression, K from measured soil
texture and carbon, LS from a DEM, C from simulated crop and residue cover, and P
from a management choice. Presenting A as a single number hides that, so every
result here carries its factors and their provenance.

Two limits are stated in the result rather than in a footnote.

**The R factor is not true EI30.** Rainfall erosivity properly requires the kinetic
energy of 30-minute rainfall intensity. Retrieving sub-hourly precipitation as a
point time series was not achievable here - the GES DISC Data Rods endpoint has
moved and granule-by-granule IMERG retrieval is tens of thousands of requests - so
R is estimated from the Renard & Freimund (1994) regressions on annual and monthly
precipitation. Those were fitted on United States data, which suits this project's
Tier 1 site, but they are regressions. Both available forms are computed and their
spread is reported as the uncertainty, because a single estimate would imply a
precision that is not there.

**RUSLE models water erosion only.** On the US High Plains that is the wrong
mechanism for the bare season: winter there is dry, so the erosive rainfall during
the uncovered window is negligible and RUSLE returns a reassuring near-zero soil
loss. This is the Dust Bowl region, and the dominant process is wind. A soil-loss
figure that omits it is not conservative, it is misleading, so
`wind_exposure_index` computes an explicit flag from observed wind and simulated
cover and the result says plainly that RUSLE does not account for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Surface-cover subfactor coefficient, per percent of ground covered by residue.
# RUSLE quotes 0.025-0.05 depending on how much of the runoff is rill rather than
# interrill; 0.035 is the common cropland midpoint.
SURFACE_COVER_COEFFICIENT = 0.035

# Support-practice factors, RUSLE Table. Contouring effectiveness falls away on
# steep ground, which is why it is slope-dependent.
PRACTICE_FACTORS = {
    "up_and_down_slope": 1.00,
    "cross_slope": 0.75,
    "contour": 0.50,
    "contour_strip_crop": 0.25,
    "terraced": 0.15,
}

# The Williams (1995) / EPIC erodibility equation returns K in US customary units
# (t ac h per hundreds of ac ft tonf in). RUSLE in SI needs
# t ha h / (ha MJ mm), and the conversion is 0.1317. Omitting it inflates K - and
# therefore soil loss - by a factor of 7.6, which is large enough to look like a
# real erosion problem on a field that does not have one.
K_US_TO_SI = 0.1317

# Published K ranges in SI for sanity checking: sand ~0.01, silt loam ~0.03-0.045,
# silt ~0.06, clay ~0.03.
K_PLAUSIBLE_SI = (0.005, 0.085)

# Wind erosion: the threshold friction velocity for a dry, bare, medium-textured
# soil corresponds to roughly 6 m/s at 2 m height. Below it, saltation does not
# begin; above it, flux rises with roughly the cube of wind speed.
WIND_THRESHOLD_MS = 6.0


class RusleError(ValueError):
    pass


@dataclass(frozen=True)
class RusleFactors:
    """The five factors, kept separate because they carry different confidence."""

    r_mj_mm_ha_h_yr: float
    k_t_ha_h_per_ha_mj_mm: float
    ls: float
    c: float
    p: float
    r_method: str
    r_spread: tuple[float, float]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def soil_loss_t_ha_yr(self) -> float:
        return (
            self.r_mj_mm_ha_h_yr
            * self.k_t_ha_h_per_ha_mj_mm
            * self.ls
            * self.c
            * self.p
        )

    @property
    def soil_loss_range_t_ha_yr(self) -> tuple[float, float]:
        """Range implied by the two R estimates, not a confidence interval.

        It bounds one source of uncertainty - the erosivity regression - and says
        nothing about error in K, LS or C.
        """
        scale = self.k_t_ha_h_per_ha_mj_mm * self.ls * self.c * self.p
        return (self.r_spread[0] * scale, self.r_spread[1] * scale)

    def describe(self) -> str:
        lo, hi = self.soil_loss_range_t_ha_yr
        lines = [
            f"soil loss {self.soil_loss_t_ha_yr:.2f} t/ha/yr "
            f"(erosivity range {lo:.2f}-{hi:.2f})",
            f"  R = {self.r_mj_mm_ha_h_yr:.0f}  [{self.r_method}]",
            f"  K = {self.k_t_ha_h_per_ha_mj_mm:.4f}  LS = {self.ls:.3f}  "
            f"C = {self.c:.4f}  P = {self.p:.2f}",
        ]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


# ---------------------------------------------------------------- R, erosivity


def modified_fournier_index(monthly_precip_mm: np.ndarray) -> float:
    """MFI: sum of squared monthly rainfall over the annual total.

    It weights concentrated rainfall more heavily than evenly spread rainfall, which
    is the feature that makes it a usable proxy for erosivity.
    """
    p = np.asarray(monthly_precip_mm, dtype=float)
    if p.size != 12:
        raise RusleError(f"expected 12 monthly values, got {p.size}")
    if np.any(p < 0):
        raise RusleError("negative monthly rainfall")
    annual = float(p.sum())
    if annual <= 0.0:
        return 0.0
    return float((p**2).sum() / annual)


def erosivity_from_annual_precip(annual_precip_mm: float) -> float:
    """R from annual rainfall, Renard & Freimund (1994)."""
    if annual_precip_mm < 0.0:
        raise RusleError("negative annual rainfall")
    p = annual_precip_mm
    if p <= 850.0:
        return float(0.0483 * p**1.610)
    return float(587.8 - 1.219 * p + 0.004105 * p**2)


def erosivity_from_mfi(mfi: float) -> float:
    """R from the Modified Fournier Index, Renard & Freimund (1994)."""
    if mfi < 0.0:
        raise RusleError("negative MFI")
    if mfi <= 55.0:
        return float(0.7397 * mfi**1.847)
    return float(95.77 - 6.081 * mfi + 0.4770 * mfi**2)


def erosivity(
    monthly_precip_mm: np.ndarray, prefer: str = "mfi"
) -> tuple[float, str, tuple[float, float]]:
    """R with both estimates and their spread.

    Returns (chosen R, method label, (low, high)). MFI is preferred by default
    because it responds to how concentrated the rainfall is, and two sites with the
    same annual total but different seasonality are not equally erosive - which is
    precisely the distinction a rotation tool needs.
    """
    if prefer not in ("mfi", "annual"):
        raise RusleError("prefer must be 'mfi' or 'annual'")
    annual = float(np.asarray(monthly_precip_mm, dtype=float).sum())
    r_annual = erosivity_from_annual_precip(annual)
    r_mfi = erosivity_from_mfi(modified_fournier_index(monthly_precip_mm))
    spread = (min(r_annual, r_mfi), max(r_annual, r_mfi))
    chosen = r_mfi if prefer == "mfi" else r_annual
    label = (
        f"Renard & Freimund 1994 from {'MFI' if prefer == 'mfi' else 'annual rainfall'}; "
        "not true EI30"
    )
    return chosen, label, spread


# ---------------------------------------------------------------- K, erodibility


def soil_erodibility(
    sand_pct: float, silt_pct: float, clay_pct: float, organic_carbon_pct: float
) -> float:
    """K by the Williams (1995) / EPIC equation, SI units.

    Four multiplicative terms: coarse sand reduces erodibility, a high silt-to-clay
    ratio raises it, organic carbon binds aggregates and lowers it, and very sandy
    soils are reduced again. The organic carbon term is why a carbon-building
    rotation reduces erosion through a second pathway beyond surface cover - and
    because RothC changes that carbon over the simulation, erodibility is not static.
    """
    total = sand_pct + silt_pct + clay_pct
    if not 90.0 <= total <= 110.0:
        raise RusleError(f"texture fractions sum to {total:g}, not ~100")
    if organic_carbon_pct < 0.0:
        raise RusleError("negative organic carbon")
    scale = 100.0 / total
    san, sil, cla = sand_pct * scale, silt_pct * scale, clay_pct * scale
    c = organic_carbon_pct

    f_csand = 0.2 + 0.3 * np.exp(-0.0256 * san * (1.0 - sil / 100.0))
    f_cl_si = (sil / max(cla + sil, 1e-9)) ** 0.3
    f_orgc = 1.0 - (0.25 * c) / (c + np.exp(3.72 - 2.95 * c))
    sn1 = 1.0 - san / 100.0
    f_hisand = 1.0 - (0.7 * sn1) / (sn1 + np.exp(-5.51 + 22.9 * sn1))
    k_si = float(f_csand * f_cl_si * f_orgc * f_hisand * K_US_TO_SI)

    lo, hi = K_PLAUSIBLE_SI
    if not lo <= k_si <= hi:
        raise RusleError(
            f"K came out at {k_si:.4f} SI, outside the published range {lo}-{hi} for "
            f"sand/silt/clay {san:.0f}/{sil:.0f}/{cla:.0f} with {c:g}% organic "
            "carbon. The usual cause is a missing or doubled unit conversion."
        )
    return k_si


# ---------------------------------------------------------------- C, cover


def cover_management_factor(
    canopy_cover_fraction: float,
    residue_cover_fraction: float,
    canopy_height_m: float = 0.5,
    surface_coefficient: float = SURFACE_COVER_COEFFICIENT,
) -> float:
    """C from the canopy and surface-cover subfactors.

    Surface residue dominates: it intercepts raindrops at the soil surface where
    detachment happens, while a canopy only reduces the energy of drops that then
    fall from it. Sixty percent residue cover cuts C by about an eighth; sixty
    percent canopy at half a metre cuts it by about a third.
    """
    for name, value in (
        ("canopy", canopy_cover_fraction),
        ("residue", residue_cover_fraction),
    ):
        if not 0.0 <= value <= 1.0:
            raise RusleError(f"{name} cover fraction {value} outside 0-1")
    if canopy_height_m < 0.0:
        raise RusleError("negative canopy height")

    cc = 1.0 - canopy_cover_fraction * np.exp(-0.34 * canopy_height_m)
    sc = np.exp(-surface_coefficient * residue_cover_fraction * 100.0)
    return float(cc * sc)


def practice_factor(practice: str = "up_and_down_slope") -> float:
    try:
        return PRACTICE_FACTORS[practice]
    except KeyError:
        raise RusleError(
            f"unknown practice {practice!r}; have {sorted(PRACTICE_FACTORS)}"
        ) from None


# ---------------------------------------------------------------- wind, the gap


@dataclass(frozen=True)
class WindExposure:
    """A flag, not a soil-loss estimate.

    Quantifying wind erosion properly needs the Wind Erosion Equation or RWEQ, plus
    surface roughness, aggregate size distribution and unsheltered field length -
    none of which this project models. What this does provide is enough evidence to
    say that RUSLE is reporting on the wrong mechanism, which is more useful than a
    confident number for the mechanism that is not operating.
    """

    erosive_wind_days: int
    bare_erosive_wind_days: int
    total_days: int
    mean_wind_ms: float
    exposure_index: float

    @property
    def fraction_of_erosive_wind_on_bare_soil(self) -> float:
        return (
            self.bare_erosive_wind_days / self.erosive_wind_days
            if self.erosive_wind_days
            else 0.0
        )

    def warning(self) -> str | None:
        if self.bare_erosive_wind_days == 0:
            return None
        return (
            f"{self.bare_erosive_wind_days} of {self.total_days} days combined wind "
            f"above {WIND_THRESHOLD_MS:g} m/s with little ground cover. RUSLE models "
            "WATER erosion only and does not account for this. Where the bare season "
            "is also the dry season, a low RUSLE soil loss should not be read as a "
            "low erosion risk - the dominant mechanism is wind, and quantifying it "
            "needs the Wind Erosion Equation and field-level roughness data this "
            "model does not have."
        )


def wind_exposure_index(
    wind_2m_ms: np.ndarray,
    ground_cover_fraction: np.ndarray,
    *,
    threshold_ms: float = WIND_THRESHOLD_MS,
    bare_threshold: float = 0.30,
) -> WindExposure:
    """Co-occurrence of erosive wind and bare ground.

    The index weights excess wind speed cubed, because saltation flux scales roughly
    with the cube of shear velocity, and multiplies by exposed ground. It is an
    ordinal indicator for comparing rotations at one site, not a mass flux.
    """
    wind = np.asarray(wind_2m_ms, dtype=float)
    cover = np.asarray(ground_cover_fraction, dtype=float)
    if wind.shape != cover.shape:
        raise RusleError("wind and cover series must be the same length")
    if np.any(cover < 0) or np.any(cover > 1):
        raise RusleError("ground cover fraction outside 0-1")

    erosive = wind > threshold_ms
    bare = cover < bare_threshold
    excess = np.clip(wind - threshold_ms, 0.0, None)
    index = float(np.sum(excess**3 * (1.0 - cover)))
    return WindExposure(
        erosive_wind_days=int(erosive.sum()),
        bare_erosive_wind_days=int((erosive & bare).sum()),
        total_days=int(wind.size),
        mean_wind_ms=float(wind.mean()),
        exposure_index=index,
    )


# ---------------------------------------------------------------- assembly


def soil_loss(
    *,
    monthly_precip_mm: np.ndarray,
    sand_pct: float,
    silt_pct: float,
    clay_pct: float,
    organic_carbon_pct: float,
    ls: float,
    canopy_cover_fraction: float,
    residue_cover_fraction: float,
    canopy_height_m: float = 0.5,
    practice: str = "up_and_down_slope",
    prefer_r: str = "mfi",
    extra_notes: tuple[str, ...] = (),
) -> RusleFactors:
    """Assemble A = R K LS C P, keeping every factor and its caveats."""
    r, r_method, r_spread = erosivity(monthly_precip_mm, prefer=prefer_r)
    k = soil_erodibility(sand_pct, silt_pct, clay_pct, organic_carbon_pct)
    c = cover_management_factor(
        canopy_cover_fraction, residue_cover_fraction, canopy_height_m
    )
    p = practice_factor(practice)

    notes = [
        "R is a regression on rainfall totals, not measured EI30 from sub-hourly "
        "intensity; treat it as indicative and prefer comparisons between rotations "
        "over the absolute soil-loss figure.",
        "RUSLE accounts for water erosion only.",
        *extra_notes,
    ]
    if ls < 0.1:
        notes.append(
            f"LS is only {ls:.3f}: on terrain this flat water erosion is inherently "
            "small, so a low soil loss here reflects topography rather than good "
            "management, and wind should be checked instead."
        )
    return RusleFactors(
        r_mj_mm_ha_h_yr=r,
        k_t_ha_h_per_ha_mj_mm=k,
        ls=ls,
        c=c,
        p=p,
        r_method=r_method,
        r_spread=r_spread,
        notes=tuple(notes),
    )

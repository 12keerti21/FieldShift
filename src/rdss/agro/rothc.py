"""RothC-26.3 soil organic carbon turnover.

This is the model that makes rotation comparison worth doing. Water stress and
erosion are seasonal; soil carbon is the state variable that remembers what a field
was asked to do over decades, and it is the one a farmer cannot see changing from
one year to the next. RothC is the standard for this - it underpins IPCC Tier 2
soil carbon accounting - and it is simple enough to implement honestly.

Five pools, four of them active, on a monthly timestep:

    DPM  decomposable plant material      k = 10.0  /yr
    RPM  resistant plant material         k =  0.3  /yr
    BIO  microbial biomass                k =  0.66 /yr
    HUM  humified organic matter          k =  0.02 /yr
    IOM  inert organic matter             does not decompose

Three rate modifiers multiply those constants: temperature, soil moisture deficit,
and whether the soil is under a crop. The third is why rotation changes carbon at
all - a covered soil decomposes at 0.6 of the bare rate, so a bare window is a
carbon cost even before erosion is considered.

The clay dependence is the part worth understanding before reading any output. Of
the carbon that decomposes, the fraction lost as CO2 rather than retained as
biomass and humus falls as clay rises, because clay physically protects organic
matter. So the SAME residue input builds more carbon on a heavy soil than a light
one, and a rotation that works on clay may do nothing on sand. That is a real
agronomic conclusion the model produces rather than an assumption fed into it.

Reference: Coleman & Jenkinson, RothC-26.3 model description; Falloon et al. (1998)
for the inert pool estimate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

# Decomposition rate constants, per year (Coleman & Jenkinson).
K_DPM = 10.0
K_RPM = 0.3
K_BIO = 0.66
K_HUM = 0.02

BIO_SHARE_OF_RETAINED = 0.46  # the rest becomes HUM
MIN_MOISTURE_MODIFIER = 0.2
COVERED_RATE_FACTOR = 0.6  # a cropped soil decomposes more slowly than a bare one
TSMD_THRESHOLD_FRACTION = 0.444  # deficit below which moisture does not limit
BARE_SOIL_TSMD_DIVISOR = 1.8  # bare soil cannot dry as far before decay stalls
TEMPERATURE_FLOOR_C = -5.0

# DPM/RPM ratio of incoming plant material, by vegetation type.
DPM_RPM_ARABLE = 1.44
DPM_RPM_GRASSLAND = 0.67
DPM_RPM_WOODLAND = 0.25

# Farmyard manure partitions differently from fresh residue.
FYM_DPM, FYM_RPM, FYM_HUM = 0.49, 0.49, 0.02


class RothCError(ValueError):
    pass


@dataclass(frozen=True)
class RothCPools:
    """Carbon in each pool, t C/ha."""

    dpm: float
    rpm: float
    bio: float
    hum: float
    iom: float

    @property
    def total(self) -> float:
        return self.dpm + self.rpm + self.bio + self.hum + self.iom

    @property
    def active(self) -> float:
        """Everything that can still decompose - the part management can move."""
        return self.dpm + self.rpm + self.bio + self.hum

    def __post_init__(self) -> None:
        for name in ("dpm", "rpm", "bio", "hum", "iom"):
            if getattr(self, name) < -1e-9:
                raise RothCError(f"negative carbon in {name} pool")


@dataclass(frozen=True)
class RothCResult:
    """Monthly carbon trajectory plus the accounting needed to check it."""

    pools: tuple[RothCPools, ...]
    soc_t_ha: np.ndarray
    co2_t_ha: np.ndarray
    tsmd_mm: np.ndarray
    rate_modifier: np.ndarray
    carbon_input_t_ha: np.ndarray
    closure_residual_t_ha: float

    def __len__(self) -> int:
        return len(self.soc_t_ha)

    @property
    def initial_soc(self) -> float:
        return float(self.soc_t_ha[0])

    @property
    def final_soc(self) -> float:
        return float(self.soc_t_ha[-1])

    @property
    def change_t_ha(self) -> float:
        return self.final_soc - self.initial_soc

    def annual_change_t_ha(self, years: float) -> float:
        return self.change_t_ha / years if years > 0 else 0.0


# ---------------------------------------------------------------- rate modifiers


def temperature_modifier(temp_c: np.ndarray | float) -> np.ndarray | float:
    """Rate modifier 'a'. Equals 1.0 near 9.3 degC, and 0 below -5 degC."""
    t = np.asarray(temp_c, dtype=float)
    a = 47.91 / (1.0 + np.exp(106.06 / (t + 18.27)))
    return np.where(t < TEMPERATURE_FLOOR_C, 0.0, a)


def max_tsmd_mm(clay_pct: float, depth_cm: float = 23.0, covered: bool = True) -> float:
    """Maximum topsoil moisture deficit the soil can reach, mm (negative).

    Clay holds more water, so a heavier soil can run a larger deficit before
    decomposition stalls. Bare soil is given a smaller maximum deficit, which is
    RothC's way of saying an uncovered surface stops biological activity sooner.
    """
    if not 0.0 <= clay_pct <= 100.0:
        raise RothCError(f"clay percent {clay_pct} out of range")
    if depth_cm <= 0.0:
        raise RothCError("depth must be positive")
    base = -(20.0 + 1.3 * clay_pct - 0.01 * clay_pct**2) * (depth_cm / 23.0)
    return base if covered else base / BARE_SOIL_TSMD_DIVISOR


def moisture_modifier(accumulated_tsmd_mm: float, max_tsmd: float) -> float:
    """Rate modifier 'b', between 0.2 and 1.0.

    Unlimited while the deficit stays shallower than 44.4 percent of the maximum,
    then falling linearly to 0.2 at the maximum.
    """
    if max_tsmd >= 0.0:
        raise RothCError("max TSMD must be negative")
    threshold = TSMD_THRESHOLD_FRACTION * max_tsmd
    if accumulated_tsmd_mm > threshold:  # both negative; > means a shallower deficit
        return 1.0
    span = max_tsmd - threshold
    if abs(span) < 1e-12:
        return MIN_MOISTURE_MODIFIER
    scaled = (max_tsmd - accumulated_tsmd_mm) / span
    return float(
        np.clip(
            MIN_MOISTURE_MODIFIER + (1.0 - MIN_MOISTURE_MODIFIER) * scaled,
            MIN_MOISTURE_MODIFIER,
            1.0,
        )
    )


def co2_fraction(clay_pct: float) -> float:
    """Share of decomposed carbon lost as CO2 rather than retained in BIO and HUM.

    Falls as clay rises: clay physically protects organic matter, so the same
    residue input builds more carbon on a heavy soil than a light one. This single
    relation is why a carbon-building rotation is soil-specific.
    """
    if not 0.0 <= clay_pct <= 100.0:
        raise RothCError(f"clay percent {clay_pct} out of range")
    x = 1.67 * (1.85 + 1.60 * np.exp(-0.0786 * clay_pct))
    return float(x / (x + 1.0))


def iom_from_soc(soc_t_ha: float) -> float:
    """Inert organic matter from total SOC, Falloon et al. (1998).

    An empirical fit, and treated as such: it is the least defensible number in a
    RothC initialisation, but the pool is inert so it only shifts the baseline, not
    the direction of change.
    """
    if soc_t_ha < 0.0:
        raise RothCError("SOC cannot be negative")
    return 0.049 * soc_t_ha**1.139


# ---------------------------------------------------------------- the step


def rothc_step(
    pools: RothCPools,
    *,
    clay_pct: float,
    temp_c: float,
    rain_mm: float,
    et0_mm: float,
    covered: bool,
    carbon_input_t_ha: float,
    irrigation_mm: float = 0.0,
    dpm_rpm_ratio: float = DPM_RPM_ARABLE,
    fym_input_t_ha: float = 0.0,
    previous_tsmd_mm: float = 0.0,
    depth_cm: float = 23.0,
) -> tuple[RothCPools, float, float, float]:
    """Advance one month. Returns (pools, TSMD, rate modifier, CO2 released).

    RothC's moisture term uses rainfall minus 0.75 x open-pan evaporation. Since
    FAO-56 reference ET is itself about 0.75 of open-pan evaporation, ET0 is passed
    in directly in place of that product rather than inventing a pan conversion.

    ``irrigation_mm`` is added to rainfall. Standard RothC assumes rainfed
    conditions, and omitting irrigation makes an irrigated field look as dry as a
    rainfed one - which understates decomposition and so overstates its carbon
    gain. On the Ogallala, where a maize crop receives several hundred millimetres
    of applied water, that error is large and in the flattering direction.
    """
    if carbon_input_t_ha < 0.0 or fym_input_t_ha < 0.0:
        raise RothCError("carbon input cannot be negative")
    if irrigation_mm < 0.0:
        raise RothCError("irrigation cannot be negative")
    if dpm_rpm_ratio <= 0.0:
        raise RothCError("dpm_rpm_ratio must be positive")

    cap = max_tsmd_mm(clay_pct, depth_cm, covered)
    tsmd = min(0.0, max(previous_tsmd_mm + (rain_mm + irrigation_mm - et0_mm), cap))

    a = float(temperature_modifier(temp_c))
    b = moisture_modifier(tsmd, cap)
    c = COVERED_RATE_FACTOR if covered else 1.0
    rate = a * b * c
    dt = 1.0 / 12.0

    co2_share = co2_fraction(clay_pct)
    retained = 1.0 - co2_share
    to_bio = retained * BIO_SHARE_OF_RETAINED
    to_hum = retained * (1.0 - BIO_SHARE_OF_RETAINED)

    decomposed = {}
    remaining = {}
    for name, k, amount in (
        ("dpm", K_DPM, pools.dpm),
        ("rpm", K_RPM, pools.rpm),
        ("bio", K_BIO, pools.bio),
        ("hum", K_HUM, pools.hum),
    ):
        left = amount * np.exp(-k * rate * dt)
        remaining[name] = float(left)
        decomposed[name] = float(amount - left)

    total_decomposed = sum(decomposed.values())
    co2 = total_decomposed * co2_share

    dpm_share = dpm_rpm_ratio / (1.0 + dpm_rpm_ratio)
    new = RothCPools(
        dpm=remaining["dpm"] + carbon_input_t_ha * dpm_share + fym_input_t_ha * FYM_DPM,
        rpm=remaining["rpm"]
        + carbon_input_t_ha * (1.0 - dpm_share)
        + fym_input_t_ha * FYM_RPM,
        bio=remaining["bio"] + total_decomposed * to_bio,
        hum=remaining["hum"] + total_decomposed * to_hum + fym_input_t_ha * FYM_HUM,
        iom=pools.iom,
    )
    return new, tsmd, rate, co2


# ---------------------------------------------------------------- multi-month run


def run_rothc(
    initial: RothCPools,
    *,
    clay_pct: float,
    temp_c: np.ndarray,
    rain_mm: np.ndarray,
    et0_mm: np.ndarray,
    covered: np.ndarray,
    carbon_input_t_ha: np.ndarray,
    irrigation_mm: np.ndarray | float = 0.0,
    dpm_rpm_ratio: np.ndarray | float = DPM_RPM_ARABLE,
    fym_input_t_ha: np.ndarray | float = 0.0,
    depth_cm: float = 23.0,
    initial_tsmd_mm: float = 0.0,
) -> RothCResult:
    """Run RothC over a monthly series.

    Carbon balance closure is computed rather than assumed: everything that enters
    must appear as stored carbon or as CO2.
    """
    n = len(temp_c)
    arrays = {
        "rain_mm": rain_mm, "et0_mm": et0_mm, "covered": covered,
        "carbon_input_t_ha": carbon_input_t_ha,
    }
    for name, arr in arrays.items():
        if len(arr) != n:
            raise RothCError(f"{name} has length {len(arr)}, expected {n}")
    if n == 0:
        raise RothCError("empty monthly series")

    ratio = np.broadcast_to(np.asarray(dpm_rpm_ratio, dtype=float), (n,))
    irrig = np.broadcast_to(np.asarray(irrigation_mm, dtype=float), (n,))
    fym = np.broadcast_to(np.asarray(fym_input_t_ha, dtype=float), (n,))

    pools = [initial]
    soc = np.empty(n + 1)
    co2 = np.zeros(n)
    tsmd_series = np.zeros(n)
    rate_series = np.zeros(n)
    soc[0] = initial.total
    tsmd = initial_tsmd_mm
    current = initial

    for i in range(n):
        current, tsmd, rate, released = rothc_step(
            current,
            clay_pct=clay_pct,
            temp_c=float(temp_c[i]),
            rain_mm=float(rain_mm[i]),
            et0_mm=float(et0_mm[i]),
            covered=bool(covered[i]),
            carbon_input_t_ha=float(carbon_input_t_ha[i]),
            irrigation_mm=float(irrig[i]),
            dpm_rpm_ratio=float(ratio[i]),
            fym_input_t_ha=float(fym[i]),
            previous_tsmd_mm=tsmd,
            depth_cm=depth_cm,
        )
        pools.append(current)
        soc[i + 1] = current.total
        co2[i] = released
        tsmd_series[i] = tsmd
        rate_series[i] = rate

    total_in = float(np.sum(carbon_input_t_ha) + np.sum(fym))
    closure = float((soc[-1] - soc[0]) - (total_in - co2.sum()))

    return RothCResult(
        pools=tuple(pools),
        soc_t_ha=soc,
        co2_t_ha=co2,
        tsmd_mm=tsmd_series,
        rate_modifier=rate_series,
        carbon_input_t_ha=np.asarray(carbon_input_t_ha, dtype=float),
        closure_residual_t_ha=closure,
    )


# ---------------------------------------------------------------- initialisation


def equilibrium_pools(
    annual_input_t_ha: float,
    *,
    clay_pct: float,
    temp_c: np.ndarray,
    rain_mm: np.ndarray,
    et0_mm: np.ndarray,
    covered: np.ndarray,
    dpm_rpm_ratio: float = DPM_RPM_ARABLE,
    iom_t_ha: float = 0.0,
    depth_cm: float = 23.0,
    years: int = 2000,
) -> RothCPools:
    """Spin the pools to steady state under a repeating year of climate.

    Two thousand years is not excessive: the HUM pool has a 50-year turnover time,
    so a short spin-up leaves it still filling and the model then shows a spurious
    trend that looks like a management effect.
    """
    if len(temp_c) != 12:
        raise RothCError("equilibrium spin-up expects a 12-month climatology")
    monthly_input = np.full(12, annual_input_t_ha / 12.0)
    pools = RothCPools(0.0, 0.0, 0.0, 0.0, iom_t_ha)
    tsmd = 0.0
    for _ in range(years):
        for m in range(12):
            pools, tsmd, _, _ = rothc_step(
                pools, clay_pct=clay_pct, temp_c=float(temp_c[m]),
                rain_mm=float(rain_mm[m]), et0_mm=float(et0_mm[m]),
                covered=bool(covered[m]), carbon_input_t_ha=float(monthly_input[m]),
                dpm_rpm_ratio=dpm_rpm_ratio, previous_tsmd_mm=tsmd, depth_cm=depth_cm,
            )
    return pools


def initialise_from_measured_soc(
    measured_soc_t_ha: float,
    *,
    clay_pct: float,
    temp_c: np.ndarray,
    rain_mm: np.ndarray,
    et0_mm: np.ndarray,
    covered: np.ndarray,
    dpm_rpm_ratio: float = DPM_RPM_ARABLE,
    depth_cm: float = 23.0,
    tolerance_t_ha: float = 0.05,
    max_iterations: int = 60,
) -> tuple[RothCPools, float]:
    """Find the pool distribution and annual carbon input consistent with measured SOC.

    This is RothC's inverse mode, and it is the right way to start a run: a measured
    total tells you nothing about how that carbon is distributed between a pool with
    a five-week turnover and one with fifty years, and the distribution controls how
    fast the soil can respond to a change in management. Guessing it would make the
    first decade of any projection meaningless.

    Returns the equilibrium pools and the implied annual carbon input, which is
    itself informative: if it is far from what the current rotation plausibly
    returns, either the SOC measurement or the rotation history is suspect.
    """
    if measured_soc_t_ha <= 0.0:
        raise RothCError("measured SOC must be positive")
    iom = iom_from_soc(measured_soc_t_ha)
    if iom >= measured_soc_t_ha:
        raise RothCError("inert pool estimate exceeds measured SOC")

    lo, hi = 0.0, 20.0
    pools = None
    for _ in range(max_iterations):
        mid = 0.5 * (lo + hi)
        pools = equilibrium_pools(
            mid, clay_pct=clay_pct, temp_c=temp_c, rain_mm=rain_mm, et0_mm=et0_mm,
            covered=covered, dpm_rpm_ratio=dpm_rpm_ratio, iom_t_ha=iom,
            depth_cm=depth_cm, years=400,
        )
        if abs(pools.total - measured_soc_t_ha) < tolerance_t_ha:
            return pools, mid
        if pools.total < measured_soc_t_ha:
            lo = mid
        else:
            hi = mid
    raise RothCError(
        f"could not match measured SOC {measured_soc_t_ha:.1f} t C/ha; closest "
        f"equilibrium was {pools.total:.1f} with inputs up to {hi:.1f} t C/ha/yr. "
        "A measured SOC this high may need manure or a non-arable history."
    )


def soc_from_organic_carbon_pct(
    organic_carbon_pct: float, bulk_density_g_cm3: float, depth_cm: float = 23.0
) -> float:
    """Convert a measured carbon percentage to a stock, t C/ha.

    SSURGO and most soil surveys report a concentration; RothC works in stocks, and
    the conversion needs bulk density and depth. Skipping it is a common and
    invisible way to be wrong by a factor of three.
    """
    if organic_carbon_pct < 0.0:
        raise RothCError("organic carbon percent cannot be negative")
    if not 0.5 <= bulk_density_g_cm3 <= 2.0:
        raise RothCError(f"implausible bulk density {bulk_density_g_cm3}")
    return (organic_carbon_pct / 100.0) * bulk_density_g_cm3 * depth_cm * 100.0

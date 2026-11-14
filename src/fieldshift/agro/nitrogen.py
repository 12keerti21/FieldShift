"""Soil nitrogen balance, coupled to RothC's carbon flows.

Nitrogen is where rotation effects are sharpest and least visible. Three canonical
behaviours have to emerge from the arithmetic rather than be asserted:

1. **Straw immobilises nitrogen.** Cereal residue has a C:N near 90, while the
   microbial biomass built from it sits near 8.5. Decomposing that straw therefore
   needs more nitrogen than it releases, so the microbes take the difference from
   the soil and the following crop goes short. This is why incorporating wheat straw
   can depress the next crop even though it builds carbon.

2. **Legumes leave a credit.** Fixation adds nitrogen, but most of what a grain
   legume fixes leaves in the grain. The credit to the next crop comes from the
   residue and roots, which is a far smaller number than gross fixation suggests -
   and a green manure, which exports nothing, leaves a much larger one than its
   fixation rate alone would imply.

3. **Fallow mineralises into an empty field.** With no crop taking it up, nitrogen
   accumulates and then leaches on the next wet period. A summer fallow banks water
   and loses nitrogen, and a model that tracks only water would show half the story.

Nitrogen is computed from exactly the carbon flows RothC produced, not from a
parallel decomposition model, so the two budgets cannot drift apart.

Nitrogen is tracked PER POOL rather than through a single assumed residue C:N. The
first version of this module took one C:N for everything, which meant that changing
the incoming residue also silently changed the assumed composition of carbon already
in the soil - and that moved the answer by nearly 200 kg N/ha, swamping the effect
being measured. Each pool now carries its own nitrogen, so its C:N is a consequence
of what went into it rather than a parameter anyone has to choose.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rothc import CarbonFlows, RothCPools

# C:N of the pools carbon is routed INTO. Microbial biomass and humus are both
# nitrogen-rich relative to plant material, which is the whole reason straw
# immobilises. Values follow the standard RothC/SUNDIAL nitrogen coupling.
CN_BIO = 8.5
CN_HUM = 10.0

# Fraction of mineral nitrogen lost to denitrification per month when the soil is
# wet. Deliberately crude: denitrification is episodic, highly local, and the
# dominant control is anaerobic microsites a field-scale model cannot resolve.
DENITRIFICATION_FRACTION_WET = 0.02

# Share of a grain legume's fixed nitrogen exported in the harvested product. The
# rest reaches the soil in residue and roots.
DEFAULT_GRAIN_LEGUME_EXPORT = 0.70


class NitrogenError(ValueError):
    pass


@dataclass(frozen=True)
class NitrogenFluxes:
    """One month of nitrogen movement, kg N/ha."""

    gross_mineralised: float
    immobilised: float
    fixed: float
    fertilised: float
    taken_up: float
    leached: float
    denitrified: float

    @property
    def net_mineralised(self) -> float:
        """Positive when the soil releases nitrogen, negative when it locks it up."""
        return self.gross_mineralised - self.immobilised

    @property
    def total_inputs(self) -> float:
        return self.gross_mineralised + self.fixed + self.fertilised

    @property
    def total_losses(self) -> float:
        return self.immobilised + self.taken_up + self.leached + self.denitrified


@dataclass(frozen=True)
class NitrogenPools:
    """Organic nitrogen in each RothC pool, kg N/ha.

    Mirrors RothCPools one-for-one so each pool's C:N is derived rather than
    assumed. IOM is omitted: it does not decompose, so its nitrogen never cycles.
    """

    dpm: float
    rpm: float
    bio: float
    hum: float

    def __post_init__(self) -> None:
        for name in ("dpm", "rpm", "bio", "hum"):
            if getattr(self, name) < -1e-9:
                raise NitrogenError(f"negative nitrogen in the {name} pool")

    @property
    def total(self) -> float:
        return self.dpm + self.rpm + self.bio + self.hum

    def cn_ratio(self, pool: str, carbon_t_ha: float) -> float:
        """C:N of one pool, given its carbon. Returns inf for a pool with no N."""
        n = getattr(self, pool)
        if n <= 1e-12:
            return float("inf")
        return (carbon_t_ha * 1000.0) / n


def initial_nitrogen_pools(
    carbon: "RothCPools", historic_residue_cn: float = 60.0
) -> NitrogenPools:
    """Nitrogen pools consistent with a given carbon distribution.

    BIO and HUM take their characteristic ratios, which are properties of microbial
    and humified material rather than of the residue that formed them. Only the
    undecomposed plant pools need the historic residue C:N, and because they hold a
    small share of total carbon in a steady-state soil, the answer is not sensitive
    to it.
    """
    if historic_residue_cn <= 0.0:
        raise NitrogenError("historic residue C:N must be positive")
    return NitrogenPools(
        dpm=carbon.dpm * 1000.0 / historic_residue_cn,
        rpm=carbon.rpm * 1000.0 / historic_residue_cn,
        bio=carbon.bio * 1000.0 / CN_BIO,
        hum=carbon.hum * 1000.0 / CN_HUM,
    )


@dataclass(frozen=True)
class NitrogenState:
    """Mineral nitrogen plus the organic nitrogen held in each pool, kg N/ha."""

    mineral_n_kg_ha: float
    pools: NitrogenPools

    def __post_init__(self) -> None:
        if self.mineral_n_kg_ha < -1e-9:
            raise NitrogenError("mineral nitrogen cannot be negative")

    @property
    def total_n_kg_ha(self) -> float:
        return self.mineral_n_kg_ha + self.pools.total


def mineralisation_from_flows(
    flows: CarbonFlows,
    carbon_before: "RothCPools",
    nitrogen_pools: NitrogenPools,
    *,
    cn_bio: float = CN_BIO,
    cn_hum: float = CN_HUM,
) -> tuple[float, float, dict[str, float]]:
    """Gross mineralisation, immobilisation, and the nitrogen released per pool.

    Each pool releases nitrogen in proportion to the carbon it lost, at that pool's
    OWN current C:N. Nothing is assumed about composition - a pool's ratio is
    whatever its history made it.

    Returns (gross_mineralised, immobilised, released_per_pool), kg N/ha. When
    high-C:N material dominates the decomposing carbon, building new biomass and
    humus at C:N 8.5 and 10 needs more nitrogen than is released, so the difference
    is drawn from soil mineral nitrogen.
    """
    released: dict[str, float] = {}
    for pool in ("dpm", "rpm", "bio", "hum"):
        decomposed_c = flows.decomposed.get(pool, 0.0)
        cn = nitrogen_pools.cn_ratio(pool, getattr(carbon_before, pool))
        released[pool] = 0.0 if cn == float("inf") else decomposed_c * 1000.0 / cn

    gross = float(sum(released.values()))
    required = float((flows.to_bio / cn_bio + flows.to_hum / cn_hum) * 1000.0)
    return gross, required, released


def leaching(
    mineral_n_kg_ha: float,
    deep_percolation_mm: float,
    rootzone_water_mm: float,
) -> float:
    """Nitrate lost with water draining below the root zone, kg N/ha.

    A simple mixing model: the draining water carries the same nitrate concentration
    as the root-zone water it came from. Crude, but it captures the behaviour that
    matters for rotation - nitrogen accumulated under a fallow is exposed to the
    next wet period, so banking water and banking nitrogen are not the same thing.
    """
    if deep_percolation_mm < 0.0 or rootzone_water_mm < 0.0:
        raise NitrogenError("water amounts cannot be negative")
    if mineral_n_kg_ha <= 0.0:
        return 0.0
    total = deep_percolation_mm + rootzone_water_mm
    if total <= 0.0:
        return 0.0
    return float(mineral_n_kg_ha * deep_percolation_mm / total)


def fixation(
    potential_bnf_kg_n_ha: float,
    relative_transpiration: float,
    *,
    season_fraction: float = 1.0,
) -> float:
    """Biological fixation actually achieved, kg N/ha.

    Scaled by relative transpiration because fixation is an energy-expensive process
    that collapses under water stress faster than growth does. A drought-stressed
    legume fixes much less than its table value, so crediting the full figure would
    overstate the rotation benefit in exactly the dry years when it matters most.
    """
    if potential_bnf_kg_n_ha < 0.0:
        raise NitrogenError("potential fixation cannot be negative")
    scale = float(np.clip(relative_transpiration, 0.0, 1.0))
    return potential_bnf_kg_n_ha * scale * float(np.clip(season_fraction, 0.0, 1.0))


def nitrogen_step(
    state: NitrogenState,
    flows: CarbonFlows,
    carbon_before: "RothCPools",
    *,
    residue_cn: float,
    fixed_kg_n_ha: float = 0.0,
    fertiliser_kg_n_ha: float = 0.0,
    demand_kg_n_ha: float = 0.0,
    deep_percolation_mm: float = 0.0,
    rootzone_water_mm: float = 100.0,
    soil_is_wet: bool = False,
) -> tuple[NitrogenState, NitrogenFluxes]:
    """Advance the mineral nitrogen pool by one month.

    ``demand_kg_n_ha`` is what the crop would take up if nitrogen were freely
    available. Actual uptake is limited by what is there, so a nitrogen shortfall
    shows up as unmet demand rather than as a negative pool - which is what lets the
    simulator report that a rotation left its next crop short.
    """
    if residue_cn <= 0.0:
        raise NitrogenError("residue C:N must be positive")
    gross, required, released = mineralisation_from_flows(
        flows, carbon_before, state.pools
    )

    available = state.mineral_n_kg_ha + gross + fixed_kg_n_ha + fertiliser_kg_n_ha

    # Immobilisation competes with the crop for the same pool and wins: microbes are
    # in direct contact with the residue they are decomposing.
    immobilised = min(required, max(available, 0.0))
    available -= immobilised

    taken_up = min(max(demand_kg_n_ha, 0.0), max(available, 0.0))
    available -= taken_up

    leached = leaching(available, deep_percolation_mm, rootzone_water_mm)
    available -= leached

    denitrified = available * DENITRIFICATION_FRACTION_WET if soil_is_wet else 0.0
    available -= denitrified

    # Update the organic pools: each loses the nitrogen it released, the new
    # biomass and humus gain theirs, and incoming residue brings its own.
    new_pools = NitrogenPools(
        dpm=max(state.pools.dpm - released["dpm"]
                + flows.input_to_dpm * 1000.0 / residue_cn, 0.0),
        rpm=max(state.pools.rpm - released["rpm"]
                + flows.input_to_rpm * 1000.0 / residue_cn, 0.0),
        bio=max(state.pools.bio - released["bio"]
                + immobilised * (flows.to_bio / CN_BIO)
                / max(flows.to_bio / CN_BIO + flows.to_hum / CN_HUM, 1e-12), 0.0),
        hum=max(state.pools.hum - released["hum"]
                + immobilised * (flows.to_hum / CN_HUM)
                / max(flows.to_bio / CN_BIO + flows.to_hum / CN_HUM, 1e-12), 0.0),
    )

    fluxes = NitrogenFluxes(
        gross_mineralised=gross,
        immobilised=immobilised,
        fixed=fixed_kg_n_ha,
        fertilised=fertiliser_kg_n_ha,
        taken_up=taken_up,
        leached=leached,
        denitrified=denitrified,
    )
    return NitrogenState(max(available, 0.0), new_pools), fluxes


def nitrogen_stress_factor(supplied_kg_n_ha: float, demand_kg_n_ha: float) -> float:
    """Fraction of nitrogen demand met, 0 to 1.

    Intended to be combined with the water stress factor by taking the MINIMUM
    rather than the product. Liebig's law of the minimum is the better description
    here: a crop short of nitrogen is not additionally penalised for also being
    short of water, because the nitrogen shortfall already caps what it can build.
    Multiplying the two would double-count the same lost growth.
    """
    if demand_kg_n_ha <= 0.0:
        return 1.0
    return float(np.clip(supplied_kg_n_ha / demand_kg_n_ha, 0.0, 1.0))

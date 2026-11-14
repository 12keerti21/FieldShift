"""Physical models: reference ET, water balance, soil carbon, erosion.

Every model consumes the weather contract in `climate.py` and the parameter tables
in `fieldshift.data`, and nothing here knows about a NASA product, a granule format or a
variable name. That seam is what lets the science be written and tested before the
dataset layer exists.
"""

from .climate import DailyWeather, MonthlyWeather, Site, WeatherError
from .et0 import ET0Method, ET0Result, et0, et0_hargreaves, et0_penman_monteith
from .nitrogen import (
    NitrogenPools,
    NitrogenState,
    initial_nitrogen_pools,
    nitrogen_step,
    nitrogen_stress_factor,
)
from .rothc import (
    RothCPools,
    RothCResult,
    initialise_from_measured_soc,
    run_rothc,
    soc_from_organic_carbon_pct,
)
from .runoff import curve_number, scs_runoff_mm
from .rusle import (
    RusleFactors,
    WindExposure,
    soil_erodibility,
    soil_loss,
    wind_exposure_index,
)
from .synthetic import ARCHETYPES, ClimateArchetype, synthetic_weather
from .waterbalance import (
    RAINFED,
    IrrigationPolicy,
    WaterBalanceError,
    WaterBalanceResult,
    run_water_balance,
)

__all__ = [
    "ARCHETYPES",
    "ClimateArchetype",
    "DailyWeather",
    "ET0Method",
    "ET0Result",
    "IrrigationPolicy",
    "MonthlyWeather",
    "NitrogenPools",
    "NitrogenState",
    "RAINFED",
    "RothCPools",
    "RothCResult",
    "RusleFactors",
    "WindExposure",
    "Site",
    "WaterBalanceError",
    "WaterBalanceResult",
    "WeatherError",
    "curve_number",
    "et0",
    "et0_hargreaves",
    "et0_penman_monteith",
    "initialise_from_measured_soc",
    "initial_nitrogen_pools",
    "nitrogen_step",
    "nitrogen_stress_factor",
    "run_rothc",
    "run_water_balance",
    "soc_from_organic_carbon_pct",
    "scs_runoff_mm",
    "soil_erodibility",
    "soil_loss",
    "synthetic_weather",
    "wind_exposure_index",
]

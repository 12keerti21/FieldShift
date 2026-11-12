"""Physical models: reference ET, water balance, soil carbon, erosion.

Every model consumes the weather contract in `climate.py` and the parameter tables
in `rdss.data`, and nothing here knows about a NASA product, a granule format or a
variable name. That seam is what lets the science be written and tested before the
dataset layer exists.
"""

from .climate import DailyWeather, MonthlyWeather, Site, WeatherError
from .et0 import ET0Method, ET0Result, et0, et0_hargreaves, et0_penman_monteith
from .runoff import curve_number, scs_runoff_mm
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
    "RAINFED",
    "Site",
    "WaterBalanceError",
    "WaterBalanceResult",
    "WeatherError",
    "curve_number",
    "et0",
    "et0_hargreaves",
    "et0_penman_monteith",
    "run_water_balance",
    "scs_runoff_mm",
    "synthetic_weather",
]

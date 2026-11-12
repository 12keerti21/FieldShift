"""Physical models: reference ET, water balance, soil carbon, erosion.

Every model consumes the weather contract in `climate.py` and the parameter tables
in `rdss.data`, and nothing here knows about a NASA product, a granule format or a
variable name. That seam is what lets the science be written and tested before the
dataset layer exists.
"""

from .climate import DailyWeather, MonthlyWeather, Site, WeatherError
from .et0 import ET0Method, ET0Result, et0, et0_hargreaves, et0_penman_monteith

__all__ = [
    "DailyWeather",
    "ET0Method",
    "ET0Result",
    "MonthlyWeather",
    "Site",
    "WeatherError",
    "et0",
    "et0_hargreaves",
    "et0_penman_monteith",
]

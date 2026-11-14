"""Dataset adapters: the only place that knows about products and granules.

Everything here translates an external source into the weather contract or a
parameter table and records what it did. Gap filling belongs in this layer, not in
the models, so that the method is attached to the result.
"""

from .gridmet import GridmetError, parse_gridmet_csv
from .quickstats import QuickStatsError, fetch_yields, skill
from .ssurgo import SsurgoError, fetch_ssurgo_point, texture_from_ssurgo
from .terrain import TerrainError, field_slope_percent, ls_factor
from .power import (
    PowerError,
    PowerReport,
    fetch_power_daily,
    load_power_daily,
    parse_power_daily,
    power_request_url,
)

__all__ = [
    "GridmetError",
    "PowerError",
    "QuickStatsError",
    "SsurgoError",
    "TerrainError",
    "PowerReport",
    "fetch_power_daily",
    "load_power_daily",
    "parse_power_daily",
    "fetch_ssurgo_point",
    "fetch_yields",
    "field_slope_percent",
    "ls_factor",
    "parse_gridmet_csv",
    "power_request_url",
    "skill",
    "texture_from_ssurgo",
]

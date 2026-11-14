"""Dataset adapters: the only place that knows about products and granules.

Everything here translates an external source into the weather contract or a
parameter table and records what it did. Gap filling belongs in this layer, not in
the models, so that the method is attached to the result.
"""

from .power import (
    PowerError,
    PowerReport,
    fetch_power_daily,
    load_power_daily,
    parse_power_daily,
    power_request_url,
)

__all__ = [
    "PowerError",
    "PowerReport",
    "fetch_power_daily",
    "load_power_daily",
    "parse_power_daily",
    "power_request_url",
]

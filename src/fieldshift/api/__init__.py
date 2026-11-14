"""HTTP API and precomputed site bundles.

The split here is deliberate and maps onto what is actually expensive. Fetching
weather and soil and simulating every candidate rotation through forty-one years
takes about a minute, so it is precomputed. Re-ranking that scored set against a
farmer's stated priorities is milliseconds, so it is live - because someone
exploring trade-offs needs to move a slider and see the answer move.
"""

from .bundle import SITES, available_sites, build_bundle, load_bundle, write_bundles
from .main import app

__all__ = [
    "SITES",
    "app",
    "available_sites",
    "build_bundle",
    "load_bundle",
    "write_bundles",
]

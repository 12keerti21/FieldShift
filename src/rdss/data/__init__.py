"""Parameter tables: crop traits and soilborne pathogen return intervals."""

from .registry import ParameterError, Registry, load_registry
from .schema import Crop, IntervalConstraint, Pathogen, Violation

__all__ = [
    "Crop",
    "IntervalConstraint",
    "ParameterError",
    "Pathogen",
    "Registry",
    "Violation",
    "load_registry",
]

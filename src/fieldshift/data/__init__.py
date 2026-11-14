"""Parameter tables: crop traits, soilborne pathogens, soil texture classes."""

from .registry import ParameterError, Registry, load_registry
from .schema import Crop, IntervalConstraint, Pathogen, Violation
from .soils import SoilTexture, classify_texture

__all__ = [
    "Crop",
    "IntervalConstraint",
    "ParameterError",
    "Pathogen",
    "Registry",
    "SoilTexture",
    "Violation",
    "classify_texture",
    "load_registry",
]

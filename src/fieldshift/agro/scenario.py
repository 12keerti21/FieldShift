"""Weather scenarios: what if the last forty-one years had been hotter or drier?

This is a **stress test, not a climate projection**, and the difference matters
enough to put at the top of the file.

A climate projection comes from a GCM: it shifts the whole distribution, changes
year-to-year variability, moves the seasonality of rainfall, and alters the
frequency of extremes. None of that happens here. What happens here is simpler
and should be described simply:

    take the forty-one years of weather that actually occurred, add a constant
    to every temperature and multiply every rainfall by a constant, and replay
    the same sequence.

That answers a real and useful question - "would this rotation still work if
every year had been two degrees hotter?" - and it does NOT answer "what will the
climate be in 2050". Anything built on this module must say which question it is
answering, which is why `ScenarioResult` carries the disclaimer as data rather
than leaving it to a caller to remember.

What IS physically coupled, and matters:

  * Warming raises reference ET through the vapour pressure deficit, so a hotter
    scenario is also a thirstier one. That coupling is not applied by hand - ET0
    is recomputed from the perturbed weather, so it falls out of FAO-56.
  * Warming at constant specific humidity LOWERS relative humidity, which raises
    the deficit further. This is applied, because leaving relative humidity fixed
    while raising temperature would quietly understate the drying.
  * Growing degree days accumulate faster, so seasons finish sooner.

What is NOT represented, and is listed on every result:

  * variability, extremes, and the shape of the rainfall season are unchanged
  * no CO2 fertilisation effect on yield or water use efficiency
  * no shift in planting dates, which a real farmer would certainly make
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .climate import DailyWeather

# Clausius-Clapeyron: saturation vapour pressure rises about 7% per degree. Used
# to adjust relative humidity when temperature is raised at constant moisture.
CC_PER_DEGREE = 0.07

# Bounds. Outside these a "scenario" is no longer a stress test of a real record,
# it is a different climate for which this replay approach says nothing useful.
WARMING_RANGE_C = (-2.0, 6.0)
RAIN_SCALE_RANGE = (0.3, 2.0)


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Scenario:
    """A uniform perturbation of an observed weather record."""

    warming_c: float = 0.0
    rain_scale: float = 1.0
    label: str = ""

    def __post_init__(self) -> None:
        lo, hi = WARMING_RANGE_C
        if not lo <= self.warming_c <= hi:
            raise ScenarioError(
                f"warming of {self.warming_c} C is outside {lo}..{hi}. Beyond that "
                "range, replaying the observed record with an offset stops being a "
                "stress test of this climate and becomes a claim about a different "
                "one, which this method cannot support."
            )
        lo, hi = RAIN_SCALE_RANGE
        if not lo <= self.rain_scale <= hi:
            raise ScenarioError(
                f"rainfall scaling of {self.rain_scale} is outside {lo}..{hi}"
            )

    @property
    def is_baseline(self) -> bool:
        return self.warming_c == 0.0 and self.rain_scale == 1.0

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        if self.is_baseline:
            return "observed weather"
        bits = []
        if self.warming_c:
            bits.append(f"{self.warming_c:+.1f} C")
        if self.rain_scale != 1.0:
            bits.append(f"rainfall x{self.rain_scale:.2f}")
        return ", ".join(bits)

    @property
    def caveats(self) -> tuple[str, ...]:
        """What this scenario does not represent. Travels with every result."""
        return (
            "This is a stress test, not a climate projection. The observed "
            "1984-2024 weather is replayed with a constant offset; it is not "
            "output from a climate model.",
            "Year-to-year variability, the frequency of extremes and the timing "
            "of the rainy season are all unchanged. Real warming changes all "
            "three, and usually for the worse.",
            "No CO2 fertilisation effect is included, which would partly offset "
            "the yield loss and improve water use efficiency.",
            "Planting dates are held fixed. A real farmer facing this climate "
            "would shift them, so this is closer to a worst case than a forecast.",
        )


def perturb(weather: DailyWeather, scenario: Scenario) -> DailyWeather:
    """Apply a scenario to an observed weather record.

    Returns a new series; the original is never modified, because the baseline
    has to stay available for comparison and a mutated input would make every
    later comparison silently wrong.
    """
    if scenario.is_baseline:
        return weather

    tmax = weather.tmax_c + scenario.warming_c
    tmin = weather.tmin_c + scenario.warming_c

    # Rainfall is scaled, not shifted, so dry days stay dry. Adding a constant
    # would invent drizzle on days that had none, which changes the number of
    # wet days - and the number of wet days drives soil evaporation far more
    # than the total does.
    precip = weather.precip_mm * scenario.rain_scale

    # Relative humidity at constant specific humidity. Saturation vapour pressure
    # rises ~7%/K, so the same water vapour is a smaller fraction of a warmer
    # atmosphere's capacity. Holding RH fixed under warming would understate the
    # vapour pressure deficit and therefore understate the extra crop water use,
    # which is the main thing a farmer wants to know.
    factor = 1.0 / (1.0 + CC_PER_DEGREE) ** scenario.warming_c
    def _rh(values):
        if values is None:
            return None
        return np.clip(values * factor, 1.0, 100.0)

    return replace(
        weather,
        tmax_c=tmax,
        tmin_c=tmin,
        precip_mm=precip,
        rh_mean_pct=_rh(weather.rh_mean_pct),
        rh_max_pct=_rh(weather.rh_max_pct),
        rh_min_pct=_rh(weather.rh_min_pct),
        provenance={
            **dict(weather.provenance),
            "scenario": scenario.name,
            "scenario_note": (
                "observed record replayed with a uniform offset; not a climate "
                "projection"
            ),
        },
    )


# A small set of scenarios worth asking about, so an interface offers something
# meaningful rather than two raw sliders. Each is a question, not a prediction.
PRESETS: tuple[Scenario, ...] = (
    Scenario(0.0, 1.00, "Observed weather"),
    Scenario(1.5, 1.00, "1.5 C warmer"),
    Scenario(2.0, 0.90, "2 C warmer, 10% less rain"),
    Scenario(3.0, 0.80, "3 C warmer, 20% less rain"),
    Scenario(2.0, 1.15, "2 C warmer, 15% more rain"),
    Scenario(0.0, 0.70, "30% less rain, same temperature"),
)

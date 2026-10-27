from __future__ import annotations

import math
import os


def et_filename() -> str | None:
    """A prepared OpenHydroQual time series of nonnegative ET depths in m/day.

    NASA POWER's native EVPTRNS may instead be an energy flux and must not be
    wired directly into a depth-rate source without a documented conversion.
    """
    return os.environ.get("OHQ_ET_FILE", "").strip() or None


def et_lines(filename: str) -> list[str]:
    if any(char in filename for char in ",;\n\r"):
        raise ValueError("OHQ_ET_FILE contains an OHQ command delimiter")
    return [
        "create source;type=Evapotranspiration_Time_Series (Soil),"
        f"name=ET,ET_timeseries={filename}"
    ]


def deep_et_corr_fact() -> str | None:
    """Opt-in corr_fact for a separate deep-layer ET source on riparian_hru.

    riparian_urban_hru.json exposes two ET ports: `Evapotranspiration`
    (Soil_1 only) and `Evapotranspiration_2` (Soil_2 and Soil_3 together,
    each independently moisture-limited). The default writer behavior
    points both ports at the same `ET` source object, which applies the
    full reference-ET rate as three independent full-strength withdrawals
    rather than one demand apportioned across the column (see
    OHQ_Model/audit_2026-10-26/README.md for the water-balance evidence:
    ~3.90 billion m^3 total ET over 6 years on Zarrineh, ~1.39x annual
    precipitation).

    None (default, unset) preserves that historical behavior exactly -
    existing callers and saved models that don't set this variable are
    unaffected. Set OHQ_RIPARIAN_DEEP_ET_CORR_FACT to a number to opt into
    a second, separate `ET2` source feeding Evapotranspiration_2 with its
    own, independently controllable corr_fact - decoupling deep-layer
    demand from the top layer's, per the template's own documented intent
    ("Use a separate source... so that uptake from the deeper layers can
    be scaled independently of Soil_1").
    """
    val = os.environ.get("OHQ_RIPARIAN_DEEP_ET_CORR_FACT", "").strip()
    return val or None


def et2_lines(filename: str, corr_fact: str) -> list[str]:
    if any(char in filename for char in ",;\n\r"):
        raise ValueError("OHQ_ET_FILE contains an OHQ command delimiter")
    try:
        value = float(corr_fact)
    except ValueError as exc:
        raise ValueError(
            "OHQ_RIPARIAN_DEEP_ET_CORR_FACT must be a number"
        ) from exc
    # float() accepts "nan"/"inf"/"-inf" and negative numbers without
    # raising - corr_fact scales a physical evapotranspiration rate
    # (riparian_urban_hru's Evapotranspiration_2 port), so all three are
    # rejected explicitly rather than silently reaching the model file:
    # NaN/Inf would propagate into the solver as a non-physical state,
    # and a negative corr_fact would make the "rate" expression
    # (0 - corr_fact*ET_timeseries, soil_evapotranspiration_models.json)
    # add water instead of removing it.
    if math.isnan(value):
        raise ValueError("OHQ_RIPARIAN_DEEP_ET_CORR_FACT must not be NaN")
    if math.isinf(value):
        raise ValueError("OHQ_RIPARIAN_DEEP_ET_CORR_FACT must be finite, not infinite")
    if value < 0:
        raise ValueError(
            "OHQ_RIPARIAN_DEEP_ET_CORR_FACT must not be negative "
            f"(got {value!r}) - a negative corr_fact would make "
            "evapotranspiration add water instead of removing it"
        )
    # Serialize the parsed value rather than the original environment text.
    # Python accepts numeric spellings such as ``1_0`` that are not valid OHQ
    # numeric literals; str(float) gives a locale-independent, round-trippable
    # representation that the OHQ parser can consume safely.
    corr_fact_literal = str(value)
    return [
        "create source;type=Evapotranspiration_Time_Series (Soil),"
        f"name=ET2,ET_timeseries={filename},corr_fact={corr_fact_literal}"
    ]

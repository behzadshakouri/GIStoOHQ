"""USDA soil-texture classification and van Genuchten pedotransfer lookup.

Converts SSURGO-derived sand/silt/clay percentages into the van Genuchten
water-retention parameters consumed by OpenHydroQual's
``Mixed_Hydrologic_Response_Unit`` composite (K_sat, alpha_vG, n_vG,
theta_sat, theta_res).

Two independent, verified steps:

1. Classify (sand%, silt%, clay%) into one of the 12 USDA soil textural
   classes using the standard USDA-NRCS soil texture triangle boundary
   rules (silt+1.5*clay / silt+2*clay thresholds).
2. Look up that class's average van Genuchten parameters from Carsel, R.F.
   and Parrish, R.S. (1988), "Developing joint probability distributions of
   soil water retention characteristics", Water Resources Research 24(5),
   755-769. These are the values distributed as the built-in soil catalog in
   HYDRUS and the USDA ROSETTA database; cross-checked against multiple
   independent published reproductions before being hard-coded here.
"""

from __future__ import annotations

from dataclasses import dataclass


def classify_usda_texture(sand_pct: float, clay_pct: float) -> str:
    """Classify a sand/clay percentage pair into a USDA textural class.

    ``silt_pct`` is derived as ``100 - sand_pct - clay_pct`` rather than
    taken as a third independent input, so the three inputs are always
    self-consistent even if the source data does not sum exactly to 100.
    """

    sand = sand_pct
    clay = clay_pct
    silt = 100.0 - sand - clay

    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        return "loamy_sand"
    if (20 > clay >= 7 and sand > 52 and silt + 2 * clay >= 30) or (
        clay < 7 and silt < 50 and silt + 2 * clay > 30
    ):
        return "sandy_loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (50 <= silt and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt_loam"
    if 80 <= silt and clay < 12:
        return "silt"
    if 20 <= clay <= 35 and silt < 28 and sand > 45:
        return "sandy_clay_loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay_loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty_clay_loam"
    if 35 <= clay and 45 < sand:
        return "sandy_clay"
    if 40 <= clay and 40 <= silt:
        return "silty_clay"
    if 40 <= clay and sand <= 45 and silt < 40:
        return "clay"
    return "sandy_loam"


@dataclass(frozen=True)
class VanGenuchtenParams:
    theta_res: float
    theta_sat: float
    alpha_vG: float  # 1/m
    n_vG: float
    K_sat: float  # m/day


# Carsel & Parrish (1988) average van Genuchten parameters per USDA textural
# class. Source units: theta_r/theta_s [-], alpha [cm^-1], n [-], Ks [cm/day].
# Converted here to alpha [1/m] (x100) and K_sat [m/day] (/100) to match the
# units OpenHydroQual's alpha_vG/K_sat properties expect.
_CARSEL_PARRISH_CM_DAY: dict[str, tuple[float, float, float, float, float]] = {
    # class: (theta_r, theta_s, alpha [1/cm], n, Ks [cm/day])
    "sand": (0.045, 0.43, 0.145, 2.68, 712.8),
    "loamy_sand": (0.057, 0.41, 0.124, 2.28, 350.2),
    "sandy_loam": (0.065, 0.41, 0.075, 1.89, 106.1),
    "loam": (0.078, 0.43, 0.036, 1.56, 24.96),
    "silt": (0.034, 0.46, 0.016, 1.37, 6.00),
    "silt_loam": (0.067, 0.45, 0.020, 1.41, 10.8),
    "sandy_clay_loam": (0.100, 0.39, 0.059, 1.48, 31.44),
    "clay_loam": (0.095, 0.41, 0.019, 1.31, 6.24),
    "silty_clay_loam": (0.089, 0.43, 0.010, 1.23, 1.68),
    "sandy_clay": (0.100, 0.38, 0.027, 1.23, 2.88),
    "silty_clay": (0.070, 0.36, 0.005, 1.09, 0.48),
    "clay": (0.068, 0.38, 0.008, 1.09, 4.8),
}


def van_genuchten_params(sand_pct: float, clay_pct: float) -> VanGenuchtenParams:
    """Return Carsel & Parrish (1988) van Genuchten parameters for a soil.

    ``sand_pct``/``clay_pct`` are 0-100 percentages; silt is inferred.
    """

    texture = classify_usda_texture(sand_pct, clay_pct)
    theta_r, theta_s, alpha_cm, n, ks_cm_day = _CARSEL_PARRISH_CM_DAY[texture]
    return VanGenuchtenParams(
        theta_res=theta_r,
        theta_sat=theta_s,
        alpha_vG=alpha_cm * 100.0,
        n_vG=n,
        K_sat=ks_cm_day / 100.0,
    )

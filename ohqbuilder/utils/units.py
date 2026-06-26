M_TO_FT = 3.280839895
KM2_TO_M2 = 1_000_000.0
KM2_TO_ACRE = 247.105381
KM2_TO_SQMI = 0.3861021585

def safe_float(value, default=None):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def safe_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

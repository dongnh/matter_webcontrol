"""Pure, deterministic unit conversions. Zero dependencies beyond constants —
the highest-value unit-test target."""

from cli.constants import MIRED_MAX, MIRED_MIN

BRIGHTNESS_MAX = 254  # Matter LevelControl raw range is 0..254


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def normalize_brightness(raw):
    """Raw LevelControl level (0-254) -> normalized 0.0-1.0, rounded to 2 dp.

    Reflects the stored level regardless of on/off — `state` conveys on/off
    (finding C5: forcing 0.0 when off hid the stored level and disagreed with
    /api/level)."""
    if raw is None:
        return None
    return round(clamp(raw / BRIGHTNESS_MAX, 0.0, 1.0), 2)


def denormalize_brightness(value: float) -> int:
    """Normalized 0.0-1.0 -> raw 1-254. Callers handle 0.0 == off separately."""
    return max(1, int(clamp(value, 0.0, 1.0) * BRIGHTNESS_MAX))


def mireds_to_kelvin(mireds):
    """Mireds -> Kelvin, or None when unset / non-positive."""
    if not mireds or mireds <= 0:
        return None
    return int(1_000_000 / mireds)


def kelvin_to_mireds(kelvin: int) -> int:
    """Kelvin -> mireds, clamped to the Matter spec range."""
    return int(clamp(int(1_000_000 / kelvin), MIRED_MIN, MIRED_MAX))


def clamp_mireds(mireds) -> int:
    return int(clamp(int(mireds), MIRED_MIN, MIRED_MAX))


def centi_to_unit(value) -> float:
    """1/100-unit integer (Matter temperature/setpoint) -> rounded float."""
    return round(value / 100.0, 2)


def unit_to_centi(value) -> int:
    """Float unit (e.g. °C) -> 1/100-unit integer for a Matter setpoint write."""
    return int(round(float(value) * 100))

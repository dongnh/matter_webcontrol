"""Golden-value tests for cli.conversions + extract_matter_pin (now in
cli.constants, re-exported from cli.core)."""

import pytest

from cli import conversions as conv
from cli.core import extract_matter_pin


# -- extract_matter_pin -----------------------------------------------------

def test_extract_pin_golden():
    assert extract_matter_pin("2456-515-1552") == 84472403


def test_extract_pin_strips_separators():
    assert extract_matter_pin("2456-515-1552") == extract_matter_pin("24565151552")
    assert extract_matter_pin("2456 515 1552") == 84472403


@pytest.mark.parametrize("bad", ["", "123", "abcd-efg-hijk", "2456-515-155"])
def test_extract_pin_rejects_bad(bad):
    with pytest.raises(ValueError):
        extract_matter_pin(bad)


# -- brightness -------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [(0, 0.0), (200, 0.79), (254, 1.0), (300, 1.0), (1, 0.0), (127, 0.5)],
)
def test_normalize_brightness(raw, expected):
    assert conv.normalize_brightness(raw) == expected


def test_normalize_brightness_none():
    assert conv.normalize_brightness(None) is None


@pytest.mark.parametrize(
    "value,expected", [(0.0, 1), (1.0, 254), (0.5, 127), (2.0, 254), (-1.0, 1)]
)
def test_denormalize_brightness(value, expected):
    assert conv.denormalize_brightness(value) == expected


# -- color temperature ------------------------------------------------------

@pytest.mark.parametrize(
    "mireds,kelvin", [(320, 3125), (250, 4000), (153, 6535), (500, 2000)]
)
def test_mireds_to_kelvin(mireds, kelvin):
    assert conv.mireds_to_kelvin(mireds) == kelvin


@pytest.mark.parametrize("bad", [0, None, -5])
def test_mireds_to_kelvin_unset(bad):
    assert conv.mireds_to_kelvin(bad) is None


@pytest.mark.parametrize(
    "kelvin,mireds",
    [(2700, 370), (1000, 500), (10000, 153), (6500, 153), (2000, 500)],
)
def test_kelvin_to_mireds_clamped(kelvin, mireds):
    assert conv.kelvin_to_mireds(kelvin) == mireds


@pytest.mark.parametrize("raw,clamped", [(100, 153), (999, 500), (300, 300)])
def test_clamp_mireds(raw, clamped):
    assert conv.clamp_mireds(raw) == clamped


# -- centi scaling ----------------------------------------------------------

@pytest.mark.parametrize("centi,unit", [(2500, 25.0), (2612, 26.12), (0, 0.0)])
def test_centi_to_unit(centi, unit):
    assert conv.centi_to_unit(centi) == unit


@pytest.mark.parametrize("unit,centi", [(26.0, 2600), (25.5, 2550), (0.5, 50)])
def test_unit_to_centi(unit, centi):
    assert conv.unit_to_centi(unit) == centi

import pytest

from jokerps5.buttons import parse_buttons, format_mask


def test_single_button():
    assert parse_buttons("cross") == 0x4000


def test_combo():
    assert parse_buttons("cross+l1") == 0x4000 | 0x400


def test_unknown_button_raises():
    with pytest.raises(ValueError, match="unknown button"):
        parse_buttons("notabutton")


def test_empty_spec_is_zero():
    assert parse_buttons("") == 0


def test_format_mask():
    assert format_mask(0x4000) == "0x4000"

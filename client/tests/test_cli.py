from jokerps5.cli import _translate_seq_step


def test_translate_wait_step():
    assert _translate_seq_step("wait:1000", 120) == "w1000"


def test_translate_button_with_explicit_ms():
    assert _translate_seq_step("cross:300", 120) == "0x4000:300"


def test_translate_button_default_ms():
    assert _translate_seq_step("cross", 120) == "0x4000:120"


def test_translate_combo_button():
    assert _translate_seq_step("down", 120) == "0x40:120"

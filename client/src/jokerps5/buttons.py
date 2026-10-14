"""Button name to bitmask mapping, per docs/protocol.md."""

BUTTON_BITS: dict[str, int] = {
    "create": 0x1,
    "l3": 0x2,
    "r3": 0x4,
    "options": 0x8,
    "up": 0x10,
    "right": 0x20,
    "down": 0x40,
    "left": 0x80,
    "l2": 0x100,
    "r2": 0x200,
    "l1": 0x400,
    "r1": 0x800,
    "triangle": 0x1000,
    "circle": 0x2000,
    "cross": 0x4000,
    "square": 0x8000,
    "touchpad": 0x100000,
}


def parse_buttons(spec: str) -> int:
    """Parse a '+'-joined button spec like 'cross+l1' into a bitmask.

    Raises ValueError listing valid names if any name is unrecognized.
    """
    if not spec:
        return 0
    mask = 0
    for name in spec.split("+"):
        name = name.strip().lower()
        if name not in BUTTON_BITS:
            valid = ", ".join(sorted(BUTTON_BITS))
            raise ValueError(f"unknown button '{name}'; valid names: {valid}")
        mask |= BUTTON_BITS[name]
    return mask


def format_mask(mask: int) -> str:
    """Format a button mask as a hex string for the wire protocol."""
    return hex(mask)

"""Screenshot decoding and resizing helpers."""

from __future__ import annotations

import io
from dataclasses import dataclass


@dataclass(frozen=True)
class Screenshot:
    """Raw screenshot data as returned by the `shot` command."""

    data: bytes
    format: str  # "jpg" | "jxr" | "png"
    path: str

    def to_png(self, max_width: int = 1280) -> bytes:
        """Decode this screenshot and return PNG bytes, downscaled to max_width."""
        if self.format in ("jpg", "jpeg", "png"):
            image = _decode_with_pillow(self.data)
        elif self.format == "jxr":
            image = _decode_jxr(self.data)
        else:
            raise ValueError(f"unsupported screenshot format: {self.format}")

        image = _downscale(image, max_width)

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()


def _decode_with_pillow(data: bytes):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    return image.convert("RGB")


def _decode_jxr(data: bytes):
    try:
        import imagecodecs
    except ImportError as exc:
        raise RuntimeError(
            "JPEG XR decoding requires the 'jxr' extra. "
            "Install with: pip install jokerps5[jxr]"
        ) from exc

    import numpy as np
    from PIL import Image

    array = imagecodecs.jpegxr_decode(data)

    if np.issubdtype(array.dtype, np.floating):
        # HDR: simple tone map - clip to [0, 1], gamma correct, scale to 8-bit.
        array = np.clip(array, 0.0, 1.0)
        array = np.power(array, 1.0 / 2.2)
        array = (array * 255.0).astype(np.uint8)

    if array.ndim == 2:
        return Image.fromarray(array, mode="L").convert("RGB")
    if array.shape[-1] == 4:
        return Image.fromarray(array, mode="RGBA").convert("RGB")
    return Image.fromarray(array, mode="RGB")


def _downscale(image, max_width: int):
    if image.width <= max_width:
        return image
    ratio = max_width / image.width
    new_size = (max_width, max(1, round(image.height * ratio)))
    from PIL import Image

    return image.resize(new_size, Image.LANCZOS)

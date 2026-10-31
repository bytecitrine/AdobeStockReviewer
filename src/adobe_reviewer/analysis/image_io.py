"""Low-level image helpers shared by the analyzer and the processor.

Kept free of application settings so each function can be tested on its own.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps

from adobe_reviewer.exceptions import ImageAnalysisError

logger = logging.getLogger(__name__)

# Adobe Stock's maximum is 100 MP. Pillow warns above this value and refuses above
# twice this value (a protection against decompression-bomb files).
Image.MAX_IMAGE_PIXELS = 200_000_000

_EXIF_ORIENTATION = 0x0112
_TRANSPARENT_MODES = {"RGBA", "LA", "PA", "RGBa", "La"}


def open_image(path: Path) -> Image.Image:
    """Open and fully decode an image.

    Raises:
        ImageAnalysisError: If the file is missing, corrupt, unsupported or absurdly large.
    """
    try:
        with Image.open(path) as image:
            image.load()
            return image
    except Image.DecompressionBombError as exc:
        raise ImageAnalysisError(f"{Path(path).name} is too large to analyse") from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise ImageAnalysisError(f"Cannot open image {Path(path).name}: {exc}") from exc


def apply_orientation(image: Image.Image) -> Image.Image:
    """Rotate/flip the image according to its EXIF orientation tag.

    Phone and camera files are often stored sideways with a tag saying how to
    display them; the AI and the width/height numbers must use the displayed form.
    Returns the same object when no change is needed (avoids copying huge images).
    """
    try:
        orientation = image.getexif().get(_EXIF_ORIENTATION, 1)
    except (OSError, ValueError, SyntaxError):
        return image
    if orientation in (None, 0, 1):
        return image

    transposed = ImageOps.exif_transpose(image)
    icc = image.info.get("icc_profile")
    if icc and "icc_profile" not in transposed.info:
        transposed.info["icc_profile"] = icc
    return transposed


def has_transparency(image: Image.Image) -> bool:
    """True if the image has an alpha channel or palette/colour-key transparency."""
    return image.mode in _TRANSPARENT_MODES or "transparency" in image.info


def to_srgb_rgb(image: Image.Image) -> Image.Image:
    """Convert any supported image to 8-bit sRGB RGB.

    * Embedded ICC profiles (RGB/CMYK) are converted to sRGB when possible.
    * Transparency is flattened onto white.
    * 16-bit and float modes are scaled down to 8-bit.
    """
    if has_transparency(image):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background

    if image.mode in ("RGB", "CMYK"):
        image = _profile_to_srgb(image)

    if image.mode == "RGB":
        return image
    if image.mode.startswith("I;16"):
        scaled = np.asarray(image, dtype=np.float32) / 257.0
        return Image.fromarray(scaled.clip(0, 255).astype(np.uint8)).convert("RGB")
    if image.mode in ("I", "F"):
        scaled = np.asarray(image, dtype=np.float32)
        return Image.fromarray(scaled.clip(0, 255).astype(np.uint8)).convert("RGB")
    try:
        return image.convert("RGB")
    except (ValueError, OSError) as exc:
        raise ImageAnalysisError(f"Unsupported colour mode {image.mode}: {exc}") from exc


def _profile_to_srgb(image: Image.Image) -> Image.Image:
    """Apply the embedded ICC profile, converting to sRGB. Falls back to a plain convert."""
    icc = image.info.get("icc_profile")
    if icc:
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            target = ImageCms.createProfile("sRGB")
            return ImageCms.profileToProfile(image, source, target, outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError) as exc:
            logger.debug("ICC conversion skipped: %s", exc)
    return image.convert("RGB") if image.mode == "CMYK" else image


def resize_to_fit(image: Image.Image, max_side: int) -> Image.Image:
    """Shrink so the longest side is at most ``max_side`` pixels. Never enlarges."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_side:
        return image
    scale = max_side / longest
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS, reducing_gap=3.0)


def encode_jpeg_data_url(image: Image.Image, quality: int) -> tuple[str, int]:
    """Encode an RGB image as a base64 ``data:image/jpeg`` URL.

    Returns:
        ``(data_url, jpeg_size_in_bytes)``.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    raw = buffer.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii"), len(raw)

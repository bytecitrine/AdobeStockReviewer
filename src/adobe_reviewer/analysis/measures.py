"""Pure image-measurement functions (numpy only: no file or network access).

All pixel values are on the 0-255 scale.

Why patches?
    Full Adobe-size images can be 100 megapixels. Instead of processing every
    pixel, the analyzer samples a grid of small patches at the image's NATIVE
    resolution (no resizing, which would hide softness and noise). Sharpness is
    taken from the sharpest patches (so a deliberate blurred background does not
    count against a sharp subject) and noise from the flattest patches (where
    noise is easiest to see).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

# Mean of the standard JPEG (IJG) luminance quantisation table: sum 3688 over 64 entries.
_STD_LUMA_MEAN = 3688 / 64


def laplacian_variance(gray: np.ndarray) -> float:
    """Variance of the Laplacian: a classic sharpness measure (higher = sharper).

    Args:
        gray: 2-D array of grayscale pixel values.

    Returns:
        The variance, or 0.0 if the array is too small to measure.
    """
    g = gray.astype(np.float32, copy=False)
    if g.ndim != 2 or g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    laplacian = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1]
    return float(laplacian.var())


def estimate_noise_sigma(gray: np.ndarray) -> float:
    """Estimate the standard deviation of image noise (Immerkaer's method).

    The method is insensitive to smooth gradients and edges, so it works on
    ordinary photographs. Textured patches over-estimate, which is why the
    analyzer uses the *flattest* patches.

    Args:
        gray: 2-D array of grayscale pixel values.

    Returns:
        Estimated noise sigma on the 0-255 scale, or 0.0 if the array is too small.
    """
    g = gray.astype(np.float32, copy=False)
    if g.ndim != 2 or g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    response = (
        g[:-2, :-2]
        - 2.0 * g[:-2, 1:-1]
        + g[:-2, 2:]
        - 2.0 * g[1:-1, :-2]
        + 4.0 * g[1:-1, 1:-1]
        - 2.0 * g[1:-1, 2:]
        + g[2:, :-2]
        - 2.0 * g[2:, 1:-1]
        + g[2:, 2:]
    )
    return float(math.sqrt(math.pi / 2.0) * np.abs(response).mean() / 6.0)


def estimate_jpeg_quality(luma_table: Sequence[int]) -> int | None:
    """Estimate the JPEG quality setting (1-100) from the luminance quantisation table.

    Uses the standard IJG scaling rule. Works for files saved by libjpeg-based tools
    (Pillow, Photoshop "Save for Web", most AI image generators). Order-independent
    because only the table's mean is used.

    Returns:
        Estimated quality, or ``None`` if no table is given.
    """
    values = list(luma_table)
    if not values:
        return None
    scale = (sum(values) / len(values)) / _STD_LUMA_MEAN * 100.0
    if scale <= 0:
        return 100
    quality = 5000.0 / scale if scale > 100 else (200.0 - scale) / 2.0
    return int(round(min(100.0, max(1.0, quality))))


def patch_boxes(width: int, height: int, patch_size: int = 256, grid: int = 6) -> list[tuple[int, int, int, int]]:
    """Return ``(left, top, right, bottom)`` boxes for patches spread evenly over the image.

    On small images the patches overlap heavily; an image no larger than
    ``patch_size`` in either direction yields boxes as large as the smaller side.
    """
    size = min(patch_size, width, height)
    xs = sorted(set(np.linspace(0, width - size, grid).round().astype(int).tolist()))
    ys = sorted(set(np.linspace(0, height - size, grid).round().astype(int).tolist()))
    return [(x, y, x + size, y + size) for y in ys for x in xs]

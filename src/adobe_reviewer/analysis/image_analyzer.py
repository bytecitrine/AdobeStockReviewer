"""Measures objective facts about an image file on the ORIGINAL full-resolution data.

The AI is told these numbers are ground truth, so it explains and judges them
instead of guessing at sharpness or resolution from a downscaled copy.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from adobe_reviewer.analysis.image_io import apply_orientation, open_image
from adobe_reviewer.analysis.measures import (
    estimate_jpeg_quality,
    estimate_noise_sigma,
    laplacian_variance,
    patch_boxes,
)
from adobe_reviewer.exceptions import ImageAnalysisError
from adobe_reviewer.models.schemas import LocalMetrics

logger = logging.getLogger(__name__)

SHARPNESS_PERCENTILE = 90  # use the sharpest patches (a blurred background is not a defect)
NOISE_PERCENTILE = 25  # use the flattest patches (where noise is easiest to see)


class ImageAnalyzer:
    """Computes :class:`LocalMetrics` for an image file.

    Args:
        patch_size: Edge length in pixels of each sampled patch (native resolution).
        grid: Patches per axis; ``grid * grid`` patches are sampled across the image.
    """

    def __init__(self, patch_size: int = 256, grid: int = 6) -> None:
        self._patch_size = patch_size
        self._grid = grid

    def analyze(self, path: Path) -> LocalMetrics:
        """Measure ``path``.

        Raises:
            ImageAnalysisError: If the file cannot be read or decoded.
        """
        path = Path(path)
        try:
            file_size = path.stat().st_size
        except OSError as exc:
            raise ImageAnalysisError(f"Cannot read {path.name}: {exc}") from exc

        original = open_image(path)

        # Read format facts from the original before any orientation copy is made.
        image_format = original.format
        color_mode = original.mode
        has_alpha = original.mode in {"RGBA", "LA", "PA", "RGBa", "La"} or "transparency" in original.info
        quant_tables = getattr(original, "quantization", None)
        luma_table = quant_tables.get(0) if quant_tables else None
        jpeg_quality = estimate_jpeg_quality(luma_table) if image_format == "JPEG" and luma_table else None

        image = apply_orientation(original)
        width, height = image.size
        sharpness, noise = self._measure_patches(image)

        metrics = LocalMetrics(
            width=width,
            height=height,
            megapixels=round(width * height / 1_000_000, 2),
            aspect_ratio=round(width / height, 3),
            file_size_bytes=file_size,
            image_format=image_format,
            color_mode=color_mode,
            has_alpha=has_alpha,
            sharpness_score=sharpness,
            noise_level=noise,
            jpeg_quality_estimate=jpeg_quality,
        )
        logger.debug("Analysed %s: %s", path.name, metrics.model_dump())
        return metrics

    def _measure_patches(self, image: Image.Image) -> tuple[float | None, float | None]:
        """Return ``(sharpness, noise)`` from native-resolution patches, or ``(None, None)`` if too small."""
        width, height = image.size
        if min(width, height) < 16:
            return None, None

        sharpness_values: list[float] = []
        noise_values: list[float] = []
        for box in patch_boxes(width, height, self._patch_size, self._grid):
            gray = np.asarray(image.crop(box).convert("L"), dtype=np.float32)
            sharpness_values.append(laplacian_variance(gray))
            noise_values.append(estimate_noise_sigma(gray))

        sharpness = float(np.percentile(sharpness_values, SHARPNESS_PERCENTILE))
        noise = float(np.percentile(noise_values, NOISE_PERCENTILE))
        return round(sharpness, 1), round(noise, 2)

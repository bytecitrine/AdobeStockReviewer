"""Prepares the copy of an image that is sent to the AI model."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from adobe_reviewer.analysis.image_io import (
    apply_orientation,
    encode_jpeg_data_url,
    open_image,
    resize_to_fit,
    to_srgb_rgb,
)
from adobe_reviewer.config.manager import ConfigManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedImage:
    """The API-ready copy of an image, plus facts about how it was made."""

    data_url: str
    original_size: tuple[int, int]  # (width, height) as displayed (EXIF orientation applied)
    sent_size: tuple[int, int]
    encoded_bytes: int

    @property
    def was_resized(self) -> bool:
        """True if the copy sent to the AI is smaller than the original."""
        return self.sent_size != self.original_size


class ImageProcessor:
    """Turns an image file into a base64 JPEG data URL for the vision model.

    Steps: apply EXIF orientation, convert to sRGB (flattening transparency onto
    white), shrink so the longest side is at most ``image.max_side_px`` (never
    enlarge), then encode as JPEG. The original file is never modified.

    Args:
        config: The application's :class:`ConfigManager` (settings are read live).
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def prepare(self, path: Path) -> PreparedImage:
        """Create the API copy of ``path``.

        Raises:
            ImageAnalysisError: If the file cannot be read or converted.
        """
        settings = self._config.settings.image
        oriented = apply_orientation(open_image(Path(path)))
        original_size = oriented.size

        rgb = to_srgb_rgb(oriented)
        resized = resize_to_fit(rgb, settings.max_side_px)
        data_url, encoded_bytes = encode_jpeg_data_url(resized, settings.upload_jpeg_quality)

        prepared = PreparedImage(
            data_url=data_url,
            original_size=original_size,
            sent_size=resized.size,
            encoded_bytes=encoded_bytes,
        )
        logger.debug(
            "Prepared %s: %s -> %s, %.0f KB",
            Path(path).name,
            prepared.original_size,
            prepared.sent_size,
            encoded_bytes / 1024,
        )
        return prepared

"""Tests for ImageProcessor."""

import base64
import io

from PIL import Image

from adobe_reviewer.analysis.image_processor import ImageProcessor
from adobe_reviewer.config.manager import ConfigManager


def processor(project) -> ImageProcessor:
    return ImageProcessor(ConfigManager(project / "config" / "settings.json"))


def decode(prepared) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(prepared.data_url.split(",", 1)[1])))


def test_large_image_is_downscaled_to_max_side(project, make_image):
    prepared = processor(project).prepare(make_image("big.jpg", (3000, 2000)))
    assert prepared.original_size == (3000, 2000) and prepared.sent_size == (2048, 1365)
    assert prepared.was_resized and decode(prepared).size == prepared.sent_size


def test_small_image_is_not_enlarged(project, make_image):
    prepared = processor(project).prepare(make_image("small.jpg", (800, 600)))
    assert prepared.sent_size == (800, 600) and not prepared.was_resized


def test_original_size_reflects_orientation(project, make_image):
    prepared = processor(project).prepare(make_image("side.jpg", (300, 200), orientation=6))
    assert prepared.original_size == (200, 300)


def test_png_is_sent_as_rgb_jpeg(project, make_image):
    prepared = processor(project).prepare(make_image("a.png", (400, 300)))
    assert prepared.data_url.startswith("data:image/jpeg;base64,") and decode(prepared).mode == "RGB"

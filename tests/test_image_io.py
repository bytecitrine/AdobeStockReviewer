"""Tests for image file helpers."""

from pathlib import Path

import pytest
from PIL import Image

from adobe_reviewer.analysis.image_io import (
    apply_orientation,
    encode_jpeg_data_url,
    open_image,
    resize_to_fit,
    to_srgb_rgb,
)
from adobe_reviewer.exceptions import ImageAnalysisError


def test_corrupt_and_missing_files_raise(tmp_path: Path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(ImageAnalysisError):
        open_image(bad)
    with pytest.raises(ImageAnalysisError):
        open_image(tmp_path / "missing.jpg")


def test_exif_orientation_is_applied(make_image):
    path = make_image("sideways.jpg", (300, 200), orientation=6)
    assert open_image(path).size == (300, 200)
    assert apply_orientation(open_image(path)).size == (200, 300)


def test_no_orientation_returns_same_object(make_image):
    image = open_image(make_image("plain.jpg", (50, 40)))
    assert apply_orientation(image) is image


def test_transparency_is_flattened_onto_white(tmp_path: Path):
    path = tmp_path / "t.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(path)
    flattened = to_srgb_rgb(open_image(path))
    assert flattened.mode == "RGB" and flattened.getpixel((5, 5)) == (255, 255, 255)


def test_resize_never_enlarges_and_keeps_aspect():
    assert resize_to_fit(Image.new("RGB", (500, 300)), 2048).size == (500, 300)
    assert resize_to_fit(Image.new("RGB", (3000, 2000)), 2048).size == (2048, 1365)


def test_data_url_decodes_back_to_image():
    import base64
    import io

    url, size = encode_jpeg_data_url(Image.new("RGB", (64, 48), (10, 120, 200)), 90)
    assert url.startswith("data:image/jpeg;base64,") and size > 0
    decoded = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert decoded.size == (64, 48)

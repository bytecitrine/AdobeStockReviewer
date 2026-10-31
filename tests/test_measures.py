"""Tests for the pure measurement functions."""

import io

import numpy as np
import pytest
from PIL import Image, ImageFilter

from adobe_reviewer.analysis.measures import (
    estimate_jpeg_quality,
    estimate_noise_sigma,
    laplacian_variance,
    patch_boxes,
)


def test_noise_estimate_matches_true_sigma():
    rng = np.random.default_rng(0)
    patch = 128 + rng.normal(0, 5, (256, 256))
    assert estimate_noise_sigma(patch) == pytest.approx(5, rel=0.1)


def test_noise_estimate_ignores_smooth_gradient():
    gradient = np.tile(np.linspace(50, 200, 256), (256, 1))
    assert estimate_noise_sigma(gradient) < 0.1


def test_sharp_texture_scores_far_above_blurred_copy():
    rng = np.random.default_rng(1)
    sharp = Image.fromarray(rng.integers(0, 255, (256, 256)).astype(np.uint8))
    blurred = sharp.filter(ImageFilter.GaussianBlur(3))
    assert laplacian_variance(np.asarray(sharp)) > 100 * laplacian_variance(np.asarray(blurred))


def test_tiny_arrays_return_zero():
    assert laplacian_variance(np.zeros((2, 2))) == 0.0
    assert estimate_noise_sigma(np.zeros((2, 5))) == 0.0


@pytest.mark.parametrize("quality", [30, 50, 75, 85, 90, 95])
def test_jpeg_quality_round_trip(quality: int):
    rng = np.random.default_rng(2)
    pixels = (np.tile(np.linspace(30, 220, 300), (200, 1)) + rng.normal(0, 8, (200, 300))).clip(0, 255)
    buffer = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8)).convert("RGB").save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    with Image.open(buffer) as jpeg:
        estimate = estimate_jpeg_quality(jpeg.quantization[0])
    assert estimate == pytest.approx(quality, abs=2)


def test_jpeg_quality_none_without_table():
    assert estimate_jpeg_quality([]) is None


def test_patch_boxes_cover_large_image_and_stay_inside():
    boxes = patch_boxes(6000, 4000)
    assert len(boxes) == 36
    assert all(0 <= l and r <= 6000 and 0 <= t and b <= 4000 for l, t, r, b in boxes)


def test_patch_boxes_small_image_uses_smaller_side():
    boxes = patch_boxes(100, 80)
    assert all(r - l == 80 and b - t == 80 for l, t, r, b in boxes)

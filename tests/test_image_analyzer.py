"""Tests for ImageAnalyzer."""

from pathlib import Path

import pytest

from adobe_reviewer.analysis.image_analyzer import ImageAnalyzer
from adobe_reviewer.exceptions import ImageAnalysisError


def test_basic_facts(make_image):
    path = make_image("a.jpg", (2000, 2000), quality=90)
    metrics = ImageAnalyzer().analyze(path)
    assert (metrics.width, metrics.height) == (2000, 2000)
    assert metrics.megapixels == 4.0 and metrics.aspect_ratio == 1.0
    assert metrics.image_format == "JPEG" and metrics.color_mode == "RGB"
    assert metrics.jpeg_quality_estimate == pytest.approx(90, abs=2)
    assert metrics.file_size_bytes == path.stat().st_size


def test_png_has_no_jpeg_quality(make_image):
    metrics = ImageAnalyzer().analyze(make_image("a.png", (600, 400)))
    assert metrics.image_format == "PNG" and metrics.jpeg_quality_estimate is None


def test_dimensions_follow_exif_orientation(make_image):
    metrics = ImageAnalyzer().analyze(make_image("s.jpg", (300, 200), orientation=6))
    assert (metrics.width, metrics.height) == (200, 300)


def test_noisy_image_scores_higher_noise_than_clean(make_image):
    clean = ImageAnalyzer().analyze(make_image("clean.png", (800, 600)))
    noisy = ImageAnalyzer().analyze(make_image("noisy.png", (800, 600), noise=6.0))
    assert clean.noise_level < 1.0
    assert noisy.noise_level == pytest.approx(6.0, abs=1.5)


def test_textured_image_is_sharper_than_smooth_one(make_image):
    smooth = ImageAnalyzer().analyze(make_image("smooth.png", (800, 600)))
    detailed = ImageAnalyzer().analyze(make_image("detail.png", (800, 600), texture=60))
    assert detailed.sharpness_score > 50 * max(smooth.sharpness_score, 0.1)


def test_tiny_image_skips_sharpness_and_noise(make_image):
    metrics = ImageAnalyzer().analyze(make_image("tiny.png", (10, 10)))
    assert metrics.sharpness_score is None and metrics.noise_level is None


def test_unreadable_file_raises(tmp_path: Path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"garbage")
    with pytest.raises(ImageAnalysisError):
        ImageAnalyzer().analyze(bad)

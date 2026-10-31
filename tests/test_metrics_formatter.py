"""Tests for format_metrics_block."""

from adobe_reviewer.analysis.image_processor import PreparedImage
from adobe_reviewer.analysis.metrics_formatter import format_metrics_block
from adobe_reviewer.models.schemas import LocalMetrics

METRICS = LocalMetrics(
    width=4000, height=3000, megapixels=12.0, aspect_ratio=1.333, file_size_bytes=3_145_728,
    image_format="JPEG", color_mode="RGB", sharpness_score=412.3, noise_level=1.6, jpeg_quality_estimate=90,
)


def test_block_contains_the_key_facts():
    block = format_metrics_block(METRICS)
    for expected in ("4000 x 3000", "12.00 megapixels", "JPEG", "3.00 MB", "quality: 90", "412.3", "1.6"):
        assert expected in block
    assert "downscaled" not in block


def test_downscale_note_only_when_resized():
    resized = PreparedImage("data:", (4000, 3000), (2048, 1536), 1000)
    same = PreparedImage("data:", (800, 600), (800, 600), 1000)
    assert "downscaled to 2048 x 1536" in format_metrics_block(METRICS, resized)
    assert "downscaled" not in format_metrics_block(METRICS, same)


def test_missing_measurements_are_omitted():
    block = format_metrics_block(METRICS.model_copy(update={"sharpness_score": None, "noise_level": None}))
    assert "Sharpness" not in block and "Noise" not in block

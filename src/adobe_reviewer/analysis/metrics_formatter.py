"""Formats :class:`LocalMetrics` as the text block inserted into the AI prompt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from adobe_reviewer.models.schemas import LocalMetrics

if TYPE_CHECKING:  # avoids importing the config layer just for a type hint
    from adobe_reviewer.analysis.image_processor import PreparedImage


def format_metrics_block(metrics: LocalMetrics, prepared: "PreparedImage | None" = None) -> str:
    """Return a bullet list of measurements for the ``${metrics_block}`` prompt placeholder.

    Sharpness and noise come with a rough interpretation guide so the model does not
    have to guess what the numbers mean. The guide is deliberately labelled as rough:
    the values depend on the picture's content (a smooth subject scores lower).
    """
    lines = [
        f"- Original resolution: {metrics.width} x {metrics.height} px ({metrics.megapixels:.2f} megapixels)",
        f"- Aspect ratio: {metrics.aspect_ratio:.3f}",
        f"- File: {metrics.image_format or 'unknown format'}, colour mode {metrics.color_mode or 'unknown'}, "
        f"{metrics.file_size_bytes / 1_048_576:.2f} MB",
    ]
    if metrics.jpeg_quality_estimate is not None:
        lines.append(f"- Estimated JPEG quality: {metrics.jpeg_quality_estimate} (of 100)")

    if metrics.sharpness_score is not None:
        lines.append(
            f"- Sharpness (Laplacian variance of the sharpest regions at 100% zoom): {metrics.sharpness_score:g}. "
            "Rough guide: below 30 very soft, 30-100 moderate, above 100 sharp. Content-dependent."
        )
    if metrics.noise_level is not None:
        lines.append(
            f"- Noise (estimated in the flattest regions, 0-255 scale): {metrics.noise_level:g}. "
            "Rough guide: below 2 clean, 2-4 slight, above 4 visible noise."
        )

    if prepared is not None and prepared.was_resized:
        lines.append(
            f"- The image shown to you was downscaled to {prepared.sent_size[0]} x {prepared.sent_size[1]} px "
            "for transmission. Fine detail is therefore less visible to you than in the original."
        )
    return "\n".join(lines)

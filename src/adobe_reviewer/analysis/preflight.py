"""Rule-based checks that need no AI.

If an image has a hard-fail problem here (for example it is below Adobe Stock's
4-megapixel minimum), the review engine can reject it WITHOUT paying for an API
call. Non-fatal findings are merged into the final report so they are never lost.
"""

from __future__ import annotations

import math

from adobe_reviewer.config.manager import ConfigManager
from adobe_reviewer.models.schemas import Issue, IssueCategory, LocalMetrics, Severity

LOW_JPEG_QUALITY_MAJOR = 70  # below this, compression damage is likely visible
LOW_JPEG_QUALITY_MINOR = 80


class PreflightChecker:
    """Turns :class:`LocalMetrics` into objective :class:`Issue` objects.

    Args:
        config: The application's :class:`ConfigManager` (limits are read live).
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def check(self, metrics: LocalMetrics) -> list[Issue]:
        """Return every objective problem found, most severe first."""
        issues: list[Issue] = []
        issues.extend(self._check_resolution(metrics))
        issues.extend(self._check_format(metrics))
        issues.extend(self._check_compression(metrics))
        return sorted(issues, key=lambda issue: issue.severity.rank)

    @staticmethod
    def has_hard_fail(issues: list[Issue]) -> bool:
        """True if any issue alone means automatic rejection."""
        return any(issue.hard_fail for issue in issues)

    # ------------------------------------------------------------------ #
    def _check_resolution(self, metrics: LocalMetrics) -> list[Issue]:
        limits = self._config.settings.image
        megapixels = metrics.megapixels

        if megapixels < limits.min_megapixels:
            factor = math.sqrt(limits.min_megapixels / max(megapixels, 0.01))
            target_w = math.ceil(metrics.width * factor)
            target_h = math.ceil(metrics.height * factor)
            enforce = limits.reject_below_min_resolution
            return [
                Issue(
                    category=IssueCategory.RESOLUTION,
                    severity=Severity.CRITICAL if enforce else Severity.MAJOR,
                    title=f"Resolution too low: {megapixels:.2f} MP (minimum {limits.min_megapixels:g} MP)",
                    explanation=(
                        f"The image is {metrics.width} x {metrics.height} px ({megapixels:.2f} MP). "
                        f"Adobe Stock requires at least {limits.min_megapixels:g} megapixels."
                    ),
                    correction=(
                        f"Upscale to at least {target_w} x {target_h} px (same aspect ratio) with a "
                        "detail-preserving AI upscaler, then check sharpness and artifacts again."
                    ),
                    hard_fail=enforce,
                )
            ]

        if megapixels > limits.max_megapixels:
            factor = math.sqrt(limits.max_megapixels / megapixels)
            return [
                Issue(
                    category=IssueCategory.RESOLUTION,
                    severity=Severity.CRITICAL,
                    title=f"Resolution too high: {megapixels:.1f} MP (maximum {limits.max_megapixels:g} MP)",
                    explanation=(
                        f"The image is {metrics.width} x {metrics.height} px ({megapixels:.1f} MP). "
                        f"Adobe Stock accepts at most {limits.max_megapixels:g} megapixels."
                    ),
                    correction=(
                        f"Downscale to at most {math.floor(metrics.width * factor)} x "
                        f"{math.floor(metrics.height * factor)} px (same aspect ratio)."
                    ),
                    hard_fail=True,
                )
            ]
        return []

    @staticmethod
    def _check_format(metrics: LocalMetrics) -> list[Issue]:
        issues: list[Issue] = []
        if metrics.image_format and metrics.image_format != "JPEG":
            issues.append(
                Issue(
                    category=IssueCategory.TECHNICAL,
                    severity=Severity.INFO,
                    title=f"File is {metrics.image_format}, not JPEG",
                    explanation="Adobe Stock contributors normally upload images as JPEG files.",
                    correction="Export a high-quality (quality 90+) sRGB JPEG before uploading.",
                )
            )
        if metrics.color_mode == "CMYK":
            issues.append(
                Issue(
                    category=IssueCategory.COLOR,
                    severity=Severity.MINOR,
                    title="Image uses the CMYK colour mode",
                    explanation="CMYK files can show shifted colours on screen and are not suitable for stock upload.",
                    correction="Convert the image to sRGB (RGB colour mode) and re-check the colours.",
                )
            )
        if metrics.has_alpha:
            issues.append(
                Issue(
                    category=IssueCategory.TECHNICAL,
                    severity=Severity.MINOR,
                    title="Image contains transparency",
                    explanation="Transparent areas are flattened when exporting to JPEG and may turn black or white.",
                    correction="Flatten onto a deliberately chosen background and check the edges after export.",
                )
            )
        return issues

    @staticmethod
    def _check_compression(metrics: LocalMetrics) -> list[Issue]:
        quality = metrics.jpeg_quality_estimate
        if quality is None or quality >= LOW_JPEG_QUALITY_MINOR:
            return []
        major = quality < LOW_JPEG_QUALITY_MAJOR
        return [
            Issue(
                category=IssueCategory.COMPRESSION,
                severity=Severity.MAJOR if major else Severity.MINOR,
                title=f"Heavily compressed JPEG (estimated quality {quality})",
                explanation=(
                    "Low JPEG quality causes blocking, ringing and banding that Adobe Stock "
                    "reviewers reject as a technical defect."
                ),
                correction="Re-export from the original (uncompressed) source at JPEG quality 90 or higher.",
            )
        ]

"""Shared pytest fixtures."""

import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A throw-away project root with a copy of the real settings.json and prompts."""
    (tmp_path / "config").mkdir()
    shutil.copy(PROJECT_ROOT / "config" / "settings.json", tmp_path / "config" / "settings.json")
    shutil.copytree(PROJECT_ROOT / "prompts", tmp_path / "prompts")
    return tmp_path


@pytest.fixture()
def make_image(tmp_path: Path) -> Callable[..., Path]:
    """Factory that writes a synthetic test image and returns its path.

    The picture is a smooth diagonal gradient (so noise measurements are clean),
    optionally with a fine-detail texture and added Gaussian noise.
    """

    def _make(
        name: str = "test.jpg",
        size: tuple[int, int] = (800, 600),
        *,
        noise: float = 0.0,
        texture: float = 0.0,
        quality: int = 90,
        mode: str = "RGB",
        orientation: int | None = None,
    ) -> Path:
        width, height = size
        rng = np.random.default_rng(42)
        x = np.linspace(0, 1, width)[None, :]
        y = np.linspace(0, 1, height)[:, None]
        base = 70 + 110 * (0.5 * x + 0.5 * y)
        if texture:
            base = base + texture * rng.uniform(-1, 1, (height, width))
        if noise:
            base = base + rng.normal(0, noise, (height, width))
        pixels = np.repeat(base.clip(0, 255).astype(np.uint8)[:, :, None], 3, axis=2)
        image = Image.fromarray(pixels, "RGB")
        if mode != "RGB":
            image = image.convert(mode)

        path = tmp_path / name
        save_args: dict = {}
        if path.suffix.lower() in (".jpg", ".jpeg"):
            save_args["quality"] = quality
        if orientation is not None:
            exif = Image.Exif()
            exif[0x0112] = orientation
            save_args["exif"] = exif
        image.save(path, **save_args)
        return path

    return _make

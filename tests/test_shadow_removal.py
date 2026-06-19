"""Synthetic tests for post-warp shadow removal pipeline."""

from __future__ import annotations

import numpy as np

from scanner.config import ScannerConfig
from scanner.warp import (
    enhance_for_scan,
    process_rectified_image,
    remove_document_shadows,
)


def _synthetic_shadowed_page(width: int = 900, height: int = 1270) -> np.ndarray:
    """White page with dark text and a corner shadow gradient."""
    page = np.full((height, width, 3), 235, dtype=np.uint8)

    for y in range(120, height - 120, 48):
        page[y:y + 8, 80:width - 80] = 40

    shadow = np.linspace(1.0, 0.45, width, dtype=np.float32)
    shadow_grid = np.tile(shadow, (height, 1))
    page = np.clip(page.astype(np.float32) * shadow_grid[..., None], 0, 255).astype(np.uint8)
    return page


def test_remove_document_shadows_flattens_corner_shadow() -> None:
    cfg = ScannerConfig()
    warped = _synthetic_shadowed_page()
    corrected = remove_document_shadows(warped, cfg)

    left_before = float(np.mean(warped[:, :100]))
    right_before = float(np.mean(warped[:, -100:]))
    left_after = float(np.mean(corrected[:, :100]))
    right_after = float(np.mean(corrected[:, -100:]))

    gap_before = abs(left_before - right_before)
    gap_after = abs(left_after - right_after)

    assert corrected.shape == warped.shape
    assert gap_after < gap_before


def test_process_rectified_image_runs_shadow_before_enhance() -> None:
    cfg = ScannerConfig(apply_shadow_removal=True, apply_scan_enhancement=True)
    warped = _synthetic_shadowed_page()
    result = process_rectified_image(warped, cfg)

    assert result.shape == warped.shape
    assert result.dtype == warped.dtype
    assert not np.array_equal(result, warped)
    assert not np.array_equal(result, enhance_for_scan(warped, cfg))


def test_process_rectified_image_can_disable_shadow_removal() -> None:
    cfg = ScannerConfig(apply_shadow_removal=False, apply_scan_enhancement=True)
    warped = _synthetic_shadowed_page()
    shadow_off = process_rectified_image(warped, cfg)
    shadow_on = process_rectified_image(
        warped,
        ScannerConfig(apply_shadow_removal=True, apply_scan_enhancement=True),
    )

    assert not np.array_equal(shadow_off, shadow_on)

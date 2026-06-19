from __future__ import annotations

import cv2
import numpy as np

from .config import ScannerConfig
from .geometry import edge_lengths


def a4_target_size(short_side: int, a4_ratio: float) -> tuple[int, int]:
    width = int(short_side)
    height = int(short_side * a4_ratio)
    return width, height


def compute_warp_short_side(frame_width: int, frame_height: int, cfg: ScannerConfig) -> int:
    if (
        cfg.scale_warp_to_capture
        and frame_width > 0
        and frame_height > 0
    ):
        s = int(min(frame_width, frame_height) * cfg.warp_capture_scale)
    else:
        s = int(cfg.warp_short_side)
    return max(cfg.warp_short_side_min, min(s, cfg.warp_short_side_max))


def _interpolation_from_config(cfg: ScannerConfig) -> int:
    name = (cfg.warp_interpolation or "cubic").strip().lower()
    mapping = {
        "linear": cv2.INTER_LINEAR,
        "cubic": cv2.INTER_CUBIC,
        "lanczos4": cv2.INTER_LANCZOS4,
        "lanczos": cv2.INTER_LANCZOS4,
    }
    return mapping.get(name, cv2.INTER_CUBIC)


def _source_quad_for_portrait_output(quad: np.ndarray, cfg: ScannerConfig | None) -> np.ndarray:
    q = quad.astype(np.float32)
    if cfg is None or not getattr(cfg, "auto_rotate_landscape_to_portrait", False):
        return q

    top, right, bottom, left = edge_lengths(q)
    horizontal = (top + bottom) * 0.5
    vertical = (right + left) * 0.5

    # Already portrait-like in the source view: keep original orientation.
    if horizontal <= vertical * 1.02:
        return q

    direction = (getattr(cfg, "landscape_rotation_direction", "ccw") or "ccw").strip().lower()
    if direction in {"cw", "clockwise", "right"}:
        # Make left edge become the top edge in output.
        return np.roll(q, 1, axis=0).astype(np.float32)  # [bl, tl, tr, br]
    # Default: make right edge become the top edge in output.
    return np.roll(q, -1, axis=0).astype(np.float32)  # [tr, br, bl, tl]


def warp_document(
    frame: np.ndarray,
    quad: np.ndarray,
    dst_size: tuple[int, int],
    cfg: ScannerConfig | None = None,
) -> np.ndarray:
    width, height = dst_size
    src_quad = _source_quad_for_portrait_output(quad, cfg)
    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src_quad, dst)
    interp = _interpolation_from_config(cfg) if cfg is not None else cv2.INTER_LINEAR
    warped = cv2.warpPerspective(
        frame,
        matrix,
        (width, height),
        flags=interp,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return warped


def _shadow_kernel_size(image: np.ndarray, cfg: ScannerConfig) -> int:
    height, width = image.shape[:2]
    short_side = min(height, width)
    divisor = max(1, int(getattr(cfg, "shadow_removal_kernel_divisor", 30)))
    kernel_min = max(3, int(getattr(cfg, "shadow_removal_kernel_min", 15)))
    kernel_max = max(kernel_min, int(getattr(cfg, "shadow_removal_kernel_max", 80)))
    kernel = short_side // divisor
    return max(kernel_min, min(kernel, kernel_max))


def remove_document_shadows(warped: np.ndarray, cfg: ScannerConfig | None = None) -> np.ndarray:
    """Flatten uneven illumination on a rectified page using morphological background estimation."""
    if warped.size == 0:
        return warped

    kernel_size = _shadow_kernel_size(warped, cfg) if cfg is not None else 25
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))

    lab = cv2.cvtColor(warped, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)

    background = cv2.morphologyEx(l_chan, cv2.MORPH_CLOSE, kernel)
    background = cv2.GaussianBlur(background, (0, 0), sigmaX=3, sigmaY=3)
    background = np.maximum(background, 1)

    l_norm = cv2.divide(l_chan, background, scale=255)
    return cv2.cvtColor(cv2.merge((l_norm, a_chan, b_chan)), cv2.COLOR_LAB2BGR)


def process_rectified_image(warped: np.ndarray, cfg: ScannerConfig) -> np.ndarray:
    processed = warped
    if cfg.apply_shadow_removal:
        processed = remove_document_shadows(processed, cfg)
    if cfg.apply_scan_enhancement:
        processed = enhance_for_scan(processed, cfg)
    return processed


def _gamma_lut(gamma: float) -> np.ndarray:
    gamma = max(0.1, float(gamma))
    inv_gamma = 1.0 / gamma
    values = np.arange(256, dtype=np.float32) / 255.0
    table = np.power(values, inv_gamma) * 255.0
    return np.clip(table, 0.0, 255.0).astype(np.uint8)


def enhance_for_scan(warped: np.ndarray, cfg: ScannerConfig | None = None) -> np.ndarray:
    if warped.size == 0:
        return warped

    shadow_on = bool(cfg is not None and getattr(cfg, "apply_shadow_removal", False))
    gamma = max(0.1, float(getattr(cfg, "enhance_gamma", 1.05 if shadow_on else 1.08)))
    clahe_clip = max(0.1, float(getattr(cfg, "enhance_clahe_clip_limit", 1.5 if shadow_on else 2.0)))
    clahe_tile = max(2, int(getattr(cfg, "enhance_clahe_tile_size", 8)))
    saturation_boost = max(0.0, float(getattr(cfg, "enhance_saturation_boost", 1.0 if shadow_on else 1.10)))
    sharpen_strength = max(0.0, float(getattr(cfg, "enhance_sharpen_strength", 0.30 if shadow_on else 0.55)))

    gamma_corrected = cv2.LUT(warped, _gamma_lut(gamma))

    lab = cv2.cvtColor(gamma_corrected, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_tile, clahe_tile))
    l_chan = clahe.apply(l_chan)
    contrast_boosted = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)

    hsv = cv2.cvtColor(contrast_boosted, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation_boost, 0.0, 255.0)
    saturated = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    if sharpen_strength <= 1e-6:
        return saturated
    blurred = cv2.GaussianBlur(saturated, (0, 0), sigmaX=1.1, sigmaY=1.1)
    return cv2.addWeighted(saturated, 1.0 + sharpen_strength, blurred, -sharpen_strength, 0)


from __future__ import annotations

import cv2
import numpy as np

from .config import ScannerConfig
from .geometry import contour_to_quad, edge_lengths, order_points
from .preprocess import make_binary_mask, make_edge_mask


def _ratio_score(value: float, low: float, high: float) -> float:
    if low <= value <= high:
        return 1.0
    distance = min(abs(value - low), abs(value - high))
    return max(0.0, 1.0 - distance * 3.0)


def _aspect_score(quad: np.ndarray, target_a4_ratio: float) -> float:
    top, right, bottom, left = edge_lengths(quad)
    width = max((top + bottom) * 0.5, 1.0)
    height = max((right + left) * 0.5, 1.0)
    ratio = max(width, height) / min(width, height)
    error = abs(ratio - target_a4_ratio)
    return max(0.0, 1.0 - error / 0.45)


def _contour_to_quad_relaxed(contour: np.ndarray) -> np.ndarray | None:
    quad = contour_to_quad(contour)
    if quad is not None:
        return quad

    hull = cv2.convexHull(contour)
    if hull is None or len(hull) < 4:
        return None

    peri = cv2.arcLength(hull, True)
    if peri <= 0:
        return None

    for eps_scale in (0.015, 0.025, 0.04, 0.06):
        approx = cv2.approxPolyDP(hull, eps_scale * peri, True)
        if len(approx) == 4:
            return order_points(approx.reshape(4, 2).astype(np.float32))

    # Fallback: minimum-area rectangle can recover page bounds when contour is noisy.
    rect = cv2.minAreaRect(hull)
    w, h = rect[1]
    if min(w, h) <= 1.0:
        return None
    box = cv2.boxPoints(rect).astype(np.float32)
    return order_points(box)


def detect_document_quad(frame: np.ndarray, cfg: ScannerConfig) -> tuple[np.ndarray | None, float, dict]:
    edges = make_edge_mask(frame, cfg)
    binary = make_binary_mask(frame, cfg)
    merged = cv2.bitwise_or(edges, binary)
    contours_ext_merged, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours_ext_binary, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours_all, _ = cv2.findContours(merged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = contours_ext_merged + contours_ext_binary + contours_all

    frame_area = float(frame.shape[0] * frame.shape[1])
    best_quad = None
    best_conf = 0.0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area <= 0:
            continue
        area_ratio = area / frame_area

        quad = _contour_to_quad_relaxed(contour)
        if quad is None:
            continue

        if not cv2.isContourConvex(quad.astype(np.int32)):
            continue

        top, right, bottom, left = edge_lengths(quad)
        if min(top, right, bottom, left) < cfg.min_edge_px:
            continue

        area_s = _ratio_score(area_ratio, cfg.min_area_ratio, cfg.max_area_ratio)
        aspect_s = _aspect_score(quad, cfg.a4_ratio)
        edge_balance = min(top, bottom) / max(top, bottom) * min(left, right) / max(left, right)
        quad_area = abs(float(cv2.contourArea(quad.astype(np.float32))))
        fill_s = min(1.0, area / max(quad_area, 1.0))
        conf = 0.40 * area_s + 0.30 * aspect_s + 0.20 * edge_balance + 0.10 * fill_s

        if conf > best_conf:
            best_conf = conf
            best_quad = quad

    debug = {"edges": edges, "binary": binary, "merged": merged}
    return best_quad, float(best_conf), debug


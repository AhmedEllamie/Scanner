from __future__ import annotations

import cv2
import numpy as np


def order_points(points: np.ndarray) -> np.ndarray:
    pts = np.array(points, dtype=np.float32)
    if pts.shape != (4, 2):
        raise ValueError("order_points expects shape (4,2)")

    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(-1)

    top_left = pts[np.argmin(sums)]
    bottom_right = pts[np.argmax(sums)]
    top_right = pts[np.argmin(diffs)]
    bottom_left = pts[np.argmax(diffs)]

    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def contour_to_quad(contour: np.ndarray) -> np.ndarray | None:
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    if len(approx) != 4:
        return None
    quad = approx.reshape(4, 2).astype(np.float32)
    return order_points(quad)


def edge_lengths(quad: np.ndarray) -> tuple[float, float, float, float]:
    tl, tr, br, bl = quad
    top = float(np.linalg.norm(tr - tl))
    right = float(np.linalg.norm(br - tr))
    bottom = float(np.linalg.norm(br - bl))
    left = float(np.linalg.norm(bl - tl))
    return top, right, bottom, left


def smooth_quad(current: np.ndarray, previous: np.ndarray | None, alpha: float) -> np.ndarray:
    if previous is None:
        return current
    return (alpha * current + (1.0 - alpha) * previous).astype(np.float32)


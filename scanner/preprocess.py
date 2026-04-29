from __future__ import annotations

import cv2
import numpy as np

from .config import ScannerConfig


def to_gray(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def make_edge_mask(frame: np.ndarray, cfg: ScannerConfig) -> np.ndarray:
    gray = to_gray(frame)
    blur = cv2.GaussianBlur(gray, (cfg.gaussian_kernel, cfg.gaussian_kernel), 0)
    edges = cv2.Canny(blur, cfg.canny_low, cfg.canny_high)
    # Bridge small gaps along the outer page border (helpful when printed tables create dense inner edges).
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, k, iterations=1)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k, iterations=2)
    return edges


def make_binary_mask(frame: np.ndarray, cfg: ScannerConfig) -> np.ndarray:
    gray = to_gray(frame)
    _, fixed = cv2.threshold(gray, cfg.binary_threshold, 255, cv2.THRESH_BINARY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.bitwise_or(fixed, otsu)
    # Fill table/text holes inside the paper so we can recover a cleaner outer contour.
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_k, iterations=1)
    return mask


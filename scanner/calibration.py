from __future__ import annotations

import os

import cv2
import numpy as np

from .config import ScannerConfig


class FisheyeUndistorter:
    def __init__(self, cfg: ScannerConfig) -> None:
        self.enabled = bool(cfg.fisheye_correction_enabled)
        self.calibration_file = (cfg.fisheye_calibration_file or "").strip()
        self.balance = max(0.0, min(1.0, float(cfg.fisheye_balance)))
        self._map_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        self._k: np.ndarray | None = None
        self._d: np.ndarray | None = None
        self._warned = False

        if not self.enabled:
            return
        if not self.calibration_file:
            print("Fisheye correction is enabled, but fisheye_calibration_file is empty. Skipping.")
            self.enabled = False
            return
        if not os.path.exists(self.calibration_file):
            print(f"Fisheye calibration file not found: {self.calibration_file}. Skipping.")
            self.enabled = False
            return
        try:
            with np.load(self.calibration_file) as data:
                self._k = np.array(data["K"], dtype=np.float64)
                self._d = np.array(data["D"], dtype=np.float64).reshape(4, 1)
            if self._k.shape != (3, 3) or self._d.shape != (4, 1):
                raise ValueError(f"Unexpected calibration shapes K={self._k.shape}, D={self._d.shape}")
            print(f"Fisheye correction enabled using calibration: {self.calibration_file}")
        except Exception as exc:
            print(f"Failed to load fisheye calibration: {exc}. Skipping.")
            self.enabled = False

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if not self.enabled or self._k is None or self._d is None:
            return frame
        if frame.ndim != 3 or frame.shape[2] != 3:
            return frame

        h, w = frame.shape[:2]
        key = (w, h)
        maps = self._map_cache.get(key)
        if maps is None:
            try:
                dim = (w, h)
                identity_r = np.eye(3, dtype=np.float64)
                new_k = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                    self._k,
                    self._d,
                    dim,
                    identity_r,
                    balance=self.balance,
                )
                map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                    self._k,
                    self._d,
                    identity_r,
                    new_k,
                    dim,
                    cv2.CV_16SC2,
                )
                maps = (map1, map2)
                self._map_cache[key] = maps
            except Exception as exc:
                if not self._warned:
                    print(f"Fisheye undistort map init failed: {exc}. Disabling correction.")
                    self._warned = True
                self.enabled = False
                return frame

        map1, map2 = maps
        return cv2.remap(frame, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

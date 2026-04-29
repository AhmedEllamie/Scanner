from __future__ import annotations

import sys

import cv2

from .config import ScannerConfig


def camera_api_preference(cfg: ScannerConfig) -> int:
    name = (cfg.camera_backend or "").strip().upper()
    if sys.platform == "win32":
        if name in ("MSMF", "CAP_MSMF", "MEDIA_FOUNDATION"):
            return cv2.CAP_MSMF
        if name in ("DSHOW", "DIRECTSHOW", "CAP_DSHOW"):
            return cv2.CAP_DSHOW
        return int(cv2.CAP_ANY)
    if name in ("V4L2", "CAP_V4L2"):
        return int(cv2.CAP_V4L2)
    v4l2 = getattr(cv2, "CAP_V4L2", None)
    if sys.platform.startswith("linux") and v4l2 is not None and not name:
        return int(v4l2)
    return int(cv2.CAP_ANY)


def open_video_capture(cfg: ScannerConfig) -> cv2.VideoCapture | None:
    api = camera_api_preference(cfg)
    cap = cv2.VideoCapture(cfg.camera_index, api)
    if cap.isOpened():
        return cap
    if cfg.camera_index != 0:
        cap0 = cv2.VideoCapture(0, api)
        if cap0.isOpened():
            print(f"Note: opened camera index 0 (index {cfg.camera_index} failed).")
            return cap0
    cap_any = cv2.VideoCapture(cfg.camera_index)
    if cap_any.isOpened():
        return cap_any
    return None


def apply_camera_settings(cap: cv2.VideoCapture, cfg: ScannerConfig) -> tuple[int, int]:
    autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    if autofocus_prop is not None:
        try:
            cap.set(autofocus_prop, 1.0 if cfg.camera_autofocus_enabled else 0.0)
        except Exception:
            pass

    fourcc = (cfg.camera_fourcc or "").strip().upper()
    if len(fourcc) == 4:
        try:
            code = cv2.VideoWriter_fourcc(*fourcc)
            cap.set(cv2.CAP_PROP_FOURCC, code)
        except Exception:
            pass
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_height)

    focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
    if not cfg.camera_autofocus_enabled and focus_prop is not None and cfg.camera_manual_focus >= 0:
        try:
            cap.set(focus_prop, float(cfg.camera_manual_focus))
        except Exception:
            pass

    cap.read()
    aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if autofocus_prop is not None:
        try:
            af_now = cap.get(autofocus_prop)
            print(f"Camera autofocus: {'ON' if af_now >= 0.5 else 'OFF'}")
        except Exception:
            pass
    print(
        f"Camera actual resolution: {aw}x{ah} (requested {cfg.frame_width}x{cfg.frame_height})"
    )
    if aw < cfg.frame_width * 0.95 or ah < cfg.frame_height * 0.95:
        print(
            "Hint: If Windows Camera shows higher resolution, try ScannerConfig.camera_backend "
            "'MSMF' or 'DSHOW', or set camera_fourcc to '' / 'MJPG' and retry."
        )
    return aw, ah


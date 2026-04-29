from dataclasses import dataclass
import os
import sys


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_camera_index() -> int:
    # Ubuntu/Linux usually exposes the first webcam at /dev/video0.
    if sys.platform.startswith("linux"):
        return 0
    return 1


def _default_camera_backend() -> str:
    # Linux should use V4L2/CAP_ANY; Windows often benefits from DSHOW/MSMF.
    if sys.platform.startswith("linux"):
        return ""
    return "DSHOW"


@dataclass
class ScannerConfig:
    # Linux/Ubuntu: first USB webcam is usually 0. Use 1 if you have multiple cameras.
    camera_index: int = _env_int("SCAN_CAMERA_INDEX", _default_camera_index())
    # Windows: try "MSMF" or "DSHOW" if resolution is wrong. Linux: leave "" (V4L2) or set "V4L2".
    camera_backend: str = os.getenv("SCAN_CAMERA_BACKEND", _default_camera_backend())
    # Requested capture size (USB cams may fall back if unsupported — check console for actual size).
    frame_width: int = 3840
    frame_height: int = 2160
    # Use MJPEG on many UVC cameras so 4K is possible over USB; set "" to let the driver choose.
    camera_fourcc: str = os.getenv("SCAN_CAMERA_FOURCC", "MJPG")
    # Camera focus controls (hardware/backend dependent).
    camera_autofocus_enabled: bool = True
    # Set >=0 to request manual focus value when autofocus is disabled.
    camera_manual_focus: float = -1.0
    # Focus increment used by keyboard shortcuts +/- in webcam mode.
    camera_focus_step: float = 5.0
    preview_scale: float = 1.0

    gaussian_kernel: int = 5
    canny_low: int = 75
    canny_high: int = 180
    binary_threshold: int = 140

    min_area_ratio: float = 0.12
    max_area_ratio: float = 0.95
    min_edge_px: float = 45.0

    a4_ratio: float = 1.41421356237
    # Output rectified width (short side of A4 portrait target). Higher = sharper file, more CPU/RAM.
    warp_short_side: int = 2200
    # Tie warp output to camera resolution so 4K input is not thrown away (Orange Pi: lower caps / disable).
    # On a PC with 1080p capture, warp_capture_scale=0.58 yields ~626px then clamps to warp_short_side_min (900),
    # so saved scans stay soft unless you raise the scale (e.g. 1.0) or set scale_warp_to_capture=False.
    scale_warp_to_capture: bool = True
    warp_capture_scale: float = 0.58
    warp_short_side_min: int = 900
    warp_short_side_max: int = 4000
    # Perspective resampling: linear (fast), cubic (default), lanczos4 (slowest, often sharpest).
    warp_interpolation: str = "cubic"
    # If the detected page looks landscape in the camera view, rotate it to portrait on output.
    auto_rotate_landscape_to_portrait: bool = True
    # Rotation direction used only when auto_rotate_landscape_to_portrait is active.
    # "ccw" fixes the common case where paper is rotated 90 degrees to the right (clockwise) in camera.
    landscape_rotation_direction: str = "ccw"  # "ccw" or "cw"
    max_display_width: int = 900
    max_display_height: int = 700

    smoothing_alpha: float = 0.24
    confidence_threshold: float = 0.62
    start_mode: str = "MANUAL"  # Start in manual mode by default

    save_dir: str = "output"
    # If False, do not write rectified images to local disk.
    save_rectified_locally: bool = True
    # If True, save the pre-processed capture frame with received 4-point quad overlay (API debug aid).
    save_debug_capture_with_quad: bool = True
    # Folder for debug snapshots produced by save_debug_capture_with_quad.
    debug_capture_dir: str = "output/debug"
    # Lens distortion correction (fisheye model) applied before document detection.
    fisheye_correction_enabled: bool = True
    # Path to .npz calibration file with keys: "K" (3x3 camera matrix), "D" (4x1 coefficients).
    fisheye_calibration_file: str = os.getenv(
        "SCAN_FISHEYE_CALIBRATION_FILE",
        "calibration/fisheye_test.npz",
    )
    # 0.0 = tighter crop with straighter lines, 1.0 = wider FOV with more edge distortion.
    fisheye_balance: float = 0.2
    apply_scan_enhancement: bool = True
    # Enhancement pipeline (gamma -> CLAHE in LAB -> saturation boost -> sharpening).
    enhance_gamma: float = 1.08
    enhance_clahe_clip_limit: float = 2.0
    enhance_clahe_tile_size: int = 8
    enhance_saturation_boost: float = 1.10
    enhance_sharpen_strength: float = 0.55

    # Optional readability verification (OCR based)
    enable_readability_check: bool = True
    readability_mode: str = "fast"  # "fast" for Orange Pi, "ocr" for Tesseract
    min_readability_confidence: float = 6.0
    tesseract_cmd: str = os.getenv("TESSERACT_CMD", "")
    require_readable_to_save: bool = True

    # Optional API upload config
    upload_enabled: bool = True
    upload_url: str = os.getenv("SCAN_UPLOAD_URL", "http://127.0.0.1:5001/api/capture")
    upload_token: str = os.getenv("SCAN_UPLOAD_TOKEN", "")
    upload_timeout_seconds: int = 15
    upload_field_name: str = "file"

    # Upload cleanup / storage strategy
    # - upload_from_memory=True: do not write a file to disk unless upload fails (save_on_upload_fail).
    # - upload_from_memory=False: save to disk, upload from file, then optionally delete after successful upload.
    upload_from_memory: bool = True
    delete_after_upload_success: bool = True
    save_on_upload_fail: bool = True

    # Fully automatic capture (no keyboard save required)
    auto_capture_enabled: bool = False
    auto_capture_stable_frames: int = 8
    # True = one readable capture, then lock until reset (API or manual).
    single_capture_until_api_reset: bool = True
    capture_reset_url: str = os.getenv("SCAN_CAPTURE_RESET_URL", "")
    capture_reset_token: str = os.getenv("SCAN_CAPTURE_RESET_TOKEN", "")
    capture_reset_poll_interval_seconds: float = 1.0
    capture_reset_timeout_seconds: int = 5

    # Optional API call when a capture is rejected due to low readability.
    unreadable_notify_enabled: bool = False
    unreadable_notify_url: str = os.getenv("SCAN_UNREADABLE_NOTIFY_URL", "")
    unreadable_notify_token: str = os.getenv("SCAN_UNREADABLE_NOTIFY_TOKEN", "")
    unreadable_notify_timeout_seconds: int = 10


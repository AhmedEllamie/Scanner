from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Sequence

import cv2
import numpy as np

from .calibration import FisheyeUndistorter
from .camera import apply_camera_settings, open_video_capture
from .config import ScannerConfig
from .geometry import edge_lengths, order_points
from .readability import ReadabilityResult, verify_readability
from .warp import a4_target_size, compute_warp_short_side, enhance_for_scan, warp_document


@dataclass
class ManualCaptureResult:
    ok: bool
    status: str
    message: str
    png_bytes: bytes | None
    frame_width: int
    frame_height: int
    readability: ReadabilityResult | None
    elapsed_ms: int


def encode_png_bytes(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image)
    if not ok or buf is None:
        raise ValueError("Failed to encode rectified image as PNG")
    return buf.tobytes()


def _coerce_point(point: object) -> tuple[float, float]:
    if isinstance(point, dict):
        if "x" not in point or "y" not in point:
            raise ValueError("Point dict must contain x and y keys")
        return float(point["x"]), float(point["y"])
    if isinstance(point, (list, tuple)) and len(point) == 2:
        return float(point[0]), float(point[1])
    raise ValueError("Each point must be [x, y] or {'x':..., 'y':...}")


def normalize_quad_points(points: Sequence[object]) -> np.ndarray:
    if len(points) != 4:
        raise ValueError("quad_points must contain exactly 4 points")
    pts = np.array([_coerce_point(p) for p in points], dtype=np.float32)
    return order_points(pts)


def validate_quad_within_frame(
    quad: np.ndarray,
    frame_width: int,
    frame_height: int,
    min_edge_px: float,
) -> tuple[bool, str]:
    if quad.shape != (4, 2):
        return False, "Quad must have shape (4, 2)"

    xs = quad[:, 0]
    ys = quad[:, 1]
    if np.any(xs < 0) or np.any(xs > frame_width - 1):
        return False, "Quad x coordinates are outside camera frame"
    if np.any(ys < 0) or np.any(ys > frame_height - 1):
        return False, "Quad y coordinates are outside camera frame"

    if not cv2.isContourConvex(quad.astype(np.int32)):
        return False, "Quad points must form a convex polygon"

    top, right, bottom, left = edge_lengths(quad)
    if min(top, right, bottom, left) < float(min_edge_px):
        return False, f"Quad edges are too small; minimum edge must be >= {min_edge_px:.1f}px"

    area = abs(float(cv2.contourArea(quad.astype(np.float32))))
    min_area = max(1.0, frame_width * frame_height * 0.005)
    if area < min_area:
        return False, "Quad area is too small for a reliable rectification"
    return True, "ok"


def _apply_focus_overrides(
    cap: cv2.VideoCapture,
    autofocus_enabled: bool,
    manual_focus_value: float | None,
) -> None:
    autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    if autofocus_prop is not None:
        try:
            cap.set(autofocus_prop, 1.0 if autofocus_enabled else 0.0)
        except Exception:
            pass

    if autofocus_enabled:
        return

    if manual_focus_value is None:
        return

    focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
    if focus_prop is None:
        return
    try:
        cap.set(focus_prop, float(manual_focus_value))
    except Exception:
        pass


def _read_frame(cap: cv2.VideoCapture, attempts: int = 4) -> np.ndarray:
    for _ in range(max(1, attempts)):
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            return frame
        time.sleep(0.03)
    raise RuntimeError("Camera frame read failed")


def peek_frame_size(
    cfg: ScannerConfig,
    autofocus_enabled: bool,
    manual_focus_value: float | None,
) -> tuple[int, int]:
    cap = open_video_capture(cfg)
    if cap is None or not cap.isOpened():
        raise RuntimeError("Cannot open camera")
    try:
        apply_camera_settings(cap, cfg)
        _apply_focus_overrides(cap, autofocus_enabled=autofocus_enabled, manual_focus_value=manual_focus_value)
        frame = _read_frame(cap, attempts=3)
        h, w = frame.shape[:2]
        return int(w), int(h)
    finally:
        cap.release()


def capture_rectified_manual_png(
    cfg: ScannerConfig,
    quad_points: Sequence[object],
    *,
    autofocus_enabled: bool,
    manual_focus_value: float | None,
    readability_required: bool | None = None,
    timeout_seconds: float = 15.0,
) -> ManualCaptureResult:
    started = time.time()
    frame_w = 0
    frame_h = 0

    cap = open_video_capture(cfg)
    if cap is None or not cap.isOpened():
        return ManualCaptureResult(
            ok=False,
            status="camera_unavailable",
            message="Cannot open camera",
            png_bytes=None,
            frame_width=frame_w,
            frame_height=frame_h,
            readability=None,
            elapsed_ms=int((time.time() - started) * 1000),
        )

    try:
        apply_camera_settings(cap, cfg)
        _apply_focus_overrides(cap, autofocus_enabled=autofocus_enabled, manual_focus_value=manual_focus_value)
        frame = _read_frame(cap, attempts=4)
    except Exception as exc:
        cap.release()
        return ManualCaptureResult(
            ok=False,
            status="camera_read_failed",
            message=str(exc),
            png_bytes=None,
            frame_width=frame_w,
            frame_height=frame_h,
            readability=None,
            elapsed_ms=int((time.time() - started) * 1000),
        )
    finally:
        # Keep explicit release in both success and error paths.
        if cap.isOpened():
            cap.release()

    try:
        frame = FisheyeUndistorter(cfg).apply(frame)
        frame_h, frame_w = frame.shape[:2]
        quad = normalize_quad_points(quad_points)
        valid, reason = validate_quad_within_frame(quad, frame_w, frame_h, cfg.min_edge_px)
        if not valid:
            return ManualCaptureResult(
                ok=False,
                status="invalid_quad",
                message=reason,
                png_bytes=None,
                frame_width=frame_w,
                frame_height=frame_h,
                readability=None,
                elapsed_ms=int((time.time() - started) * 1000),
            )

        warp_short = compute_warp_short_side(frame_w, frame_h, cfg)
        dst_size = a4_target_size(warp_short, cfg.a4_ratio)
        warped = warp_document(frame, quad, dst_size, cfg=cfg)
        result = enhance_for_scan(warped, cfg=cfg) if cfg.apply_scan_enhancement else warped

        readability_result: ReadabilityResult | None = None
        require_readable = cfg.require_readable_to_save if readability_required is None else readability_required
        if cfg.enable_readability_check:
            readability_result = verify_readability(
                result,
                min_confidence=cfg.min_readability_confidence,
                tesseract_cmd=cfg.tesseract_cmd,
                mode=cfg.readability_mode,
            )
            if require_readable and not readability_result.readable:
                return ManualCaptureResult(
                    ok=False,
                    status="unreadable",
                    message=readability_result.message,
                    png_bytes=None,
                    frame_width=frame_w,
                    frame_height=frame_h,
                    readability=readability_result,
                    elapsed_ms=int((time.time() - started) * 1000),
                )

        png_bytes = encode_png_bytes(result)
        elapsed_ms = int((time.time() - started) * 1000)
        if timeout_seconds > 0 and elapsed_ms > int(timeout_seconds * 1000):
            return ManualCaptureResult(
                ok=False,
                status="timeout",
                message=f"Capture exceeded timeout ({timeout_seconds:.1f}s)",
                png_bytes=None,
                frame_width=frame_w,
                frame_height=frame_h,
                readability=readability_result,
                elapsed_ms=elapsed_ms,
            )
        return ManualCaptureResult(
            ok=True,
            status="succeeded",
            message="Capture completed",
            png_bytes=png_bytes,
            frame_width=frame_w,
            frame_height=frame_h,
            readability=readability_result,
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        return ManualCaptureResult(
            ok=False,
            status="processing_failed",
            message=str(exc),
            png_bytes=None,
            frame_width=frame_w,
            frame_height=frame_h,
            readability=None,
            elapsed_ms=int((time.time() - started) * 1000),
        )


def process_rectified_manual_frame(
    frame: np.ndarray,
    cfg: ScannerConfig,
    quad_points: Sequence[object],
    *,
    readability_required: bool | None = None,
    timeout_seconds: float = 15.0,
) -> ManualCaptureResult:
    started = time.time()
    if frame is None or frame.size == 0:
        return ManualCaptureResult(
            ok=False,
            status="frame_unavailable",
            message="No frame provided",
            png_bytes=None,
            frame_width=0,
            frame_height=0,
            readability=None,
            elapsed_ms=0,
        )

    try:
        work_frame = FisheyeUndistorter(cfg).apply(frame)
        frame_h, frame_w = work_frame.shape[:2]
        quad = normalize_quad_points(quad_points)
        valid, reason = validate_quad_within_frame(quad, frame_w, frame_h, cfg.min_edge_px)
        if not valid:
            return ManualCaptureResult(
                ok=False,
                status="invalid_quad",
                message=reason,
                png_bytes=None,
                frame_width=frame_w,
                frame_height=frame_h,
                readability=None,
                elapsed_ms=int((time.time() - started) * 1000),
            )

        warp_short = compute_warp_short_side(frame_w, frame_h, cfg)
        dst_size = a4_target_size(warp_short, cfg.a4_ratio)
        warped = warp_document(work_frame, quad, dst_size, cfg=cfg)
        result = enhance_for_scan(warped, cfg=cfg) if cfg.apply_scan_enhancement else warped

        readability_result: ReadabilityResult | None = None
        require_readable = cfg.require_readable_to_save if readability_required is None else readability_required
        if cfg.enable_readability_check:
            readability_result = verify_readability(
                result,
                min_confidence=cfg.min_readability_confidence,
                tesseract_cmd=cfg.tesseract_cmd,
                mode=cfg.readability_mode,
            )
            if require_readable and not readability_result.readable:
                return ManualCaptureResult(
                    ok=False,
                    status="unreadable",
                    message=readability_result.message,
                    png_bytes=None,
                    frame_width=frame_w,
                    frame_height=frame_h,
                    readability=readability_result,
                    elapsed_ms=int((time.time() - started) * 1000),
                )

        png_bytes = encode_png_bytes(result)
        elapsed_ms = int((time.time() - started) * 1000)
        if timeout_seconds > 0 and elapsed_ms > int(timeout_seconds * 1000):
            return ManualCaptureResult(
                ok=False,
                status="timeout",
                message=f"Capture exceeded timeout ({timeout_seconds:.1f}s)",
                png_bytes=None,
                frame_width=frame_w,
                frame_height=frame_h,
                readability=readability_result,
                elapsed_ms=elapsed_ms,
            )
        return ManualCaptureResult(
            ok=True,
            status="succeeded",
            message="Capture completed",
            png_bytes=png_bytes,
            frame_width=frame_w,
            frame_height=frame_h,
            readability=readability_result,
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:
        return ManualCaptureResult(
            ok=False,
            status="processing_failed",
            message=str(exc),
            png_bytes=None,
            frame_width=0,
            frame_height=0,
            readability=None,
            elapsed_ms=int((time.time() - started) * 1000),
        )


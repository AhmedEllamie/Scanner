from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np

from scanner import (
    ManualSelector,
    ScannerConfig,
    a4_target_size,
    apply_camera_settings,
    check_capture_reset_api,
    compute_warp_short_side,
    detect_document_quad,
    encode_png_bytes,
    enhance_for_scan,
    notify_unreadable_capture,
    open_video_capture,
    smooth_quad,
    upload_scan,
    upload_scan_bytes,
    verify_readability,
    warp_document,
)
from scanner.readability import ReadabilityResult


def put_status(
    frame: np.ndarray,
    mode: str,
    confidence: float,
    fps: float,
    saves: int,
    auto_status: str,
    focus_status: str,
    manual_status: str,
    keys_status: str,
) -> None:
    lines = [
        f"Mode: {mode}",
        f"Confidence: {confidence:.2f}",
        f"FPS: {fps:.1f}",
        f"Saved: {saves}",
        auto_status,
        focus_status,
        manual_status,
        keys_status,
    ]
    y = 28
    for line in lines:
        cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 0), 2, cv2.LINE_AA)
        y += 28


def draw_quad(frame: np.ndarray, quad: np.ndarray, color: tuple[int, int, int]) -> None:
    pts = quad.astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(frame, [pts], True, color, 3, cv2.LINE_AA)


def save_scan(image: np.ndarray, save_dir: str) -> str:
    os.makedirs(save_dir, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png"
    path = os.path.join(save_dir, name)
    cv2.imwrite(path, image)
    return path


def fit_for_display(image: np.ndarray, max_width: int, max_height: int) -> np.ndarray:
    h, w = image.shape[:2]
    if w <= max_width and h <= max_height:
        return image
    scale = min(max_width / float(w), max_height / float(h))
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def persist_capture(
    image: np.ndarray,
    cfg: ScannerConfig,
    readability_result: ReadabilityResult | None = None,
) -> None:
    if not cfg.save_rectified_locally:
        run_post_processors(image, image_path="", cfg=cfg, readability_result=readability_result)
        print("Local save disabled by config; rectified image was not written to disk.")
        return

    # Memory mode keeps disk clean; local fallback is handled in run_post_processors on upload failure.
    if cfg.upload_enabled and cfg.upload_url and cfg.upload_from_memory:
        run_post_processors(image, image_path="", cfg=cfg, readability_result=readability_result)
        print("Rectified image uploaded from memory (local file kept only if upload fails).")
        return

    out_path = save_scan(image, cfg.save_dir)
    run_post_processors(image, out_path, cfg, readability_result=readability_result)
    if os.path.exists(out_path):
        print(f"Saved rectified image: {out_path}")
    else:
        print("Upload successful; local file deleted after upload.")


def notify_unreadable(
    cfg: ScannerConfig,
    detector_confidence: float,
    readability_result: ReadabilityResult | None,
) -> bool:
    if not cfg.unreadable_notify_enabled:
        return False
    if not cfg.unreadable_notify_url:
        print(
            "Unreadable capture detected; notify API skipped (set unreadable_notify_url to enable)."
        )
        return False

    reason = readability_result.message if readability_result is not None else "Low readability"
    readability_confidence = readability_result.mean_confidence if readability_result is not None else 0.0
    readability_tokens = readability_result.token_count if readability_result is not None else 0
    u = notify_unreadable_capture(
        notify_url=cfg.unreadable_notify_url,
        detector_confidence=detector_confidence,
        readability_confidence=readability_confidence,
        readability_tokens=readability_tokens,
        reason=reason,
        api_token=cfg.unreadable_notify_token or None,
        timeout_seconds=cfg.unreadable_notify_timeout_seconds,
    )
    print(f"Unreadable notify: {u.message} (status={u.status_code})")
    if u.response_preview:
        print(f"Unreadable notify response: {u.response_preview}")
    return u.ok


def clear_capture_lock_if_api_allows(
    cfg: ScannerConfig,
    now: float,
    capture_locked: bool,
    last_reset_poll_ts: float,
    last_reset_error_ts: float,
) -> tuple[bool, float, float]:
    if not capture_locked:
        return capture_locked, last_reset_poll_ts, last_reset_error_ts
    if not cfg.capture_reset_url:
        return capture_locked, last_reset_poll_ts, last_reset_error_ts
    if (now - last_reset_poll_ts) < max(0.05, cfg.capture_reset_poll_interval_seconds):
        return capture_locked, last_reset_poll_ts, last_reset_error_ts

    last_reset_poll_ts = now
    reset_state = check_capture_reset_api(
        reset_url=cfg.capture_reset_url,
        api_token=cfg.capture_reset_token or None,
        timeout_seconds=cfg.capture_reset_timeout_seconds,
    )
    if reset_state.ok and reset_state.allow_capture:
        print("Capture lock cleared by reset API. Ready for next photo.")
        return False, last_reset_poll_ts, last_reset_error_ts
    if not reset_state.ok and (now - last_reset_error_ts) >= 5.0:
        print(f"Reset API warning: {reset_state.message} (status={reset_state.status_code})")
        last_reset_error_ts = now
    return capture_locked, last_reset_poll_ts, last_reset_error_ts


def process_single_image(image_path: str, cfg: ScannerConfig, show_windows: bool = True) -> int:
    frame = cv2.imread(image_path)
    if frame is None:
        print(f"Error: Cannot read image: {image_path}")
        return 1

    fh, fw = frame.shape[:2]
    warp_short = compute_warp_short_side(fw, fh, cfg)
    dst_size = a4_target_size(warp_short, cfg.a4_ratio)
    quad, confidence, _debug = detect_document_quad(frame, cfg)
    vis = frame.copy()

    if quad is None or confidence < cfg.confidence_threshold:
        print("No confident document detection in this image.")
        print(f"Confidence: {confidence:.2f} (threshold: {cfg.confidence_threshold:.2f})")
        if show_windows:
            cv2.imshow(
                "A4 Scanner - Input",
                fit_for_display(vis, cfg.max_display_width, cfg.max_display_height),
            )
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return 2

    draw_quad(vis, quad, (0, 255, 0))
    warped = warp_document(frame, quad, dst_size, cfg=cfg)
    result = enhance_for_scan(warped, cfg=cfg) if cfg.apply_scan_enhancement else warped

    can_save, readability_result = can_save_by_readability(result, cfg)
    if not can_save:
        notify_unreadable(cfg, confidence, readability_result)
        if show_windows:
            cv2.imshow(
                "A4 Scanner - Input",
                fit_for_display(vis, cfg.max_display_width, cfg.max_display_height),
            )
            cv2.imshow(
                "A4 Scanner - Rectified",
                fit_for_display(result, cfg.max_display_width, cfg.max_display_height),
            )
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return 3

    print(f"Detected with confidence: {confidence:.2f}")
    persist_capture(result, cfg, readability_result=readability_result)

    if show_windows:
        cv2.imshow(
            "A4 Scanner - Input",
            fit_for_display(vis, cfg.max_display_width, cfg.max_display_height),
        )
        cv2.imshow(
            "A4 Scanner - Rectified",
            fit_for_display(result, cfg.max_display_width, cfg.max_display_height),
        )
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adaptive A4 document scanner")
    parser.add_argument(
        "--image",
        type=str,
        default=None,
        help="Path to a saved photo to process once instead of webcam mode.",
    )
    parser.add_argument(
        "--verify-readable",
        action="store_true",
        help="Run OCR readability verification on the flattened image.",
    )
    parser.add_argument(
        "--upload-url",
        type=str,
        default="",
        help="If set, upload saved scans to this API endpoint.",
    )
    parser.add_argument(
        "--upload-token",
        type=str,
        default="",
        help="Optional bearer token for the upload API.",
    )
    parser.add_argument(
        "--tesseract-cmd",
        type=str,
        default="",
        help="Full path to tesseract executable (if not in PATH).",
    )
    parser.add_argument(
        "--autofocus",
        type=str,
        choices=("on", "off"),
        default="",
        help="Camera autofocus mode.",
    )
    parser.add_argument(
        "--manual-focus",
        type=float,
        default=None,
        help="Manual focus value (applied when autofocus is off).",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=None,
        help="Camera device index (Ubuntu: usually 0 for first USB webcam).",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Disable OpenCV windows (useful on headless Ubuntu/Orange Pi).",
    )
    parser.add_argument(
        "--save-local",
        type=str,
        choices=("true", "false"),
        default="",
        help="Enable/disable saving rectified images to local disk.",
    )
    parser.add_argument(
        "--capture-reset-url",
        type=str,
        default="",
        help="API endpoint that unlocks next capture when it returns allow_capture=true.",
    )
    parser.add_argument(
        "--capture-reset-token",
        type=str,
        default="",
        help="Optional bearer token for capture reset API.",
    )
    parser.add_argument(
        "--unreadable-notify-url",
        type=str,
        default="",
        help="Optional API endpoint to call when readability gate rejects a capture.",
    )
    parser.add_argument(
        "--unreadable-notify-token",
        type=str,
        default="",
        help="Optional bearer token for unreadable-capture notify endpoint.",
    )
    return parser.parse_args()


def run_post_processors(
    image: np.ndarray,
    image_path: str,
    cfg: ScannerConfig,
    readability_result: ReadabilityResult | None = None,
) -> None:
    if cfg.enable_readability_check:
        r = readability_result
        if r is None:
            r = verify_readability(
                image,
                min_confidence=cfg.min_readability_confidence,
                tesseract_cmd=cfg.tesseract_cmd,
                mode=cfg.readability_mode,
            )
        print(
            "Readability:",
            f"{r.message} | readable={r.readable} | mean_conf={r.mean_confidence:.2f} | tokens={r.token_count}",
        )

    if cfg.upload_enabled and cfg.upload_url:
        # Memory upload: avoid writing files on successful uploads.
        if cfg.upload_from_memory:
            image_bytes = encode_png_bytes(image)
            filename = "scan.png"
            u = upload_scan_bytes(
                image_bytes=image_bytes,
                filename=filename,
                upload_url=cfg.upload_url,
                api_token=cfg.upload_token or None,
                timeout_seconds=cfg.upload_timeout_seconds,
                field_name=cfg.upload_field_name,
            )
            print(f"Upload: {u.message} (status={u.status_code})")
            if u.response_preview:
                print(f"Upload response: {u.response_preview}")

            if not u.ok and cfg.save_on_upload_fail:
                if cfg.save_rectified_locally:
                    saved_path = save_scan(image, cfg.save_dir)
                    print(f"Upload failed; kept local copy: {saved_path}")
                else:
                    print("Upload failed; local fallback copy skipped because save_local is disabled.")
            return

        # Disk upload: upload the saved file, then optionally delete it.
        u = upload_scan(
            image_path=image_path,
            upload_url=cfg.upload_url,
            api_token=cfg.upload_token or None,
            timeout_seconds=cfg.upload_timeout_seconds,
            field_name=cfg.upload_field_name,
        )
        print(f"Upload: {u.message} (status={u.status_code})")
        if u.response_preview:
            print(f"Upload response: {u.response_preview}")

        if u.ok and cfg.delete_after_upload_success:
            try:
                os.remove(image_path)
                print(f"Deleted local copy after successful upload: {image_path}")
            except Exception as exc:
                print(f"Warning: could not delete local copy: {exc}")


def can_save_by_readability(
    image: np.ndarray,
    cfg: ScannerConfig,
) -> tuple[bool, ReadabilityResult | None]:
    if not cfg.enable_readability_check or not cfg.require_readable_to_save:
        return True, None

    r = verify_readability(
        image,
        min_confidence=cfg.min_readability_confidence,
        tesseract_cmd=cfg.tesseract_cmd,
        mode=cfg.readability_mode,
    )
    print(
        "Readability gate:",
        f"{r.message} | readable={r.readable} | mean_conf={r.mean_confidence:.2f} | tokens={r.token_count}",
    )
    if not r.readable:
        print("Save skipped: flattened image is not readable by current OCR threshold.")
    return r.readable, r


def run_webcam(cfg: ScannerConfig) -> int:
    cap = open_video_capture(cfg)
    if cap is None or not cap.isOpened():
        print("Error: Cannot open camera.")
        print(f"- Tried index {cfg.camera_index} (use --camera N or set ScannerConfig.camera_index).")
        print("- On Ubuntu the first webcam is usually index 0: python main.py --camera 0")
        if sys.platform.startswith("linux"):
            print('- List devices: v4l2-ctl --list-devices   (install: sudo apt install v4l-utils)')
            print("- Permissions: sudo usermod -aG video $USER  (then log out and back in)")
            print('- If config has camera_backend "DSHOW"/"MSMF", leave it empty on Linux or use "V4L2".')
        return 1
    aw, ah = apply_camera_settings(cap, cfg)
    warp_short = compute_warp_short_side(aw, ah, cfg)
    dst_size = a4_target_size(warp_short, cfg.a4_ratio)
    print(f"Rectified output size (approx A4): {dst_size[0]}x{dst_size[1]} (short side {warp_short})")

    cv2.namedWindow("A4 Scanner", cv2.WINDOW_NORMAL)
    cv2.namedWindow("Rectified", cv2.WINDOW_NORMAL)

    selector = ManualSelector("A4 Scanner")
    cv2.setMouseCallback("A4 Scanner", selector.on_mouse)

    mode = cfg.start_mode.strip().upper() if cfg.start_mode else "AUTO"
    if mode not in {"AUTO", "MANUAL"}:
        mode = "AUTO"
    manual_step = "FOCUS"
    capture_lock_enabled = cfg.single_capture_until_api_reset
    if capture_lock_enabled and not cfg.capture_reset_url:
        print(
            "Info: capture reset API is not set. "
            "After one readable save, scanner locks until manual reset key [r]."
        )
    prev_quad = None
    warped_preview = np.zeros((dst_size[1], dst_size[0], 3), dtype=np.uint8)

    frame_count = 0
    detect_hits = 0
    total_conf = 0.0
    save_count = 0
    stable_frames = 0
    capture_locked = False
    last_reset_poll_ts = 0.0
    last_reset_error_ts = 0.0
    autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
    focus_step = max(0.1, float(cfg.camera_focus_step))
    focus_is_auto = cfg.camera_autofocus_enabled
    if autofocus_prop is not None:
        try:
            focus_is_auto = cap.get(autofocus_prop) >= 0.5
        except Exception:
            pass
    manual_focus_value: float | None = cfg.camera_manual_focus if cfg.camera_manual_focus >= 0 else None
    if focus_prop is not None:
        try:
            f_now = cap.get(focus_prop)
            if f_now >= 0:
                manual_focus_value = f_now
        except Exception:
            pass

    t0 = time.time()
    prev_fps_t = time.time()
    fps = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Warning: Camera read failed. Exiting loop.")
                break

            frame_count += 1
            now = time.time()
            dt = now - prev_fps_t
            if dt > 0:
                fps = 1.0 / dt
            prev_fps_t = now

            vis = frame.copy()
            active_quad = None
            confidence = 0.0

            if mode == "AUTO":
                selector.enabled = False
                quad, confidence, _debug = detect_document_quad(frame, cfg)
                if quad is not None:
                    total_conf += confidence
                    if confidence >= cfg.confidence_threshold:
                        detect_hits += 1
                        active_quad = smooth_quad(quad, prev_quad, cfg.smoothing_alpha)
                        prev_quad = active_quad
            else:
                selector.enabled = manual_step == "POINTS"
                vis = selector.draw(vis)
                if manual_step == "POINTS":
                    manual_quad = selector.get_quad()
                    if manual_quad is not None:
                        active_quad = manual_quad
                        confidence = 1.0

            if active_quad is not None:
                draw_quad(vis, active_quad, (0, 255, 255) if mode == "MANUAL" else (0, 255, 0))
                warped = warp_document(frame, active_quad, dst_size, cfg=cfg)
                warped_preview = enhance_for_scan(warped, cfg=cfg) if cfg.apply_scan_enhancement else warped
            else:
                stable_frames = 0

            if capture_lock_enabled and cfg.capture_reset_url:
                capture_locked, last_reset_poll_ts, last_reset_error_ts = clear_capture_lock_if_api_allows(
                    cfg=cfg,
                    now=now,
                    capture_locked=capture_locked,
                    last_reset_poll_ts=last_reset_poll_ts,
                    last_reset_error_ts=last_reset_error_ts,
                )

            auto_active = cfg.auto_capture_enabled and mode == "AUTO"
            if auto_active and active_quad is not None and not capture_locked:
                stable_frames += 1
                if stable_frames >= max(1, cfg.auto_capture_stable_frames):
                    can_save, readability_result = can_save_by_readability(warped_preview, cfg)
                    if can_save:
                        persist_capture(warped_preview, cfg, readability_result=readability_result)
                        save_count += 1
                        if capture_lock_enabled:
                            capture_locked = True
                            if cfg.capture_reset_url:
                                print("Capture lock raised: waiting for reset API before next photo.")
                            else:
                                print("Capture lock raised: press [r] to allow next photo.")
                    else:
                        notify_unreadable(cfg, confidence, readability_result)
                    stable_frames = 0

            if auto_active:
                if capture_locked:
                    if cfg.capture_reset_url:
                        auto_status = "Auto capture: LOCKED | waiting reset API"
                    else:
                        auto_status = "Auto capture: LOCKED | press [r] to reset"
                else:
                    auto_status = f"Auto capture: READY {stable_frames}/{max(1, cfg.auto_capture_stable_frames)}"
            else:
                auto_status = "Auto capture: OFF (switch to AUTO mode)"

            if focus_prop is not None:
                try:
                    f_now = cap.get(focus_prop)
                    if f_now >= 0:
                        manual_focus_value = f_now
                except Exception:
                    pass
            if autofocus_prop is None and focus_prop is None:
                focus_status = "Focus: unsupported by camera/backend"
            elif focus_is_auto:
                focus_status = "Focus: AUTO"
            elif manual_focus_value is not None:
                focus_status = f"Focus: MANUAL {manual_focus_value:.1f}"
            else:
                focus_status = "Focus: MANUAL"

            if mode == "MANUAL":
                if manual_step == "FOCUS":
                    manual_status = "Manual step: FOCUS (adjust lens with +/-)"
                else:
                    manual_status = "Manual step: POINTS (click 4 corners)"
            else:
                manual_status = "Manual step: OFF"
            keys_status = (
                "Keys: [a] auto [m] manual focus [n] points [p] focus [s] save/capture [r] reset "
                "[f] AF on/off [+/- or 1/2] focus out/in [q] quit"
            )
            put_status(
                vis,
                mode,
                confidence,
                fps,
                save_count,
                auto_status,
                focus_status,
                manual_status,
                keys_status,
            )
            scanner_view = fit_for_display(vis, cfg.max_display_width, cfg.max_display_height)
            selector.set_viewport(
                source_width=vis.shape[1],
                source_height=vis.shape[0],
                display_width=scanner_view.shape[1],
                display_height=scanner_view.shape[0],
            )
            cv2.imshow("A4 Scanner", scanner_view)
            cv2.imshow(
                "Rectified",
                fit_for_display(warped_preview, cfg.max_display_width, cfg.max_display_height),
            )

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("a"):
                mode = "AUTO"
                selector.reset()
                manual_step = "FOCUS"
            elif key == ord("m"):
                if mode != "MANUAL":
                    selector.reset()
                mode = "MANUAL"
                manual_step = "FOCUS"
                prev_quad = None
                stable_frames = 0
                print("Manual mode: FOCUS step. Adjust lens with [+/-], press [s] to capture photo, or [n] for 4-point selection.")
            elif key in (ord("n"), ord("N")):
                if mode == "MANUAL":
                    if manual_step != "POINTS":
                        selector.reset()
                    manual_step = "POINTS"
                    stable_frames = 0
                    print("Manual mode: POINTS step. Click 4 corners, then press [s] to save.")
            elif key in (ord("p"), ord("P")):
                if mode == "MANUAL":
                    manual_step = "FOCUS"
                    stable_frames = 0
                    print("Manual mode: returned to FOCUS step.")
            elif key in (ord("r"), ord("R")):
                selector.reset()
                prev_quad = None
                stable_frames = 0
                if mode == "MANUAL":
                    print("Manual points reset.")
                if capture_lock_enabled and capture_locked:
                    capture_locked = False
                    stable_frames = 0
                    print("Capture lock cleared manually. Ready for next photo.")
            elif key in (ord("s"), ord("S")):
                if mode == "MANUAL" and manual_step == "FOCUS":
                    capture_image = frame.copy()
                    persist_capture(capture_image, cfg, readability_result=None)
                    save_count += 1
                    print("Manual capture: saved current camera photo.")
                elif active_quad is None:
                    if mode == "MANUAL":
                        if manual_step != "POINTS":
                            print("Save skipped: manual mode is in FOCUS step. Press [n], set 4 corners, then save.")
                        else:
                            print("Save skipped: set 4 manual corners first.")
                    else:
                        print("Save skipped: no document detected.")
                elif mode != "MANUAL" and capture_lock_enabled and capture_locked:
                    if cfg.capture_reset_url:
                        print("Save blocked: capture is locked until reset API unlocks it.")
                    else:
                        print("Save blocked: capture is locked. Press [r] to reset.")
                else:
                    can_save, readability_result = can_save_by_readability(warped_preview, cfg)
                    if can_save:
                        persist_capture(warped_preview, cfg, readability_result=readability_result)
                        save_count += 1
                        if mode != "MANUAL" and capture_lock_enabled:
                            capture_locked = True
                            if cfg.capture_reset_url:
                                print("Capture lock raised: waiting for reset API before next photo.")
                            else:
                                print("Capture lock raised: press [r] to allow next photo.")
                    else:
                        notify_unreadable(cfg, confidence, readability_result)
            elif key == ord("f"):
                if autofocus_prop is None:
                    print("Autofocus toggle is not supported by this camera/backend.")
                else:
                    target_auto = not focus_is_auto
                    ok_af = cap.set(autofocus_prop, 1.0 if target_auto else 0.0)
                    if ok_af:
                        focus_is_auto = target_auto
                        mismatch_note = ""
                        try:
                            af_now = cap.get(autofocus_prop)
                            if af_now >= 0 and ((af_now >= 0.5) != focus_is_auto):
                                mismatch_note = " (driver reports different state)"
                        except Exception:
                            pass
                        print(f"Camera autofocus: {'ON' if focus_is_auto else 'OFF'}{mismatch_note}")
                    else:
                        print("Warning: could not change autofocus mode.")
            elif key in (ord("-"), ord("_"), ord("+"), ord("="), ord("["), ord("]"), ord("1"), ord("2")):
                if focus_prop is None:
                    print("Manual focus is not supported by this camera/backend.")
                elif mode == "MANUAL" and manual_step != "FOCUS":
                    print("Focus adjustment is available in MANUAL FOCUS step. Press [p] to return to focus.")
                else:
                    if manual_focus_value is None:
                        try:
                            f_now = cap.get(focus_prop)
                            manual_focus_value = f_now if f_now >= 0 else 0.0
                        except Exception:
                            manual_focus_value = 0.0

                    focus_in_keys = {ord("-"), ord("_"), ord("["), ord("1")}
                    delta = -focus_step if key in focus_in_keys else focus_step
                    target_focus = max(0.0, float(manual_focus_value) + delta)
                    was_auto = focus_is_auto
                    autofocus_turned_off = False
                    if was_auto and autofocus_prop is not None:
                        if cap.set(autofocus_prop, 0.0):
                            focus_is_auto = False
                            autofocus_turned_off = True
                        else:
                            print("Warning: could not disable autofocus; trying manual focus anyway.")

                    ok_focus = cap.set(focus_prop, target_focus)
                    try:
                        f_now = cap.get(focus_prop)
                        manual_focus_value = f_now if f_now >= 0 else target_focus
                    except Exception:
                        manual_focus_value = target_focus

                    if ok_focus:
                        focus_is_auto = False
                        if autofocus_turned_off:
                            print("Camera autofocus: OFF (manual focus mode).")
                        direction = "IN" if key in focus_in_keys else "OUT"
                        print(f"Manual focus {direction}: {manual_focus_value:.1f}")
                    else:
                        if autofocus_turned_off and autofocus_prop is not None:
                            if cap.set(autofocus_prop, 1.0):
                                focus_is_auto = True
                            print("Warning: manual focus is not supported; autofocus restored.")
                        else:
                            print("Warning: manual focus adjustment failed.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        elapsed = max(0.001, time.time() - t0)
        avg_fps = frame_count / elapsed
        hit_rate = (detect_hits / max(1, frame_count)) * 100.0
        avg_conf = total_conf / max(1, frame_count)
        print("\nRun Metrics")
        print(f"- Frames processed: {frame_count}")
        print(f"- Elapsed seconds: {elapsed:.2f}")
        print(f"- Average FPS: {avg_fps:.2f}")
        print(f"- Auto detect hit rate: {hit_rate:.2f}%")
        print(f"- Mean confidence: {avg_conf:.3f}")
    return 0


def main() -> int:
    args = parse_args()
    cfg = ScannerConfig()
    if args.verify_readable:
        cfg.enable_readability_check = True
    if args.upload_url:
        cfg.upload_enabled = True
        cfg.upload_url = args.upload_url
    if args.upload_token:
        cfg.upload_token = args.upload_token
    if args.autofocus:
        cfg.camera_autofocus_enabled = args.autofocus == "on"
    if args.manual_focus is not None:
        cfg.camera_autofocus_enabled = False
        cfg.camera_manual_focus = args.manual_focus
    if args.camera is not None:
        cfg.camera_index = args.camera
    if args.save_local:
        cfg.save_rectified_locally = args.save_local == "true"
    if args.capture_reset_url:
        cfg.capture_reset_url = args.capture_reset_url
        cfg.single_capture_until_api_reset = True
    if args.capture_reset_token:
        cfg.capture_reset_token = args.capture_reset_token
    if args.unreadable_notify_url:
        cfg.unreadable_notify_enabled = True
        cfg.unreadable_notify_url = args.unreadable_notify_url
    if args.unreadable_notify_token:
        cfg.unreadable_notify_token = args.unreadable_notify_token
    if args.tesseract_cmd:
        cfg.tesseract_cmd = args.tesseract_cmd
    if args.image:
        return process_single_image(args.image, cfg, show_windows=not args.no_gui)
    return run_webcam(cfg)


if __name__ == "__main__":
    raise SystemExit(main())


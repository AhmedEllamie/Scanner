from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
import queue
import threading
import time
import uuid
from typing import Any, Callable

from scanner.config import ScannerConfig

from .models import (
    JobRecord,
    JobRequest,
    ManualConfig,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    utc_now_iso,
)


CaptureExecutor = Callable[..., Any]
QuadNormalizer = Callable[[list[object]], Any]
QuadValidator = Callable[[Any, int, int, float], tuple[bool, str]]
FrameProcessor = Callable[..., Any]


class CameraManager:
    def __init__(self, cfg: ScannerConfig) -> None:
        from scanner.calibration import FisheyeUndistorter
        from scanner.camera import apply_camera_settings, open_video_capture

        self._cfg = cfg
        self._open_video_capture = open_video_capture
        self._apply_camera_settings = apply_camera_settings
        self._undistorter = FisheyeUndistorter(cfg)

        self._frame_lock = threading.Lock()
        self._latest_frame: Any = None
        self._frame_width = 0
        self._frame_height = 0
        self._frame_ts = 0.0
        self._camera_error: str | None = "camera_not_started"

        self._command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._fully_stopped = False
        self._thread = threading.Thread(target=self._run, name="scanner-camera-manager", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout_seconds: float = 3.0) -> None:
        with self._stop_lock:
            if self._fully_stopped:
                return
            self._stop_event.set()
            self._thread.join(timeout=max(0.1, timeout_seconds))
            self._fully_stopped = True

    def enqueue_focus_mode(self, *, autofocus_enabled: bool, manual_focus_value: float | None) -> None:
        self._command_queue.put(
            {
                "kind": "focus_mode",
                "autofocus_enabled": bool(autofocus_enabled),
                "manual_focus_value": None if manual_focus_value is None else float(manual_focus_value),
            }
        )

    def enqueue_focus_adjust(self, *, delta: float) -> None:
        self._command_queue.put({"kind": "focus_adjust", "delta": float(delta)})

    def get_snapshot(self) -> tuple[Any, int, int, float]:
        with self._frame_lock:
            if self._latest_frame is None:
                return None, self._frame_width, self._frame_height, self._frame_ts
            return self._latest_frame.copy(), self._frame_width, self._frame_height, self._frame_ts

    def get_status(self) -> dict[str, Any]:
        with self._frame_lock:
            return {
                "frame_width": self._frame_width,
                "frame_height": self._frame_height,
                "frame_ts": self._frame_ts,
                "camera_error": self._camera_error,
            }

    def _run(self) -> None:
        import cv2

        backoff = 0.2
        cap = None
        focus_is_auto = self._cfg.camera_autofocus_enabled
        manual_focus_value = self._cfg.camera_manual_focus if self._cfg.camera_manual_focus >= 0 else None

        while not self._stop_event.is_set():
            try:
                if cap is None or not cap.isOpened():
                    cap = self._open_video_capture(self._cfg)
                    if cap is None or not cap.isOpened():
                        self._set_camera_error("camera_unavailable")
                        if self._stop_event.wait(timeout=backoff):
                            break
                        backoff = min(2.0, backoff * 1.5)
                        continue
                    self._apply_camera_settings(cap, self._cfg)
                    self._set_camera_error(None)
                    backoff = 0.2

                self._drain_commands(cap, manual_focus_value)
                focus_is_auto, manual_focus_value = self._focus_state_from_camera(cap, focus_is_auto, manual_focus_value)

                ok, frame = cap.read()
                if not ok or frame is None or frame.size == 0:
                    self._set_camera_error("camera_read_failed")
                    cap.release()
                    cap = None
                    continue

                frame = self._undistorter.apply(frame)
                h, w = frame.shape[:2]
                with self._frame_lock:
                    self._latest_frame = frame
                    self._frame_width = int(w)
                    self._frame_height = int(h)
                    self._frame_ts = time.time()
                    self._camera_error = None
            except Exception as exc:
                self._set_camera_error(f"camera_manager_error: {exc}")
                if cap is not None and cap.isOpened():
                    cap.release()
                cap = None
                if self._stop_event.wait(timeout=0.3):
                    break

        if cap is not None and cap.isOpened():
            cap.release()

    def _drain_commands(self, cap: Any, manual_focus_value: float | None) -> None:
        import cv2

        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        while True:
            try:
                cmd = self._command_queue.get_nowait()
            except queue.Empty:
                break
            kind = cmd.get("kind")
            if kind == "focus_mode":
                target_auto = bool(cmd.get("autofocus_enabled"))
                target_manual = cmd.get("manual_focus_value", manual_focus_value)
                if autofocus_prop is not None:
                    try:
                        cap.set(autofocus_prop, 1.0 if target_auto else 0.0)
                    except Exception:
                        pass
                if not target_auto and target_manual is not None and focus_prop is not None:
                    try:
                        cap.set(focus_prop, float(target_manual))
                    except Exception:
                        pass
            elif kind == "focus_adjust":
                delta = float(cmd.get("delta", 0.0))
                if autofocus_prop is not None:
                    try:
                        cap.set(autofocus_prop, 0.0)
                    except Exception:
                        pass
                if focus_prop is not None:
                    current = manual_focus_value if manual_focus_value is not None else 0.0
                    target = max(0.0, float(current) + delta)
                    try:
                        cap.set(focus_prop, target)
                    except Exception:
                        pass

    def _focus_state_from_camera(self, cap: Any, focus_is_auto: bool, manual_focus_value: float | None) -> tuple[bool, float | None]:
        import cv2

        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        if autofocus_prop is not None:
            try:
                af = cap.get(autofocus_prop)
                if af >= 0:
                    focus_is_auto = af >= 0.5
            except Exception:
                pass
        if focus_prop is not None:
            try:
                f = cap.get(focus_prop)
                if f >= 0:
                    manual_focus_value = float(f)
            except Exception:
                pass
        return focus_is_auto, manual_focus_value

    def _set_camera_error(self, message: str | None) -> None:
        with self._frame_lock:
            self._camera_error = message


class ScannerJobWorker:
    def __init__(
        self,
        cfg: ScannerConfig,
        *,
        capture_executor: CaptureExecutor | None = None,
        quad_normalizer: QuadNormalizer | None = None,
        quad_validator: QuadValidator | None = None,
        frame_processor: FrameProcessor | None = None,
        camera_manager: CameraManager | None = None,
    ) -> None:
        self._cfg = cfg
        if capture_executor is None or quad_normalizer is None or quad_validator is None or frame_processor is None:
            from scanner.capture import (
                normalize_quad_points,
                process_rectified_manual_frame,
                validate_quad_within_frame,
            )

            self._capture_executor = capture_executor
            self._frame_processor = frame_processor or process_rectified_manual_frame
            self._quad_normalizer = quad_normalizer or normalize_quad_points
            self._quad_validator = quad_validator or validate_quad_within_frame
        else:
            self._capture_executor = capture_executor
            self._frame_processor = frame_processor
            self._quad_normalizer = quad_normalizer
            self._quad_validator = quad_validator

        self._jobs: dict[str, JobRecord] = {}
        self._jobs_lock = threading.Lock()
        self._manual_lock = threading.Lock()
        self._manual_config = ManualConfig()
        self._camera = camera_manager or CameraManager(cfg)

        self._queue: queue.Queue[JobRequest] = queue.Queue()
        self._stop_event = threading.Event()
        self._stop_lock = threading.Lock()
        self._fully_stopped = False
        self._thread = threading.Thread(target=self._run, name="scanner-capture-worker", daemon=True)

    def start(self) -> None:
        self._camera.start()
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout_seconds: float = 3.0) -> None:
        with self._stop_lock:
            if self._fully_stopped:
                return
            self._stop_event.set()
            self._thread.join(timeout=max(0.1, timeout_seconds))
            self._camera.stop(timeout_seconds=timeout_seconds)
            self._fully_stopped = True

    def get_camera_status(self) -> dict[str, Any]:
        return self._camera.get_status()

    def get_latest_frame_snapshot(self) -> tuple[Any, int, int, float]:
        return self._camera.get_snapshot()

    def get_manual_config(self) -> dict[str, Any]:
        with self._manual_lock:
            return self._manual_config.to_dict()

    def set_manual_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")

        with self._manual_lock:
            current = replace(self._manual_config)
            current.quad_points = [list(p) for p in self._manual_config.quad_points]

        autofocus_enabled = bool(payload.get("autofocus_enabled", current.autofocus_enabled))
        if "manual_focus_value" in payload:
            raw_focus = payload.get("manual_focus_value")
            manual_focus_value = None if raw_focus is None else float(raw_focus)
        else:
            manual_focus_value = current.manual_focus_value

        raw_points = payload.get("quad_points", current.quad_points)
        if not raw_points:
            raise ValueError("quad_points is required and must contain 4 points")

        self._camera.enqueue_focus_mode(
            autofocus_enabled=autofocus_enabled,
            manual_focus_value=manual_focus_value,
        )

        quad = self._quad_normalizer(raw_points)
        frame_width, frame_height = self._current_frame_size()
        valid, reason = self._quad_validator(quad, frame_width, frame_height, self._cfg.min_edge_px)
        if not valid:
            raise ValueError(reason)

        updated = ManualConfig(
            autofocus_enabled=autofocus_enabled,
            manual_focus_value=manual_focus_value,
            quad_points=self._quad_to_list(quad),
            frame_width=frame_width,
            frame_height=frame_height,
            valid=True,
            validation_message="ok",
            updated_at=utc_now_iso(),
        )
        with self._manual_lock:
            self._manual_config = updated
            return self._manual_config.to_dict()

    def set_focus_mode(self, *, autofocus_enabled: bool, manual_focus_value: float | None = None) -> dict[str, Any]:
        with self._manual_lock:
            current = replace(self._manual_config)
            current.quad_points = [list(p) for p in self._manual_config.quad_points]

        new_manual_focus = current.manual_focus_value if manual_focus_value is None else float(manual_focus_value)
        self._camera.enqueue_focus_mode(
            autofocus_enabled=bool(autofocus_enabled),
            manual_focus_value=new_manual_focus,
        )
        frame_width, frame_height = self._current_frame_size()

        valid = bool(current.quad_points)
        validation_message = "quad_points are not set yet"
        if current.quad_points:
            quad = self._quad_normalizer(current.quad_points)
            q_ok, q_reason = self._quad_validator(quad, frame_width, frame_height, self._cfg.min_edge_px)
            valid = bool(q_ok)
            validation_message = "ok" if q_ok else q_reason
            current.quad_points = self._quad_to_list(quad)

        updated = ManualConfig(
            autofocus_enabled=bool(autofocus_enabled),
            manual_focus_value=new_manual_focus,
            quad_points=[list(p) for p in current.quad_points],
            frame_width=int(frame_width),
            frame_height=int(frame_height),
            valid=valid,
            validation_message=validation_message,
            updated_at=utc_now_iso(),
        )
        with self._manual_lock:
            self._manual_config = updated
            return self._manual_config.to_dict()

    def adjust_focus(self, *, direction: str, step: float | None = None) -> dict[str, Any]:
        d = str(direction or "").strip().lower()
        if d not in {"+", "-", "in", "out", "near", "far"}:
            raise ValueError("direction must be one of: '+', '-', 'in', 'out', 'near', 'far'")
        s = float(step) if step is not None else float(self._cfg.camera_focus_step)
        if s <= 0:
            raise ValueError("step must be > 0")

        with self._manual_lock:
            current = replace(self._manual_config)
            current.quad_points = [list(p) for p in self._manual_config.quad_points]

        base = current.manual_focus_value if current.manual_focus_value is not None else 0.0
        delta = -s if d in {"-", "in", "near"} else s
        new_focus = max(0.0, float(base) + delta)
        self._camera.enqueue_focus_adjust(delta=delta)
        return self.set_focus_mode(autofocus_enabled=False, manual_focus_value=new_focus)

    def set_quad_points(self, *, quad_points: list[object]) -> dict[str, Any]:
        with self._manual_lock:
            current = replace(self._manual_config)
            current.quad_points = [list(p) for p in self._manual_config.quad_points]

        quad = self._quad_normalizer(quad_points)
        frame_width, frame_height = self._current_frame_size()
        valid, reason = self._quad_validator(quad, frame_width, frame_height, self._cfg.min_edge_px)
        if not valid:
            raise ValueError(reason)

        updated = ManualConfig(
            autofocus_enabled=current.autofocus_enabled,
            manual_focus_value=current.manual_focus_value,
            quad_points=self._quad_to_list(quad),
            frame_width=int(frame_width),
            frame_height=int(frame_height),
            valid=True,
            validation_message="ok",
            updated_at=utc_now_iso(),
        )
        with self._manual_lock:
            self._manual_config = updated
            return self._manual_config.to_dict()

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            payload = {}

        mode = str(payload.get("mode", "manual")).strip().lower()
        if mode != "manual":
            raise ValueError("Only mode='manual' is supported")

        readability_required_raw = payload.get("readability_required", None)
        readability_required = None
        if readability_required_raw is not None:
            readability_required = bool(readability_required_raw)

        timeout_seconds_raw = payload.get("timeout_seconds", self._cfg.upload_timeout_seconds)
        timeout_seconds = float(timeout_seconds_raw)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self._validate_manual_config_against_camera()

        job_id = str(uuid.uuid4())
        req = JobRequest(
            job_id=job_id,
            mode=mode,
            readability_required=readability_required,
            timeout_seconds=timeout_seconds,
        )
        rec = JobRecord(job_id=job_id, mode=mode, status=STATUS_QUEUED, created_at=req.created_at)
        with self._jobs_lock:
            self._jobs[job_id] = rec
        self._queue.put(req)
        return rec.to_public_dict()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._jobs_lock:
            rec = self._jobs.get(job_id)
            return None if rec is None else rec.to_public_dict()

    def get_job_image(self, job_id: str) -> tuple[str, bytes | None]:
        with self._jobs_lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return "missing", None
            if rec.status != STATUS_SUCCEEDED or rec.image_bytes is None:
                return rec.status, None
            return rec.status, rec.image_bytes

    def _validate_manual_config_against_camera(self) -> None:
        with self._manual_lock:
            cfg = replace(self._manual_config)
            cfg.quad_points = [list(p) for p in self._manual_config.quad_points]

        if not cfg.quad_points:
            raise ValueError("Manual config is not set; call POST /session/manual-config first")

        quad = self._quad_normalizer(cfg.quad_points)
        frame_width, frame_height = self._current_frame_size()
        valid, reason = self._quad_validator(quad, frame_width, frame_height, self._cfg.min_edge_px)
        if not valid:
            raise ValueError(reason)

        with self._manual_lock:
            self._manual_config.frame_width = int(frame_width)
            self._manual_config.frame_height = int(frame_height)
            self._manual_config.valid = True
            self._manual_config.validation_message = "ok"
            self._manual_config.updated_at = utc_now_iso()
            self._manual_config.quad_points = self._quad_to_list(quad)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                req = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            self._mark_running(req.job_id)
            try:
                with self._manual_lock:
                    cfg = replace(self._manual_config)
                    cfg.quad_points = [list(p) for p in self._manual_config.quad_points]
                if not cfg.quad_points:
                    raise RuntimeError("Manual config is missing")

                if self._capture_executor is not None:
                    result = self._capture_executor(
                        self._cfg,
                        cfg.quad_points,
                        autofocus_enabled=cfg.autofocus_enabled,
                        manual_focus_value=cfg.manual_focus_value,
                        readability_required=req.readability_required,
                        timeout_seconds=req.timeout_seconds,
                    )
                else:
                    result = self._process_job_from_snapshot(req, cfg)

                if result.ok and result.png_bytes is not None:
                    self._mark_succeeded(req.job_id, result)
                else:
                    self._mark_failed(
                        req.job_id,
                        error=result.status,
                        detail=result.message,
                        metadata=self._result_metadata(result),
                    )
            except Exception as exc:
                self._mark_failed(req.job_id, error="worker_error", detail=str(exc), metadata={})
            finally:
                self._queue.task_done()

    def _process_job_from_snapshot(self, req: JobRequest, cfg: ManualConfig) -> Any:
        frame, width, height, frame_ts = self._camera.get_snapshot()
        if frame is None:
            raise RuntimeError("No camera frame available")
        if (time.time() - frame_ts) > 2.5:
            raise RuntimeError("Camera frame is stale")
        debug_input_path = self._save_debug_snapshot_if_enabled(
            frame=frame,
            quad_points=cfg.quad_points,
            job_id=req.job_id,
        )
        result = self._frame_processor(
            frame,
            self._cfg,
            cfg.quad_points,
            readability_required=req.readability_required,
            timeout_seconds=req.timeout_seconds,
        )
        result.frame_width = width
        result.frame_height = height
        if debug_input_path:
            setattr(result, "debug_input_path", debug_input_path)
        return result

    def _mark_running(self, job_id: str) -> None:
        with self._jobs_lock:
            rec = self._jobs[job_id]
            rec.status = STATUS_RUNNING
            rec.started_at = utc_now_iso()
            rec.error = None
            rec.detail = None

    def _mark_succeeded(self, job_id: str, result: Any) -> None:
        metadata = self._result_metadata(result)
        saved_path = self._save_result_locally_if_enabled(result.png_bytes)
        if saved_path:
            metadata["saved_path"] = saved_path

        with self._jobs_lock:
            rec = self._jobs[job_id]
            rec.status = STATUS_SUCCEEDED
            rec.finished_at = utc_now_iso()
            rec.image_bytes = result.png_bytes
            rec.error = None
            rec.detail = result.message
            rec.metadata = metadata

    def _mark_failed(self, job_id: str, *, error: str, detail: str, metadata: dict[str, Any]) -> None:
        with self._jobs_lock:
            rec = self._jobs[job_id]
            rec.status = STATUS_FAILED
            rec.finished_at = utc_now_iso()
            rec.image_bytes = None
            rec.error = error
            rec.detail = detail
            rec.metadata = dict(metadata)

    @staticmethod
    def _result_metadata(result: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "frame_width": result.frame_width,
            "frame_height": result.frame_height,
            "elapsed_ms": result.elapsed_ms,
        }
        debug_input_path = getattr(result, "debug_input_path", "")
        if isinstance(debug_input_path, str) and debug_input_path:
            metadata["debug_input_path"] = debug_input_path
        if result.readability is not None:
            metadata["readability"] = {
                "readable": result.readability.readable,
                "mean_confidence": result.readability.mean_confidence,
                "token_count": result.readability.token_count,
                "message": result.readability.message,
            }
        return metadata

    def _save_result_locally_if_enabled(self, png_bytes: bytes | None) -> str | None:
        if not self._cfg.save_rectified_locally:
            return None
        if not png_bytes:
            return None
        os.makedirs(self._cfg.save_dir, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png"
        path = os.path.join(self._cfg.save_dir, name)
        with open(path, "wb") as f:
            f.write(png_bytes)
        return path

    def _save_debug_snapshot_if_enabled(
        self,
        *,
        frame: Any,
        quad_points: list[list[float]],
        job_id: str,
    ) -> str | None:
        if not self._cfg.save_debug_capture_with_quad:
            return None
        if frame is None:
            return None
        try:
            import cv2
            import numpy as np

            os.makedirs(self._cfg.debug_capture_dir, exist_ok=True)
            overlay = frame.copy()
            pts = np.array(quad_points, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(overlay, [pts], isClosed=True, color=(0, 255, 255), thickness=3, lineType=cv2.LINE_AA)
            for idx, point in enumerate(quad_points, start=1):
                x, y = int(point[0]), int(point[1])
                cv2.circle(overlay, (x, y), 7, (0, 0, 255), -1, lineType=cv2.LINE_AA)
                cv2.putText(
                    overlay,
                    str(idx),
                    (x + 10, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (255, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
            name = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{job_id}_input_quad.png"
            path = os.path.join(self._cfg.debug_capture_dir, name)
            cv2.imwrite(path, overlay)
            return path
        except Exception:
            return None

    @staticmethod
    def _quad_to_list(quad: Any) -> list[list[float]]:
        raw = quad.tolist() if hasattr(quad, "tolist") else quad
        if not isinstance(raw, list) or len(raw) != 4:
            raise ValueError("Quad must contain exactly 4 points")
        out: list[list[float]] = []
        for point in raw:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Each quad point must contain x and y values")
            out.append([float(point[0]), float(point[1])])
        return out

    def _current_frame_size(self) -> tuple[int, int]:
        status = self._camera.get_status()
        w = int(status.get("frame_width") or 0)
        h = int(status.get("frame_height") or 0)
        if w <= 0 or h <= 0:
            err = status.get("camera_error") or "camera_unavailable"
            raise RuntimeError(f"Camera frame size unavailable: {err}")
        return w, h


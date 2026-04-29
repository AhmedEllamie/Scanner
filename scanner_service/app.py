from __future__ import annotations

import argparse
import atexit
import os
import signal
import socket
import threading
from datetime import datetime
from typing import Any, Callable, cast

import cv2
from flask import Flask, Response, jsonify, request
from werkzeug.serving import BaseWSGIServer, make_server

from scanner.config import ScannerConfig

from .models import STATUS_FAILED, STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCEEDED
from .worker import ScannerJobWorker


def _extract_auth_token() -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-Scanner-Token") or "").strip()


def create_app(
    cfg: ScannerConfig | None = None,
    *,
    worker: ScannerJobWorker | None = None,
    service_token: str | None = None,
    shutdown_event: threading.Event | None = None,
) -> Flask:
    cfg = cfg or ScannerConfig()
    worker = worker or ScannerJobWorker(cfg)
    token = service_token if service_token is not None else os.getenv("SCANNER_SERVICE_TOKEN", "")
    shutdown_evt = shutdown_event if shutdown_event is not None else threading.Event()

    app = Flask(__name__)
    app.config["SCANNER_WORKER"] = worker
    app.config["SCANNER_SERVICE_TOKEN"] = token
    app.config["SCANNER_SHUTDOWN_EVENT"] = shutdown_evt

    _stop_lock = threading.Lock()
    _worker_stopped = False

    def stop_worker_once() -> None:
        nonlocal _worker_stopped
        with _stop_lock:
            if _worker_stopped:
                return
            _worker_stopped = True
        shutdown_evt.set()
        try:
            worker.stop()
        except Exception:
            pass

    app.config["SCANNER_STOP_WORKER"] = stop_worker_once

    worker.start()
    atexit.register(stop_worker_once)

    @app.before_request
    def _require_auth() -> Response | None:
        if request.path == "/health":
            return None
        expected = str(app.config.get("SCANNER_SERVICE_TOKEN") or "")
        if not expected:
            return None
        if _extract_auth_token() != expected:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return None

    @app.after_request
    def _access_log(response: Response) -> Response:
        remote_addr = request.remote_addr or "-"
        timestamp = datetime.now().strftime("%d/%b/%Y %H:%M:%S")
        # request.full_path adds a trailing '?' when there is no query string.
        path = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        protocol = request.environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        print(f'{remote_addr} - - [{timestamp}] "{request.method} {path} {protocol}" {response.status_code} -')
        return response

    @app.get("/health")
    def health() -> Response:
        camera = worker.get_camera_status()
        return jsonify({"ok": True, "status": "ready", "camera": camera})

    @app.get("/session/manual-config")
    def get_manual_config() -> Response:
        state = worker.get_manual_config()
        return jsonify({"ok": True, "manual_config": state})

    @app.post("/session/manual-config")
    def set_manual_config() -> Response:
        payload = request.get_json(silent=True) or {}
        try:
            state = worker.set_manual_config(payload)
            return jsonify({"ok": True, "manual_config": state})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @app.post("/session/focus-mode")
    def set_focus_mode() -> Response:
        payload = request.get_json(silent=True) or {}
        if "autofocus_enabled" not in payload:
            return jsonify({"ok": False, "error": "autofocus_enabled is required"}), 400
        try:
            state = worker.set_focus_mode(
                autofocus_enabled=bool(payload.get("autofocus_enabled")),
                manual_focus_value=payload.get("manual_focus_value"),
            )
            return jsonify({"ok": True, "manual_config": state})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @app.post("/session/focus-adjust")
    def adjust_focus() -> Response:
        payload = request.get_json(silent=True) or {}
        direction = payload.get("direction", "")
        try:
            state = worker.adjust_focus(
                direction=str(direction),
                step=payload.get("step"),
            )
            return jsonify({"ok": True, "manual_config": state})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @app.post("/session/quad-points")
    def set_quad_points() -> Response:
        payload = request.get_json(silent=True) or {}
        raw_points = payload.get("quad_points")
        if raw_points is None:
            return jsonify({"ok": False, "error": "quad_points is required"}), 400
        try:
            state = worker.set_quad_points(quad_points=raw_points)
            return jsonify({"ok": True, "manual_config": state})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @app.post("/jobs")
    def create_job() -> Response:
        payload = request.get_json(silent=True) or {}
        try:
            rec = worker.create_job(payload)
            return jsonify({"ok": True, "job": rec}), 202
        except ValueError as exc:
            msg = str(exc)
            status_code = 409 if "Manual config is not set" in msg else 400
            return jsonify({"ok": False, "error": msg}), status_code
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @app.post("/capture/start")
    def capture_start() -> Response:
        payload = request.get_json(silent=True) or {}
        payload.setdefault("mode", "manual")
        try:
            rec = worker.create_job(payload)
            return jsonify({"ok": True, "capture": rec}), 202
        except ValueError as exc:
            msg = str(exc)
            status_code = 409 if "Manual config is not set" in msg else 400
            return jsonify({"ok": False, "error": msg}), status_code
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @app.get("/capture/<capture_id>/status")
    def capture_status(capture_id: str) -> Response:
        rec = worker.get_job(capture_id)
        if rec is None:
            return jsonify({"ok": False, "error": "capture_not_found"}), 404
        return jsonify({"ok": True, "capture": rec})

    @app.get("/capture/<capture_id>/result")
    def capture_result(capture_id: str) -> Response:
        status, img = worker.get_job_image(capture_id)
        if status == "missing":
            return jsonify({"ok": False, "error": "capture_not_found"}), 404
        if status in (STATUS_QUEUED, STATUS_RUNNING):
            return jsonify({"ok": False, "error": "capture_not_ready", "status": status}), 409
        if status == STATUS_FAILED:
            rec = worker.get_job(capture_id) or {}
            payload: dict[str, Any] = {"ok": False, "error": "capture_failed", "status": status}
            if rec:
                payload["capture"] = rec
            return jsonify(payload), 409
        if status == STATUS_SUCCEEDED and img is not None:
            return Response(img, mimetype="image/png")
        return jsonify({"ok": False, "error": "capture_result_missing", "status": status}), 500

    @app.get("/jobs/<job_id>")
    def get_job(job_id: str) -> Response:
        rec = worker.get_job(job_id)
        if rec is None:
            return jsonify({"ok": False, "error": "job_not_found"}), 404
        return jsonify({"ok": True, "job": rec})

    @app.get("/jobs/<job_id>/image")
    def get_job_image(job_id: str) -> Response:
        status, img = worker.get_job_image(job_id)
        if status == "missing":
            return jsonify({"ok": False, "error": "job_not_found"}), 404
        if status in (STATUS_QUEUED, STATUS_RUNNING):
            return jsonify({"ok": False, "error": "job_not_ready", "status": status}), 409
        if status == STATUS_FAILED:
            rec = worker.get_job(job_id) or {}
            payload: dict[str, Any] = {"ok": False, "error": "job_failed", "status": status}
            if rec:
                payload["job"] = rec
            return jsonify(payload), 409
        if status == STATUS_SUCCEEDED and img is not None:
            return Response(img, mimetype="image/png")
        return jsonify({"ok": False, "error": "job_image_missing", "status": status}), 500

    @app.get("/stream.mjpg")
    def stream_mjpeg() -> Response:
        fps_raw = request.args.get("fps", "10")
        width_raw = request.args.get("width", "0")
        fisheye_raw = request.args.get("fisheye", "1")
        shutdown = cast(threading.Event, app.config["SCANNER_SHUTDOWN_EVENT"])
        try:
            fps = max(1.0, min(25.0, float(fps_raw)))
        except Exception:
            fps = 10.0
        try:
            target_width = max(0, int(width_raw))
        except Exception:
            target_width = 0
        _fisheye_enabled = str(fisheye_raw).strip().lower() not in {"0", "false", "no", "off"}
        frame_delay = 1.0 / fps
        idle_sleep = min(0.08, max(0.02, frame_delay / 4))

        def _generate() -> Any:
            try:
                while not shutdown.is_set():
                    frame, _, _, _ = worker.get_latest_frame_snapshot()
                    if frame is None:
                        if shutdown.wait(timeout=idle_sleep):
                            break
                        continue
                    if target_width > 0 and frame.shape[1] > target_width:
                        h = int(frame.shape[0] * (target_width / float(frame.shape[1])))
                        frame = cv2.resize(frame, (target_width, max(1, h)), interpolation=cv2.INTER_AREA)
                    ok_jpg, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    if not ok_jpg or buf is None:
                        if shutdown.wait(timeout=0.02):
                            break
                        continue
                    payload = buf.tobytes()
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Cache-Control: no-store\r\n\r\n" + payload + b"\r\n"
                    )
                    if shutdown.wait(timeout=frame_delay):
                        break
            except GeneratorExit:
                return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        return Response(
            _generate(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    return app


def _install_shutdown_signals(shutdown_event: threading.Event) -> None:
    """Register SIGINT/SIGTERM to stop the server loop (best-effort on all platforms)."""

    def _handler(_signum: int, _frame: object | None) -> None:
        shutdown_event.set()

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def _run_werkzeug_server(app: Flask, host: str, port: int, shutdown_event: threading.Event) -> None:
    """Serve Flask with a short socket timeout so shutdown_event is polled regularly."""
    server: BaseWSGIServer = make_server(host, int(port), app, threaded=True)
    server.socket.settimeout(0.5)
    try:
        while not shutdown_event.is_set():
            try:
                server.handle_request()
            except socket.timeout:
                continue
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Scanner HTTP service launcher.", allow_abbrev=False)
    parser.add_argument("--host", default=None, help="Bind host. Overrides SCANNER_SERVICE_HOST env var.")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port. Overrides SCANNER_SERVICE_PORT env var.",
    )
    args = parser.parse_args()

    cfg = ScannerConfig()
    host = args.host if args.host is not None else os.getenv("SCANNER_SERVICE_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.getenv("SCANNER_SERVICE_PORT", "8008"))

    shutdown_event = threading.Event()
    app = create_app(cfg, shutdown_event=shutdown_event)
    stop_worker = cast(Callable[[], None], app.config["SCANNER_STOP_WORKER"])
    _install_shutdown_signals(shutdown_event)

    try:
        _run_werkzeug_server(app, host, port, shutdown_event)
    except KeyboardInterrupt:
        shutdown_event.set()
    finally:
        stop_worker()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


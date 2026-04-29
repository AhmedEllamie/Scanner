from __future__ import annotations

from dataclasses import dataclass
import sys
import threading
import time
import types
import unittest
import queue

# Provide a lightweight cv2 stub so package imports work in test environments
# where OpenCV is unavailable. Service tests inject fake capture providers.
if "cv2" not in sys.modules:
    sys.modules["cv2"] = types.SimpleNamespace(
        IMWRITE_JPEG_QUALITY=1,
        INTER_AREA=1,
        imencode=lambda _ext, _frame, _params=None: (True, types.SimpleNamespace(tobytes=lambda: b"jpg")),
        resize=lambda frame, _size, interpolation=None: frame,
    )

from scanner.config import ScannerConfig
from scanner_service.app import create_app
from scanner_service.worker import ScannerJobWorker


def _json_headers(token: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class ScannerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ScannerConfig()
        self.capture_call_count = 0
        self.max_parallel_captures = 0
        self.current_parallel_captures = 0
        self.parallel_lock = threading.Lock()

        @dataclass
        class FakeCaptureResult:
            ok: bool
            status: str
            message: str
            png_bytes: bytes | None
            frame_width: int
            frame_height: int
            readability: object | None
            elapsed_ms: int

        self.FakeCaptureResult = FakeCaptureResult
        self.focus_commands: queue.Queue[dict] = queue.Queue()

        class FakeCameraManager:
            def __init__(inner_self) -> None:
                inner_self.frame = types.SimpleNamespace(shape=(1080, 1920, 3))
                inner_self.status = {
                    "frame_width": 1920,
                    "frame_height": 1080,
                    "frame_ts": time.time(),
                    "camera_error": None,
                }
                inner_self.started = False

            def start(inner_self) -> None:
                inner_self.started = True

            def stop(inner_self, timeout_seconds: float = 3.0) -> None:
                _ = timeout_seconds
                inner_self.started = False

            def enqueue_focus_mode(inner_self, *, autofocus_enabled: bool, manual_focus_value: float | None) -> None:
                self.focus_commands.put(
                    {
                        "kind": "focus_mode",
                        "autofocus_enabled": autofocus_enabled,
                        "manual_focus_value": manual_focus_value,
                    }
                )

            def enqueue_focus_adjust(inner_self, *, delta: float) -> None:
                self.focus_commands.put({"kind": "focus_adjust", "delta": delta})

            def get_snapshot(inner_self):
                return inner_self.frame, 1920, 1080, time.time()

            def get_status(inner_self):
                inner_self.status["frame_ts"] = time.time()
                return dict(inner_self.status)

        self.fake_camera_manager = FakeCameraManager()

        def fake_capture_executor(
            _cfg: ScannerConfig,
            _quad_points: list[list[float]],
            *,
            autofocus_enabled: bool,
            manual_focus_value: float | None,
            readability_required: bool | None = None,
            timeout_seconds: float = 10.0,
        ) -> FakeCaptureResult:
            _ = autofocus_enabled, manual_focus_value, readability_required, timeout_seconds
            with self.parallel_lock:
                self.current_parallel_captures += 1
                self.max_parallel_captures = max(self.max_parallel_captures, self.current_parallel_captures)
            try:
                self.capture_call_count += 1
                time.sleep(0.05)
                return FakeCaptureResult(
                    ok=True,
                    status="succeeded",
                    message="ok",
                    png_bytes=b"\x89PNG\r\n\x1a\nfake",
                    frame_width=1920,
                    frame_height=1080,
                    readability=None,
                    elapsed_ms=50,
                )
            finally:
                with self.parallel_lock:
                    self.current_parallel_captures -= 1

        self.worker = ScannerJobWorker(
            self.cfg,
            capture_executor=fake_capture_executor,
            quad_normalizer=lambda points: points,
            quad_validator=lambda _quad, _w, _h, _min_edge: (True, "ok"),
            camera_manager=self.fake_camera_manager,
        )
        self.app = create_app(self.cfg, worker=self.worker, service_token="secret-token")
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.worker.stop()

    def _wait_for_job_terminal(self, job_id: str, timeout_seconds: float = 4.0) -> dict:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            resp = self.client.get(f"/jobs/{job_id}", headers=_json_headers("secret-token"))
            payload = resp.get_json()
            if payload and payload.get("ok") and payload["job"]["status"] in {"succeeded", "failed"}:
                return payload["job"]
            time.sleep(0.04)
        raise AssertionError("Timed out waiting for terminal job status")

    def test_manual_config_then_capture_job_returns_rectified_png(self) -> None:
        set_resp = self.client.post(
            "/session/manual-config",
            json={
                "autofocus_enabled": False,
                "manual_focus_value": 35,
                "quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]],
            },
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(set_resp.status_code, 200)
        set_payload = set_resp.get_json()
        self.assertTrue(set_payload["ok"])
        self.assertTrue(set_payload["manual_config"]["valid"])

        create_resp = self.client.post(
            "/jobs",
            json={"mode": "manual"},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(create_resp.status_code, 202)
        job_id = create_resp.get_json()["job"]["job_id"]

        job = self._wait_for_job_terminal(job_id)
        self.assertEqual(job["status"], "succeeded")

        img_resp = self.client.get(f"/jobs/{job_id}/image", headers=_json_headers("secret-token"))
        self.assertEqual(img_resp.status_code, 200)
        self.assertEqual(img_resp.mimetype, "image/png")
        self.assertGreater(len(img_resp.data), 8)

    def test_job_rejected_when_manual_config_missing(self) -> None:
        create_resp = self.client.post(
            "/jobs",
            json={"mode": "manual"},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(create_resp.status_code, 409)
        payload = create_resp.get_json()
        self.assertFalse(payload["ok"])

    def test_invalid_quad_is_rejected(self) -> None:
        self.worker.stop()

        def strict_quad_validator(quad: object, _w: int, _h: int, _min_edge: float) -> tuple[bool, str]:
            if not isinstance(quad, list) or len(quad) != 4:
                return False, "quad must have 4 points"
            seen = {tuple(p) for p in quad}
            if len(seen) < 4:
                return False, "duplicate points are not allowed"
            return True, "ok"

        self.worker = ScannerJobWorker(
            self.cfg,
            capture_executor=lambda *_args, **_kwargs: self.FakeCaptureResult(
                ok=True,
                status="succeeded",
                message="ok",
                png_bytes=b"ok",
                frame_width=10,
                frame_height=10,
                readability=None,
                elapsed_ms=1,
            ),
            quad_normalizer=lambda points: points,
            quad_validator=strict_quad_validator,
            camera_manager=self.fake_camera_manager,
        )
        self.app = create_app(self.cfg, worker=self.worker, service_token="secret-token")
        self.client = self.app.test_client()

        set_resp = self.client.post(
            "/session/manual-config",
            json={
                "autofocus_enabled": False,
                "manual_focus_value": 10,
                "quad_points": [[100, 120], [100, 120], [101, 121], [102, 122]],
            },
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(set_resp.status_code, 400)
        payload = set_resp.get_json()
        self.assertFalse(payload["ok"])

    def test_failed_capture_status_propagates(self) -> None:
        def failing_capture_executor(
            _cfg: ScannerConfig,
            _quad_points: list[list[float]],
            *,
            autofocus_enabled: bool,
            manual_focus_value: float | None,
            readability_required: bool | None = None,
            timeout_seconds: float = 10.0,
        ) -> object:
            _ = autofocus_enabled, manual_focus_value, readability_required, timeout_seconds
            return self.FakeCaptureResult(
                ok=False,
                status="camera_unavailable",
                message="camera offline",
                png_bytes=None,
                frame_width=0,
                frame_height=0,
                readability=None,
                elapsed_ms=1,
            )

        self.worker.stop()
        self.worker = ScannerJobWorker(
            self.cfg,
            capture_executor=failing_capture_executor,
            quad_normalizer=lambda points: points,
            quad_validator=lambda _quad, _w, _h, _min_edge: (True, "ok"),
            camera_manager=self.fake_camera_manager,
        )
        self.app = create_app(self.cfg, worker=self.worker, service_token="secret-token")
        self.client = self.app.test_client()

        self.client.post(
            "/session/manual-config",
            json={
                "autofocus_enabled": False,
                "manual_focus_value": 0,
                "quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]],
            },
            headers=_json_headers("secret-token"),
        )
        create_resp = self.client.post(
            "/jobs",
            json={"mode": "manual"},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(create_resp.status_code, 202)
        job_id = create_resp.get_json()["job"]["job_id"]
        job = self._wait_for_job_terminal(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "camera_unavailable")

        img_resp = self.client.get(f"/jobs/{job_id}/image", headers=_json_headers("secret-token"))
        self.assertEqual(img_resp.status_code, 409)

    def test_two_jobs_are_serialized(self) -> None:
        self.client.post(
            "/session/manual-config",
            json={
                "autofocus_enabled": False,
                "manual_focus_value": 22,
                "quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]],
            },
            headers=_json_headers("secret-token"),
        )
        j1 = self.client.post("/jobs", json={"mode": "manual"}, headers=_json_headers("secret-token")).get_json()
        j2 = self.client.post("/jobs", json={"mode": "manual"}, headers=_json_headers("secret-token")).get_json()
        id1 = j1["job"]["job_id"]
        id2 = j2["job"]["job_id"]

        s1 = self._wait_for_job_terminal(id1)
        s2 = self._wait_for_job_terminal(id2)
        self.assertEqual(s1["status"], "succeeded")
        self.assertEqual(s2["status"], "succeeded")
        self.assertEqual(self.capture_call_count, 2)
        self.assertEqual(self.max_parallel_captures, 1)

    def test_split_focus_and_quad_endpoints(self) -> None:
        r = self.client.post(
            "/session/focus-mode",
            json={"autofocus_enabled": False, "manual_focus_value": 20},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(r.status_code, 200)
        payload = r.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["manual_config"]["manual_focus_value"], 20.0)
        cmd = self.focus_commands.get(timeout=1.0)
        self.assertEqual(cmd["kind"], "focus_mode")

        r = self.client.post(
            "/session/focus-adjust",
            json={"direction": "+", "step": 2},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(r.status_code, 200)
        payload = r.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["manual_config"]["manual_focus_value"], 22.0)
        cmd = self.focus_commands.get(timeout=1.0)
        self.assertEqual(cmd["kind"], "focus_adjust")

        r = self.client.post(
            "/session/quad-points",
            json={"quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]]},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(r.status_code, 200)
        payload = r.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["manual_config"]["valid"])

    def test_stream_does_not_block_focus_and_quad(self) -> None:
        stream_resp = self.client.get(
            "/stream.mjpg?fps=5&width=640",
            headers=_json_headers("secret-token"),
            buffered=False,
        )
        self.assertEqual(stream_resp.status_code, 200)

        r1 = self.client.post(
            "/session/focus-mode",
            json={"autofocus_enabled": False, "manual_focus_value": 30},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post(
            "/session/quad-points",
            json={"quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]]},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(r2.status_code, 200)
        stream_resp.close()

    def test_capture_wrapper_endpoints(self) -> None:
        self.client.post(
            "/session/manual-config",
            json={
                "autofocus_enabled": False,
                "manual_focus_value": 35,
                "quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]],
            },
            headers=_json_headers("secret-token"),
        )

        start_resp = self.client.post(
            "/capture/start",
            json={"readability_required": True, "timeout_seconds": 10},
            headers=_json_headers("secret-token"),
        )
        self.assertEqual(start_resp.status_code, 202)
        capture_id = start_resp.get_json()["capture"]["job_id"]

        status = self._wait_for_job_terminal(capture_id)
        self.assertEqual(status["status"], "succeeded")

        status_resp = self.client.get(f"/capture/{capture_id}/status", headers=_json_headers("secret-token"))
        self.assertEqual(status_resp.status_code, 200)
        self.assertTrue(status_resp.get_json()["ok"])

        result_resp = self.client.get(f"/capture/{capture_id}/result", headers=_json_headers("secret-token"))
        self.assertEqual(result_resp.status_code, 200)
        self.assertEqual(result_resp.mimetype, "image/png")


if __name__ == "__main__":
    unittest.main()


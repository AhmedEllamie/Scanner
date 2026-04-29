from __future__ import annotations

import time
from typing import Any

import requests


class ScannerServiceClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def health(self) -> dict[str, Any]:
        resp = requests.get(f"{self.base_url}/health", headers=self._headers(), timeout=self.timeout_seconds)
        resp.raise_for_status()
        return resp.json()

    def set_manual_config(
        self,
        *,
        quad_points: list[list[float]],
        autofocus_enabled: bool = False,
        manual_focus_value: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "autofocus_enabled": bool(autofocus_enabled),
            "quad_points": quad_points,
        }
        payload["manual_focus_value"] = None if manual_focus_value is None else float(manual_focus_value)
        resp = requests.post(
            f"{self.base_url}/session/manual-config",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def get_manual_config(self) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/session/manual-config",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def set_focus_mode(
        self,
        *,
        autofocus_enabled: bool,
        manual_focus_value: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"autofocus_enabled": bool(autofocus_enabled)}
        if manual_focus_value is not None:
            payload["manual_focus_value"] = float(manual_focus_value)
        resp = requests.post(
            f"{self.base_url}/session/focus-mode",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def adjust_focus(self, *, direction: str, step: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"direction": str(direction)}
        if step is not None:
            payload["step"] = float(step)
        resp = requests.post(
            f"{self.base_url}/session/focus-adjust",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def set_quad_points(self, *, quad_points: list[list[float]]) -> dict[str, Any]:
        payload: dict[str, Any] = {"quad_points": quad_points}
        resp = requests.post(
            f"{self.base_url}/session/quad-points",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def create_job(
        self,
        *,
        mode: str = "manual",
        readability_required: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": mode}
        if readability_required is not None:
            payload["readability_required"] = bool(readability_required)
        if timeout_seconds is not None:
            payload["timeout_seconds"] = float(timeout_seconds)
        resp = requests.post(
            f"{self.base_url}/jobs",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def start_capture(
        self,
        *,
        readability_required: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": "manual"}
        if readability_required is not None:
            payload["readability_required"] = bool(readability_required)
        if timeout_seconds is not None:
            payload["timeout_seconds"] = float(timeout_seconds)
        resp = requests.post(
            f"{self.base_url}/capture/start",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def get_capture_status(self, capture_id: str) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/capture/{capture_id}/status",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def download_capture_result(self, capture_id: str) -> bytes:
        resp = requests.get(
            f"{self.base_url}/capture/{capture_id}/result",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.content

    def get_job(self, job_id: str) -> dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/jobs/{job_id}",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def wait_for_job(
        self,
        job_id: str,
        *,
        poll_interval_seconds: float = 0.35,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        deadline = time.time() + max(0.1, timeout_seconds)
        while time.time() < deadline:
            data = self.get_job(job_id)
            job = data.get("job", {})
            status = str(job.get("status") or "").lower()
            if status in {"succeeded", "failed"}:
                return data
            time.sleep(max(0.05, poll_interval_seconds))
        raise TimeoutError(f"Timed out waiting for job {job_id}")

    def download_job_image(self, job_id: str) -> bytes:
        resp = requests.get(
            f"{self.base_url}/jobs/{job_id}/image",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.content


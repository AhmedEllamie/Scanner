from __future__ import annotations

from typing import Any

import requests
from flask import Blueprint, Response, jsonify, request

from .client import ScannerServiceClient


def create_scanner_bridge_blueprint(
    *,
    scanner_service_url: str,
    scanner_service_token: str = "",
    request_timeout_seconds: float = 10.0,
) -> Blueprint:
    bp = Blueprint("scanner_bridge", __name__, url_prefix="/scanner")

    def client() -> ScannerServiceClient:
        return ScannerServiceClient(
            scanner_service_url,
            token=scanner_service_token,
            timeout_seconds=request_timeout_seconds,
        )

    @bp.post("/manual-config")
    def set_manual_config() -> Response:
        payload = request.get_json(silent=True) or {}
        try:
            quad_points = payload["quad_points"]
            result = client().set_manual_config(
                quad_points=quad_points,
                autofocus_enabled=bool(payload.get("autofocus_enabled", False)),
                manual_focus_value=payload.get("manual_focus_value"),
            )
            return jsonify(result)
        except KeyError:
            return jsonify({"ok": False, "error": "quad_points is required"}), 400
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else 502
            return jsonify({"ok": False, "error": f"scanner_service_error: {body}"}), code
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @bp.get("/manual-config")
    def get_manual_config() -> Response:
        try:
            return jsonify(client().get_manual_config())
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else 502
            return jsonify({"ok": False, "error": f"scanner_service_error: {body}"}), code
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @bp.post("/capture-jobs")
    def create_capture_job() -> Response:
        payload = request.get_json(silent=True) or {}
        try:
            result = client().create_job(
                mode=str(payload.get("mode", "manual")),
                readability_required=payload.get("readability_required"),
                timeout_seconds=payload.get("timeout_seconds"),
            )
            return jsonify(result), 202
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else 502
            return jsonify({"ok": False, "error": f"scanner_service_error: {body}"}), code
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @bp.get("/capture-jobs/<job_id>")
    def get_capture_job(job_id: str) -> Response:
        try:
            return jsonify(client().get_job(job_id))
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else 502
            return jsonify({"ok": False, "error": f"scanner_service_error: {body}"}), code
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    @bp.get("/capture-jobs/<job_id>/image")
    def get_capture_job_image(job_id: str) -> Response:
        c = client()
        try:
            data = c.download_job_image(job_id)
            return Response(data, mimetype="image/png")
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else 502
            return jsonify({"ok": False, "error": f"scanner_service_error: {body}"}), code
        except Exception as exc:
            return jsonify({"ok": False, "error": f"unexpected_error: {exc}"}), 500

    return bp


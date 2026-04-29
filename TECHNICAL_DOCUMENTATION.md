# A4 Scanner System Technical Documentation

## 1. Purpose and Scope

This document provides full technical documentation for the A4 scanner project, covering:

- runtime architecture
- module-by-module implementation details
- computer-vision pipeline internals
- local UI workflow and keyboard controls
- HTTP scanner service and contracts
- configuration system and environment variables
- deployment and operations guidance
- testing strategy and quality notes

The project supports two main operating modes:

- interactive local scanner (`main.py`) for webcam or image processing
- async HTTP scanner service (`scanner_service`) for integration from external systems (for example Flask/FastAPI backends)

---

## 2. High-Level Architecture

### 2.1 Main Components

1. **Scanner Core (`scanner/`)**
   - camera opening and camera controls
   - fisheye correction
   - preprocessing and document detection
   - perspective warp and enhancement
   - readability evaluation
   - upload/reset/notify API helpers

2. **Interactive App (`main.py`)**
   - local webcam UI
   - auto and manual capture flow
   - keyboard-driven focus and point selection
   - save/upload/readability orchestration

3. **HTTP Service (`scanner_service/`)**
   - Flask API endpoints
   - background camera manager
   - queued capture jobs
   - manual session state + validation
   - image result retrieval + MJPEG stream

4. **Integration Layer**
   - Python client: `scanner_service/client.py`
   - Flask bridge blueprint: `scanner_service/flask_bridge.py`

---

## 3. Runtime Modes and Entry Points

## 3.1 Local Interactive Scanner

- entry point: `python main.py`
- supports:
  - webcam live mode (`run_webcam`)
  - single image mode (`process_single_image --image path`)

## 3.2 HTTP Scanner Service

- entry points:
  - `python run_scanner_service.py`
  - `python -m scanner_service`
- creates Flask app from `scanner_service.app:create_app`
- starts background camera manager and background job worker

---

## 4. Scanner Configuration Model

All scanner behavior is centralized in `scanner/config.py` (`ScannerConfig` dataclass).

### 5.1 Camera and Capture

- `camera_index`, `camera_backend`, `camera_fourcc`
- `frame_width`, `frame_height`
- `camera_autofocus_enabled`, `camera_manual_focus`, `camera_focus_step`

Linux defaults are automatically different from Windows:

- Linux camera index defaults to `0`
- Linux backend defaults to empty string (CAP_ANY/V4L2 path)
- Windows backend defaults to `DSHOW`

### 5.2 Detection and Geometry

- `gaussian_kernel`, `canny_low`, `canny_high`, `binary_threshold`
- `min_area_ratio`, `max_area_ratio`, `min_edge_px`
- `confidence_threshold`, `smoothing_alpha`

### 5.3 Warp and Enhancement

- `warp_short_side`, `scale_warp_to_capture`, `warp_capture_scale`
- `warp_short_side_min`, `warp_short_side_max`, `warp_interpolation`
- `a4_ratio`, `auto_rotate_landscape_to_portrait`, `landscape_rotation_direction`
- `apply_scan_enhancement` and enhancement tuning parameters

### 5.4 Readability Gate

- `enable_readability_check`
- `readability_mode` (`fast` or `ocr`)
- `min_readability_confidence`
- `require_readable_to_save`
- `tesseract_cmd`

### 5.5 Storage and Upload Strategy

- `save_rectified_locally`, `save_dir`
- `upload_enabled`, `upload_url`, `upload_token`, `upload_timeout_seconds`, `upload_field_name`
- `upload_from_memory`, `delete_after_upload_success`, `save_on_upload_fail`

### 5.6 Auto-Capture Cycle Control

- `auto_capture_enabled`
- `auto_capture_stable_frames`
- `single_capture_until_api_reset`
- `capture_reset_url`, `capture_reset_token`, polling and timeout options

### 5.7 Unreadable Capture Notifications

- `unreadable_notify_enabled`
- `unreadable_notify_url`
- `unreadable_notify_token`
- `unreadable_notify_timeout_seconds`

### 5.8 Fisheye Correction

- `fisheye_correction_enabled`
- `fisheye_calibration_file`
- `fisheye_balance`
- optional debug input snapshot options:
  - `save_debug_capture_with_quad`
  - `debug_capture_dir`

---

## 5. Computer Vision Pipeline

### 6.1 Input Frame

From webcam (`cv2.VideoCapture`) or provided image path.

If enabled, fisheye undistortion is applied first using calibration file (`K`, `D` arrays).

### 6.2 Preprocessing (`scanner/preprocess.py`)

Two masks are generated:

1. **Edge mask**
   - grayscale -> Gaussian blur -> Canny
   - morphological dilation + close to bridge border gaps

2. **Binary mask**
   - fixed threshold + Otsu threshold
   - OR merge
   - close/open morphology to fill text/table holes

### 6.3 Quad Detection (`scanner/detect.py`)

- contours are extracted from:
  - merged mask external contours
  - binary mask external contours
  - merged full contour list
- each contour tries relaxed quad conversion:
  - polygon approximation
  - convex hull approximations
  - min-area rectangle fallback
- candidate filters:
  - convex polygon
  - min edge length
- confidence score combines:
  - area ratio score
  - A4 aspect alignment
  - opposite-edge balance
  - contour fill quality

Best candidate and confidence are returned.

### 6.4 Geometry and Ordering (`scanner/geometry.py`)

- corners are normalized to:
  - top-left, top-right, bottom-right, bottom-left
- supports smoothing between frames:
  - `smooth_quad(current, previous, alpha)`

### 6.5 Warping and Orientation (`scanner/warp.py`)

- output target size: A4-like portrait (`short_side x short_side * a4_ratio`)
- optional auto-rotation for landscape source quads
- perspective transform with configurable interpolation:
  - linear / cubic / lanczos4

### 6.6 Scan Enhancement (`scanner/warp.py`)

Applied when `apply_scan_enhancement=True`:

1. gamma correction
2. CLAHE in LAB luminance channel
3. HSV saturation boost
4. unsharp masking (Gaussian blur + weighted add)

### 6.7 Readability Verification (`scanner/readability.py`)

Two modes:

- **fast**
  - no OCR engine required
  - score from Laplacian variance + contrast + edge density
- **ocr**
  - Tesseract via `pytesseract.image_to_data`
  - computes mean token confidence and token count

Gate decision can block saving/uploading based on config.

---

## 6. Interactive App Flow (`main.py`)

### 7.1 Image Mode (`--image`)

1. load image
2. detect quad
3. reject if confidence below threshold
4. warp + enhance
5. readability gate check
6. save/upload processing
7. optional windows display

Exit codes:

- `0`: success
- `1`: image read/open errors
- `2`: no confident detection
- `3`: readability gate rejection

### 7.2 Webcam Mode

Main loop responsibilities:

- read frame and compute FPS
- detect or use manual quad
- render overlays and status text
- manage auto-capture stability counter
- enforce one-capture lock cycle
- poll reset API when locked
- handle keyboard controls

### 7.3 Local Save + Post Processing

`persist_capture(...)` controls storage behavior:

- local save disabled: only post-process (readability log/upload)
- memory upload mode: upload bytes directly, optional fallback save on failure
- disk mode: save image then upload and optionally delete

### 7.4 Keyboard Controls

- `a`: auto mode
- `m`: manual mode focus step
- `n`: manual points step
- `p`: manual focus step
- `s`: save/capture
- `r`: reset manual points / capture lock
- `f`: autofocus toggle
- `+/-` and `1/2`: focus adjust
- `q`: quit

---

## 7. Scanner API Helper Clients (`scanner/api_client.py`)

### 8.1 Upload APIs

- `upload_scan(image_path, ...)`
- `upload_scan_bytes(image_bytes, ...)`

Both use multipart form upload and return structured `UploadResult`.

### 8.2 Reset API Polling

- `check_capture_reset_api(reset_url, ...)`

Understands multiple unlock response formats:

- booleans
- common keys (`allow_capture`, `reset`, `ready_for_next_capture`, ...)
- permissive string values (`true`, `unlock`, `ready`, etc.)

### 8.3 Unreadable Notify API

- `notify_unreadable_capture(...)`

Sends JSON payload with detector/readability confidence and reason.

---

## 8. HTTP Scanner Service Design

The service is Flask-based and asynchronous by design.

### 9.1 Core Objects

1. **`ScannerJobWorker`**
   - owns job queue and job state map
   - owns manual config state
   - executes one queued job at a time on worker thread

2. **`CameraManager`**
   - dedicated camera owner thread
   - continuously reads latest frame snapshot
   - accepts queued focus commands
   - reconnects camera with backoff on failure

3. **`ManualConfig` / `JobRecord` models**
   - serialized API state
   - timestamps, status, metadata

### 9.2 Concurrency Model

- camera acquisition is isolated in `CameraManager`
- capture jobs do not directly fight for camera lock
- jobs process snapshots from latest-frame cache
- focus updates are command-queued and asynchronous
- job queue is serialized; tests assert max parallel captures = 1

### 9.3 Job Lifecycle

1. client creates job (`queued`)
2. worker marks `running`
3. worker validates manual config and processes frame
4. on success:
   - stores `image_bytes`
   - marks `succeeded`
   - attaches metadata (`elapsed_ms`, frame size, readability info, optional debug path, optional saved path)
5. on failure:
   - marks `failed`
   - sets error + detail

### 9.4 Manual Config Validation Rules

- must include 4 points
- points normalized to ordered quad
- quad must be inside current frame dimensions
- convex polygon required
- minimum edge and area constraints required

If camera frame size is unavailable, config requests fail with service-unavailable style errors.

---

## 9. HTTP API Reference

All routes are mounted at service root.

If `SCANNER_SERVICE_TOKEN` is set:

- `/health` is public
- all other routes require token:
  - `Authorization: Bearer <token>` or
  - `X-Scanner-Token: <token>`

### 10.1 Health and Session

- `GET /health`
  - readiness + camera status
- `GET /session/manual-config`
  - current manual config state
- `POST /session/manual-config`
  - set focus + quad in one request
- `POST /session/focus-mode`
  - set autofocus/manual focus mode
- `POST /session/focus-adjust`
  - incremental focus adjustment
- `POST /session/quad-points`
  - set/replace only quad points

### 10.2 Jobs

- `POST /jobs`
  - create manual capture job (`202`)
  - request fields:
    - `mode` (currently only `"manual"`)
    - `readability_required` (optional bool override)
    - `timeout_seconds` (optional)
- `GET /jobs/{job_id}`
  - status payload
- `GET /jobs/{job_id}/image`
  - `image/png` on success
  - `409` when queued/running/failed

### 10.3 Capture Wrapper Endpoints (Alias Flow)

- `POST /capture/start` -> wraps job creation with manual mode
- `GET /capture/{capture_id}/status` -> alias of job status semantics
- `GET /capture/{capture_id}/result` -> alias of image retrieval semantics

### 10.4 Live Stream

- `GET /stream.mjpg`
  - multipart MJPEG stream from latest-frame cache
  - query params:
    - `fps` (1..25)
    - `width` (0 = original)
    - `fisheye` currently parsed but not actively switching stream processing path

---

## 10. Python Integration SDK

`scanner_service/client.py` (`ScannerServiceClient`) exposes:

- health checks
- manual config and split focus/quad operations
- job/capture creation
- polling helper (`wait_for_job`)
- image download helpers

Use this client in external backends to avoid manual HTTP boilerplate.

---

## 11. Flask Bridge for External Apps

`scanner_service/flask_bridge.py` provides a ready-to-register `Blueprint` at `/scanner`:

- `POST /scanner/manual-config`
- `GET /scanner/manual-config`
- `POST /scanner/capture-jobs`
- `GET /scanner/capture-jobs/<job_id>`
- `GET /scanner/capture-jobs/<job_id>/image`

This bridge proxies upstream scanner service responses and translates exceptions into JSON errors.

---

## 12. Deployment and Operations

### 13.1 Dependencies

From `requirements.txt`:

- `opencv-python`
- `numpy`
- `pytesseract`
- `requests`
- `flask`

System package notes:

- Linux GUI/runtime libs may be required (`libgl1`, `libglib2.0-0`)
- install `tesseract-ocr` for OCR mode

### 13.2 Service Environment Variables

- `SCANNER_SERVICE_HOST`
- `SCANNER_SERVICE_PORT`
- `SCANNER_SERVICE_TOKEN`
- scanner behavior variables (examples):
  - `SCAN_CAMERA_INDEX`
  - `SCAN_CAMERA_BACKEND`
  - `SCAN_CAMERA_FOURCC`
  - `SCAN_UPLOAD_URL`
  - `SCAN_UPLOAD_TOKEN`
  - `SCAN_CAPTURE_RESET_URL`
  - `SCAN_CAPTURE_RESET_TOKEN`
  - `SCAN_UNREADABLE_NOTIFY_URL`
  - `SCAN_UNREADABLE_NOTIFY_TOKEN`
  - `SCAN_FISHEYE_CALIBRATION_FILE`

### 13.3 Ubuntu Production Deployment

See deployment artifacts:

- `deploy/ubuntu/scanner-service.service`
- `deploy/ubuntu/a4-scanner.env.example`
- `UBUNTU_RELEASE_GUIDE.md`

Recommended operational checks:

- health endpoint (`/health`)
- journal logs
- camera availability (`v4l2-ctl --list-devices`)
- token-protected endpoint access

---

## 13. Testing Strategy

### 14.1 Service Behavior Tests (`tests/test_scanner_service.py`)

Covers:

- manual-config + successful capture path
- missing config rejection
- invalid quad rejection
- capture failure propagation
- serialized execution of queued jobs
- split focus and quad endpoint behavior
- stream endpoint coexistence with config updates
- wrapper capture endpoints

Tests use fakes/stubs for camera and capture executor to isolate API behavior.

### 14.2 Client Tests (`tests/test_scanner_service_client.py`)

Covers:

- unavailable service exception handling
- timeout handling in polling helper

---

## 14. Error Handling and Status Semantics

### 15.1 HTTP Status Patterns

- `200`: success read/update
- `202`: async job accepted
- `400`: bad request / validation failure
- `401`: unauthorized (when token enabled)
- `404`: job/capture not found
- `409`: conflict (not ready/failed/manual config missing)
- `500`: unexpected server-side errors
- `503`: camera unavailable/state unavailable conditions

### 15.2 Job Status Values

- `queued`
- `running`
- `succeeded`
- `failed`

### 15.3 Common Failure Causes

- camera unavailable / read failed
- stale or missing latest frame in worker
- invalid quad geometry
- low readability (when required)
- processing timeout or CV pipeline exceptions

---

## 15. Performance and Resource Notes

- output sharpness depends on real camera output and warp short-side scaling
- MJPEG (`camera_fourcc="MJPG"`) can help high-resolution capture on USB webcams
- `fast` readability mode is lighter for low-power hardware (for example Orange Pi)
- enabling OCR mode increases CPU and latency
- enhancement and high-quality interpolation increase compute cost

---

## 16. Security Considerations

- service token protects all non-health routes
- token should be managed via environment files, not hardcoded in source
- uploads/notifications/reset calls should prefer HTTPS endpoints
- logs may include remote response previews; avoid exposing sensitive payloads in production logs

---

## 17. Known Technical Gaps / Improvement Opportunities

- stream query `fisheye` is parsed but not currently used to switch stream correction behavior
- job retention is in-memory only; history is lost on process restart
- no endpoint for job cleanup/TTL pruning
- no built-in rate limiting or request throttling
- no structured metrics exporter (Prometheus/OpenTelemetry) yet

---

## 18. Operational Runbooks

### 19.1 Start Local Service

```bash
python run_scanner_service.py
```

### 19.2 Health Check

```bash
curl http://127.0.0.1:8008/health
```

### 19.3 Manual Capture API Sequence (Preferred)

1. `POST /session/focus-mode`
2. `POST /session/quad-points`
3. `POST /capture/start`
4. poll `GET /capture/{id}/status`
5. download `GET /capture/{id}/result`

### 19.4 Legacy Sequence (Still Supported)

1. `POST /session/manual-config`
2. `POST /jobs`
3. poll `GET /jobs/{id}`
4. download `GET /jobs/{id}/image`

---

## 19. Related Project Documents

- `README.md`: usage overview and practical commands
- `FLASK_SCANNER_HTTP_INTEGRATION.md`: endpoint-level external Flask integration
- `AUTOMATION_INTEGRATION.md`: automated capture and external reset workflow
- `UBUNTU_RELEASE_GUIDE.md`: Linux/systemd deployment instructions

This technical document is the consolidated system reference, while the above files provide scenario-specific operational guides.


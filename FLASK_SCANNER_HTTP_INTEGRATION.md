# Flask to Scanner HTTP Integration

This guide explains how a separate Flask system can integrate with this scanner service using async jobs.

## 1) Start scanner service

Run from scanner repo:

```bash
python run_scanner_service.py
```

Optional environment variables:

- `SCANNER_SERVICE_HOST` (default: `127.0.0.1`)
- `SCANNER_SERVICE_PORT` (default: `8008`)
- `SCANNER_SERVICE_TOKEN` (optional bearer token)

Base URL example:

`http://127.0.0.1:8008`

## 2) Required request sequence

1. Set focus mode (autofocus on/off):
   - `POST /session/focus-mode`
2. Optional focus step (+/-):
   - `POST /session/focus-adjust`
3. Set 4 points:
   - `POST /session/quad-points`
4. Start capture and processing:
   - `POST /capture/start`
5. Poll capture status:
   - `GET /capture/{capture_id}/status`
6. Download rectified image:
   - `GET /capture/{capture_id}/result`

Legacy combined endpoint (still supported):

- `POST /session/manual-config`

Legacy capture endpoints (still supported):

- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/image`

## 3) API details

### Live stream for accurate 4-point selection

- `GET /stream.mjpg`
- Returns MJPEG stream (`multipart/x-mixed-replace`)
- Use this in your UI to let user click exact corner points from live camera view.
- Query params:
  - `fps` (default `10`, range `1..25`)
  - `width` (default `0` = original width)
  - `fisheye` (`1` default, set `0` to disable undistort in stream)

Important behavior:

- Stream uses the shared latest-frame cache and does not hold the camera device lock.
- Focus APIs and quad APIs can be called while stream is open.
- Capture jobs also use frame snapshots from the same cache, so they can run while stream is open.
- If camera is unavailable or no recent frame exists, APIs return explicit errors.

### Health

- `GET /health`
- Response:

```json
{"ok": true, "status": "ready"}
```

### Set focus mode only

- `POST /session/focus-mode`
- Body:

```json
{
  "autofocus_enabled": false,
  "manual_focus_value": 35
}
```

Focus commands are queued to the camera owner loop and applied asynchronously.  
The response reflects requested state; device state converges on the next camera-loop ticks.

### Focus adjust (+/-) only

- `POST /session/focus-adjust`
- Body examples:

```json
{"direction": "+"}
```

```json
{"direction": "-", "step": 2.5}
```

Accepted `direction`:

- `"+"`, `"-"`, `"in"`, `"out"`, `"near"`, `"far"`

This command is also queued asynchronously to the camera owner loop.

### Set 4-point config only

- `POST /session/quad-points`
- Body:

```json
{
  "quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]]
}
```

### Set manual config (legacy combined)

- `POST /session/manual-config`
- Body:

```json
{
  "autofocus_enabled": false,
  "manual_focus_value": 35,
  "quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]]
}
```

- Success response:

```json
{
  "ok": true,
  "manual_config": {
    "autofocus_enabled": false,
    "manual_focus_value": 35.0,
    "quad_points": [[100.0, 120.0], [1700.0, 130.0], [1710.0, 980.0], [120.0, 990.0]],
    "frame_width": 1920,
    "frame_height": 1080,
    "valid": true,
    "validation_message": "ok",
    "updated_at": "2026-01-01T00:00:00+00:00"
  }
}
```

### Create job

- `POST /jobs`
- Body:

```json
{
  "mode": "manual",
  "readability_required": true,
  "timeout_seconds": 15
}
```

- Response (`202`):

```json
{
  "ok": true,
  "job": {
    "job_id": "uuid",
    "mode": "manual",
    "status": "queued",
    "created_at": "2026-01-01T00:00:00+00:00",
    "started_at": null,
    "finished_at": null,
    "error": null,
    "detail": null,
    "metadata": {}
  }
}
```

### Start capture (preferred wrapper)

- `POST /capture/start`
- Body:

```json
{
  "readability_required": true,
  "timeout_seconds": 15
}
```

Local save behavior for this API:

- Controlled by `ScannerConfig.save_rectified_locally`.
- `True`: successful captures are saved to `ScannerConfig.save_dir` and still available via `/capture/{id}/result`.
- `False`: no local file is written; image is available only through API response endpoints.

Debug snapshot behavior (before rectification):

- Set `ScannerConfig.save_debug_capture_with_quad = True` to save the live input frame used by the job with the received 4-point polygon drawn on it.
- Saved in `ScannerConfig.debug_capture_dir` (default `output/debug`).
- When available, the file path is returned in job metadata as `debug_input_path` from:
  - `GET /capture/{capture_id}/status`
  - `GET /jobs/{job_id}`

- Response (`202`):

```json
{
  "ok": true,
  "capture": {
    "job_id": "uuid",
    "mode": "manual",
    "status": "queued"
  }
}
```

### Poll job

- `GET /jobs/{job_id}`
- Terminal statuses: `succeeded`, `failed`

### Download image

- `GET /jobs/{job_id}/image`
- Returns `image/png` on success
- Returns `409` if job not ready or failed

### Capture wrapper status/result

- `GET /capture/{capture_id}/status`
- `GET /capture/{capture_id}/result`
- Result endpoint returns `image/png` on success

## 4) Authentication

If `SCANNER_SERVICE_TOKEN` is set, include either:

- `Authorization: Bearer <token>`
- or `X-Scanner-Token: <token>`

`/health` is public.

## 5) Flask example (requests)

```python
import time
import requests

BASE = "http://127.0.0.1:8008"
TOKEN = "your-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# 1) set focus mode
r = requests.post(
    f"{BASE}/session/focus-mode",
    json={"autofocus_enabled": False, "manual_focus_value": 35},
    headers=HEADERS,
    timeout=10,
)
r.raise_for_status()

# 2) optional focus +/- adjustment
r = requests.post(
    f"{BASE}/session/focus-adjust",
    json={"direction": "+", "step": 1.0},
    headers=HEADERS,
    timeout=10,
)
r.raise_for_status()

# 3) set 4 points
r = requests.post(
    f"{BASE}/session/quad-points",
    json={"quad_points": [[100, 120], [1700, 130], [1710, 980], [120, 990]]},
    headers=HEADERS,
    timeout=10,
)
r.raise_for_status()

# 4) start capture
r = requests.post(
    f"{BASE}/capture/start",
    json={"readability_required": True, "timeout_seconds": 15},
    headers=HEADERS,
    timeout=10,
)
r.raise_for_status()
capture_id = r.json()["capture"]["job_id"]

# 5) poll status
while True:
    r = requests.get(f"{BASE}/capture/{capture_id}/status", headers=HEADERS, timeout=10)
    r.raise_for_status()
    cap = r.json()["capture"]
    if cap["status"] in ("succeeded", "failed"):
        break
    time.sleep(0.4)

if cap["status"] != "succeeded":
    raise RuntimeError(f"Capture failed: {cap.get('error')} - {cap.get('detail')}")

# 6) download rectified PNG
r = requests.get(f"{BASE}/capture/{capture_id}/result", headers=HEADERS, timeout=15)
r.raise_for_status()
with open("rectified.png", "wb") as f:
    f.write(r.content)
```

## 6) Browser/UI stream usage

Simple HTML:

```html
<img src="http://127.0.0.1:8008/stream.mjpg?fps=12&width=1280" />
```

If token is required, browsers cannot easily set custom auth headers for `<img>`.
Use one of these patterns:

1. Same-network trusted setup without token for local service
2. Backend proxy endpoint in Flask that forwards stream with auth header

Example Flask proxy:

```python
from flask import Response, stream_with_context
import requests

@app.get("/scanner/live")
def scanner_live():
    upstream = requests.get(
        "http://127.0.0.1:8008/stream.mjpg?fps=12&width=1280",
        headers={"Authorization": "Bearer your-token"},
        stream=True,
        timeout=30,
    )
    upstream.raise_for_status()
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=8192)),
        content_type=upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame"),
    )
```
## 7) Built-in helper modules

This repo also provides:

- `scanner_service/client.py`: reusable Python client
- `scanner_service/flask_bridge.py`: Flask blueprint adapter


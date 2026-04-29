# Automatic Paper Scanner Integration Guide

This project now supports fully automatic scanning in one-shot cycles:

- detects the paper in live camera mode
- flattens (perspective-corrects) it
- checks readability
- if readable: saves locally and/or uploads
- if unreadable: can send an API request to ask for recapture
- after one attempt: capture is locked until reset API unlocks it

No `S` key is required.

## Option A: Separate HTTP Scanner Service (Async Jobs)

If your Flask backend is in a separate repo, run this scanner repo as a local service and call it over HTTP.

Start service:

```bash
python run_scanner_service.py
```

Config env vars:

- `SCANNER_SERVICE_HOST` (default `127.0.0.1`)
- `SCANNER_SERVICE_PORT` (default `8008`)
- `SCANNER_SERVICE_TOKEN` (optional bearer token)

Manual capture contract:

1. `POST /session/manual-config` with focus settings + 4 corner points.
2. `POST /jobs` with `{"mode":"manual"}`.
3. Poll `GET /jobs/{job_id}` until `status` is `succeeded` or `failed`.
4. Fetch image from `GET /jobs/{job_id}/image`.

Flask-side helpers are available in:

- `scanner_service/client.py` (HTTP client)
- `scanner_service/flask_bridge.py` (ready-to-register Flask blueprint)

## 1) Quick Start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Optional (for OCR readability mode):
- Install Tesseract OCR and set `tesseract_cmd` if not in PATH.

3. Run webcam mode:

```bash
python main.py
```

The scanner starts in `AUTO` mode and captures automatically when the page is stable.
After that single attempt, capture is locked and waits for reset API response.

## 2) Automatic Workflow

For each stable detected document (when lock is open):

1. Detect page quad.
2. Warp to A4-like top-down output.
3. Run readability gate.
4. If readable:
   - save file to `output/` (or upload from memory depending config)
5. If unreadable:
   - skip save
   - optionally call unreadable notification API
6. Raise capture lock.
7. Poll reset API until it returns allow/reset true, then unlock for next document.

This gives strict one-photo-per-cycle behavior.

## 3) Key Config Options

Edit `scanner/config.py`:

- `auto_capture_enabled`: enable/disable fully automatic capture.
- `auto_capture_stable_frames`: frames required before auto-capture.
- `single_capture_until_api_reset`: lock after one attempt.
- `capture_reset_url`: API endpoint checked to unlock next capture.
- `capture_reset_token`: optional bearer token for reset API.
- `capture_reset_poll_interval_seconds`: API polling interval while locked.
- `capture_reset_timeout_seconds`: reset API timeout.
- `enable_readability_check`: run readability verification.
- `require_readable_to_save`: block save/upload when unreadable.
- `upload_enabled`, `upload_url`, `upload_token`: readable-scan upload target.
- `upload_from_memory`: upload without creating local file first.
- `unreadable_notify_enabled`, `unreadable_notify_url`, `unreadable_notify_token`: API request when unreadable.

## 4) CLI Overrides

You can set API endpoints from command line:

```bash
python main.py \
  --capture-reset-url "https://api.example.com/scan/reset" \
  --capture-reset-token "YOUR_RESET_TOKEN" \
  --upload-url "https://api.example.com/upload" \
  --upload-token "YOUR_UPLOAD_TOKEN" \
  --unreadable-notify-url "https://api.example.com/scan/unreadable" \
  --unreadable-notify-token "YOUR_NOTIFY_TOKEN"
```

## 5) API Contract

### 5.1 Readable scan upload

- Method: `POST`
- Content: `multipart/form-data`
- Field name: `file` (configurable by `upload_field_name`)
- File type: `image/png`
- Auth: `Authorization: Bearer <token>` (optional)

### 5.2 Unreadable notification request

- Method: `POST`
- Content: JSON
- Auth: `Authorization: Bearer <token>` (optional)

Payload example:

```json
{
  "event": "scan_unreadable",
  "detector_confidence": 0.83,
  "readability_confidence": 21.4,
  "readability_tokens": 1,
  "reason": "Low readability",
  "action": "recapture_requested"
}
```

### 5.3 Reset/unlock API (required for next photo)

- Method: `GET` (scanner polls while locked)
- Auth: `Authorization: Bearer <token>` (optional)
- Response: any JSON/text containing a truthy reset value

Accepted truthy examples:

- `{"allow_capture": true}`
- `{"reset": true}`
- `{"ready_for_next_capture": 1}`
- plain text: `true` / `1` / `unlock`

## 6) Integration Patterns

### Pattern A: Save locally + process later

- Keep `upload_enabled = False`.
- Watch `output/` from another service and process new files.

### Pattern B: Direct backend upload

- Set `upload_enabled = True`.
- Use `upload_from_memory = True` for stateless operation.

### Pattern C: Feedback loop to external system

- Configure `unreadable_notify_url`.
- Configure `capture_reset_url`.
- Your backend receives unreadable events and can trigger:
  - UI message to user
  - buzzer/light feedback
  - retry instruction
- When operator/system is ready for next photo, reset API returns `allow_capture=true`.

## 7) Minimal Backend Example (FastAPI)

```python
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    # TODO: store/process image
    return {"ok": True, "bytes": len(content), "filename": file.filename}

@app.post("/scan/unreadable")
async def unreadable_event(payload: dict):
    # TODO: trigger recapture workflow in your system
    return JSONResponse({"ok": True, "received": payload})

@app.get("/scan/reset")
async def reset_state():
    # TODO: return True when your system wants scanner to take next photo
    return {"allow_capture": True}
```

## 8) Notes for Embedding in Another Project

- Run this scanner as a standalone process and integrate through HTTP APIs.
- Or import `main.py` logic and call `run_webcam(cfg)` from your own app.
- Keep scan thresholds configurable per camera/lighting setup.
- Start with `readability_mode = "fast"` on low-power devices, switch to `"ocr"` when possible.

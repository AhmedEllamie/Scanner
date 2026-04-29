# Automated Signature Monorepo

This repository now combines two related systems into one codebase:

- `A4 Scanner` (root project): captures and rectifies A4 documents from camera or image input.
- `Plotter Signature` (`plotter-signature/`): printer automation service (Flask UI/API, FastAPI API, CLI, and desktop kiosk).

The goal is to run both components together as one workflow: scan a clean page, then route data to signature/printing automation.

## Monorepo Structure

```text
.
|-- main.py                         # Scanner app entrypoint
|-- scanner/                        # Scanner core modules
|-- scanner_service/                # Async scanner HTTP service + bridge client
|-- plotter-signature/              # Imported subtree (second repo)
|   |-- plotter_signature/          # Plotter package
|   |-- deploy/
|   |-- docs/
|   `-- README.md                   # Plotter-specific deep docs
|-- requirements.txt                # Scanner dependencies
|-- AUTOMATION_INTEGRATION.md
|-- FLASK_SCANNER_HTTP_INTEGRATION.md
|-- TECHNICAL_DOCUMENTATION.md
`-- README.md                       # This file
```

## Components Overview

### 1) A4 Scanner (root)

Main capabilities:

- Auto-detect page corners and perspective-correct to A4 ratio.
- Manual fallback (focus + 4-point corner selection).
- Optional readability validation (fast mode or OCR mode with Tesseract).
- Optional upload and API callback integration.
- Optional standalone HTTP scanner service (`scanner_service`).

Main entrypoints:

- `python main.py`
- `python run_scanner_service.py`
- `python -m scanner_service`

### 2) Plotter Signature (`plotter-signature/`)

Main capabilities:

- Printer automation services and domain logic.
- Flask UI + REST API surface.
- FastAPI printer endpoints.
- CLI entrypoint and desktop pen kiosk.
- Configurable via `appsettings.json` and environment variables.

Main entrypoints:

- `python -m plotter_signature --help`
- `python -m plotter_signature serve-flask --host 0.0.0.0 --port 5001`
- `python -m plotter_signature serve-api --host 0.0.0.0 --port 5000`
- `python -m plotter_signature.desktop.pen_kiosk`

## Prerequisites

- Python 3.10+ recommended.
- OS: Windows or Linux.
- Webcam (for scanner camera mode).
- Optional: Tesseract OCR (required only for scanner OCR readability mode).

Install Tesseract on Windows:

```powershell
winget install --id tesseract-ocr.tesseract --accept-source-agreements --accept-package-agreements
```

## Setup

You can use one shared virtual environment for the whole monorepo.

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r .\plotter-signature\requirements.txt
pip install -e .\plotter-signature
```

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r ./plotter-signature/requirements.txt
pip install -e ./plotter-signature
```

## How To Use

## A) Run scanner only

Live camera mode:

```bash
python main.py
```

Image mode:

```bash
python main.py --image "C:\path\to\image.jpg"
```

OCR readability check:

```bash
python main.py --image "C:\path\to\image.jpg" --verify-readable
```

If Tesseract is not in PATH:

```bash
python main.py --image "C:\path\to\image.jpg" --verify-readable --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Useful scanner keyboard shortcuts:

- `a` auto mode
- `m` manual mode (focus step)
- `n` manual points step
- `s` save rectified result
- `r` reset manual points
- `q` quit

## B) Run scanner as HTTP service

```bash
python run_scanner_service.py
```

or:

```bash
python -m scanner_service
```

Default: `127.0.0.1:8008`

Main endpoints:

- `GET /health`
- `GET /session/manual-config`
- `POST /session/manual-config`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/image`

## C) Run plotter signature services

From repository root (after installing editable package):

Flask UI + API:

```bash
python -m plotter_signature serve-flask --host 0.0.0.0 --port 5001
```

FastAPI printer API:

```bash
python -m plotter_signature serve-api --host 0.0.0.0 --port 5000
```

CLI help:

```bash
python -m plotter_signature --help
```

## D) Typical combined workflow

1. Start scanner (`main.py` or `scanner_service`).
2. Capture/rectify clean page image.
3. Validate readability (optional).
4. Pass data/image to plotter workflow (Flask/FastAPI/CLI side).
5. Execute printer/signature automation.

For integration details between scanning and service flows, see:

- `AUTOMATION_INTEGRATION.md`
- `FLASK_SCANNER_HTTP_INTEGRATION.md`

## Configuration

Scanner configuration:

- File: `scanner/config.py`
- Environment vars examples:
  - `SCAN_CAMERA_INDEX`
  - `SCAN_CAMERA_BACKEND`
  - `SCAN_CAMERA_FOURCC`
  - `SCAN_UPLOAD_URL`
  - `SCAN_UPLOAD_TOKEN`
  - `SCAN_CAPTURE_RESET_URL`
  - `SCAN_UNREADABLE_NOTIFY_URL`

Plotter configuration:

- File: `plotter-signature/appsettings.json`
- Package docs: `plotter-signature/README.md`
- Deployment/env templates: `plotter-signature/deploy/ubuntu/`

Authentication:

- Plotter HTTP APIs expect shared header `X-API-Key`.
- Set server key via `PLOTTER_API_KEY`.

## Additional Documentation

Scanner docs (root):

- `TECHNICAL_DOCUMENTATION.md`
- `UBUNTU_RELEASE_GUIDE.md`
- `AUTOMATION_INTEGRATION.md`
- `FLASK_SCANNER_HTTP_INTEGRATION.md`

Plotter docs:

- `plotter-signature/README.md`
- `plotter-signature/docs/api-pre-security/README.md`
- `plotter-signature/docs/doxygen/README.md`

## Troubleshooting

- If camera opens with low resolution, verify driver support and USB bandwidth.
- If OCR readability fails, confirm Tesseract is installed and reachable.
- If plotter APIs return auth errors, verify `PLOTTER_API_KEY` and `X-API-Key`.
- If import errors appear for `plotter_signature`, run `pip install -e ./plotter-signature` again in the active environment.


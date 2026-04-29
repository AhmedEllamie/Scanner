# A4 Scanner service

Captures and rectifies A4 documents from a camera or image file, with an optional HTTP service (`scanner_service`) for automation.

**Repositories:** this codebase is [AhmedEllamie/Scanner](https://github.com/AhmedEllamie/Scanner). To work on **scanner and plotter** together, clone the meta repo with submodules: [AhmedEllamie/Automated_Signature](https://github.com/AhmedEllamie/Automated_Signature) (sibling folder `plotter-signature/` is [Plotter](https://github.com/AhmedEllamie/Plotter)).

## Repository structure

```text
.
|-- main.py                         # Scanner app entrypoint
|-- scanner/                        # Scanner core modules
|-- scanner_service/                # HTTP service + bridge client
|-- deploy/ubuntu/                  # systemd + env templates
|-- requirements.txt
|-- README.md                       # This file
`-- ...
```

## Components overview

### A4 Scanner

- Auto-detect page corners and perspective-correct to A4 ratio.
- Manual fallback (focus + 4-point corner selection).
- Optional readability validation (fast mode or OCR with Tesseract).
- Optional upload and API callback integration.
- Standalone HTTP scanner service (`scanner_service`).

Main entrypoints:

- `python main.py`
- `python run_scanner_service.py`
- `python -m scanner_service`

### Plotter Signature (separate repo)

Printer automation (Flask UI/API, FastAPI, CLI, kiosk) lives in **[Plotter](https://github.com/AhmedEllamie/Plotter)**. From a meta-repo checkout it appears as `plotter-signature/`. See that repository for install and `serve-flask` / `serve-api` commands.

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

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## How to use

### A) Run scanner only

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

### B) Run scanner as HTTP service

```bash
python run_scanner_service.py
```

or:

```bash
python -m scanner_service
```

Default bind: `127.0.0.1:8008`

Main endpoints:

- `GET /health`
- `GET /session/manual-config`
- `POST /session/manual-config`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/image`

### C) Typical combined workflow (with Plotter)

1. Start scanner (`main.py` or `scanner_service`).
2. Capture/rectify a clean page image.
3. Validate readability (optional).
4. Pass data/image to the Plotter workflow (see [Plotter](https://github.com/AhmedEllamie/Plotter)).

For integration details:

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

Plotter (separate repo): `appsettings.json`, `PLOTTER_API_KEY`, deploy templates under **Plotter** `deploy/ubuntu/`. Plotter APIs expect `X-API-Key`.

## Additional documentation

- `TECHNICAL_DOCUMENTATION.md`
- `UBUNTU_RELEASE_GUIDE.md`
- `AUTOMATION_INTEGRATION.md`
- `FLASK_SCANNER_HTTP_INTEGRATION.md`

## Troubleshooting

- If camera opens with low resolution, verify driver support and USB bandwidth.
- If OCR readability fails, confirm Tesseract is installed and reachable.
- If Plotter APIs return auth errors, verify `PLOTTER_API_KEY` and `X-API-Key` on the **Plotter** service.

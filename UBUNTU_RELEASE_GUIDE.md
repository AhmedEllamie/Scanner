# Ubuntu Release Guide (A4 Scanner)

This guide prepares the project for production-style deployment on Ubuntu using `systemd`.

## 1) Install OS packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tesseract-ocr libgl1 libglib2.0-0 v4l-utils curl
```

## 2) Create runtime user and app directory

```bash
sudo mkdir -p /opt/a4-flating
sudo chown -R $USER:$USER /opt/a4-flating
```

Choose one of these:

### Option A: Clone directly into `/opt`

```bash
cd /opt
git clone <your-repo-url> a4-flating
```

### Option B: You already copied repo to home (your case)

If your code is already in `~/a4-flating`, move it:

```bash
sudo mv ~/a4-flating /opt/a4-flating
sudo chown -R $USER:$USER /opt/a4-flating
```

If your folder name is different, replace `~/a4-flating` with your real path.

### Option C: Extra inner folder (ZIP / copied Windows tree)

If the real repo root is **nested** (e.g. `/opt/a4-flating/a4 flating/scanner_service/...`), `WorkingDirectory` must be that inner folder — the directory that **directly** contains `scanner_service/` and `scanner/` — not `/opt/a4-flating` alone. Otherwise you get `No module named scanner_service`.

Paths with spaces are awkward for systemd; **rename** the inner folder once:

```bash
sudo mv "/opt/a4-flating/a4 flating" /opt/a4-flating/app
```

Then point the unit file at `/opt/a4-flating/app` for **both** `WorkingDirectory` **and** `Environment=PYTHONPATH=...` (see §6). Setting only `WorkingDirectory` is not enough for some manual tests, and `PYTHONPATH` avoids edge cases. You can keep the venv at `/opt/a4-flating/.venv` and the same `ExecStart` path to that interpreter.

To test imports from a shell, either change into the app root or set `PYTHONPATH` (running from `~` without that will always fail):

```bash
cd /opt/a4-flating/app && /opt/a4-flating/.venv/bin/python -c "import scanner_service; print('ok')"
```

To see where the code actually landed:

```bash
find /opt/a4-flating -path '*/scanner_service/__main__.py' 2>/dev/null
```

The directory **containing** `scanner_service` (not `scanner_service` itself) is the repo root for the service.

## 3) Python environment

```bash
cd /opt/a4-flating
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

If `requirements.txt` only exists under the nested app dir (e.g. `app/requirements.txt`), use that path instead: `pip install -r app/requirements.txt`.

## 4) Recommended Ubuntu config

Good defaults for Ubuntu are now auto-selected by code:

- Camera index defaults to `0` on Linux.
- Camera backend defaults to empty string (`CAP_ANY`/V4L2 path on Linux).

For explicit production behavior, set these env vars:

```bash
SCAN_CAMERA_INDEX=0
SCAN_CAMERA_BACKEND=V4L2
SCAN_CAMERA_FOURCC=MJPG
```

If your calibration file does not exist, either provide it or disable fisheye correction in config.

## 5) Create service env file

```bash
sudo cp deploy/ubuntu/a4-scanner.env.example /etc/default/a4-scanner
sudo nano /etc/default/a4-scanner
```

Set at least:

- `SCANNER_SERVICE_HOST=0.0.0.0`
- `SCANNER_SERVICE_PORT=8008`
- `SCANNER_SERVICE_TOKEN=<strong-random-token>`

## 6) Install systemd service

Before installing the unit, confirm the app tree exists (otherwise you get `No module named scanner_service`):

```bash
test -f /opt/a4-flating/scanner_service/__main__.py && echo OK || echo "Not at /opt/a4-flating root — find it with: find /opt/a4-flating -path '*/scanner_service/__main__.py'"
```

```bash
sudo cp deploy/ubuntu/scanner-service.service /etc/systemd/system/a4-scanner.service
sudo nano /etc/systemd/system/a4-scanner.service
```

Update these values if needed:

- `User=ubuntu` (or your service user)
- `WorkingDirectory=/opt/a4-flating` (must be the **repository root** that contains `scanner_service/` and `scanner/` — e.g. `/opt/a4-flating/app` if you used Option C)
- `Environment=PYTHONPATH=...` (**exactly the same path as `WorkingDirectory`**; e.g. both `/opt/a4-flating` or both `/opt/a4-flating/app`)
- `ExecStart=/opt/a4-flating/.venv/bin/python -m scanner_service`

If camera access fails, ensure service user is in `video` group:

```bash
sudo usermod -aG video ubuntu
```

Then reload and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now a4-scanner
```

## 7) Verify health

```bash
curl http://127.0.0.1:8008/health
```

With token-protected endpoints:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8008/session/manual-config
```

Check logs:

```bash
sudo journalctl -u a4-scanner -f
```

### If logs say `Address already in use` / `Port 8008 is in use`

Something else is already bound to that port (often a **leftover** `python -m scanner_service` from a manual test, or a **stuck** service instance). See what holds the port:

```bash
sudo ss -tlnp | grep 8008
# or: sudo lsof -i :8008
```

Stop the scanner service first, then end the other process if it is yours, or change the port in `/etc/default/a4-scanner`:

```bash
SCANNER_SERVICE_PORT=8009
```

After editing the env file: `sudo systemctl restart a4-scanner`.

### Camera / V4L2 warnings in the journal

Warnings like `can't open camera by index` or `Not a video capture device` often mean the wrong **`SCAN_CAMERA_INDEX`** or `/dev/video0` is not the webcam. List devices with `v4l2-ctl --list-devices` and try another index via `/etc/default/a4-scanner`. The HTTP service can still start if the port is free; the preview may stay unavailable until the camera opens.

## 8) Useful operations

```bash
sudo systemctl restart a4-scanner
sudo systemctl status a4-scanner
sudo systemctl stop a4-scanner
```

## 9) Release checklist

- Ubuntu packages installed.
- `.venv` and Python deps installed.
- Service env file configured with real tokens/URLs.
- Service enabled and healthy after reboot.
- Camera device reachable (`v4l2-ctl --list-devices`).
- Upload/reset/notify endpoints tested (if enabled).


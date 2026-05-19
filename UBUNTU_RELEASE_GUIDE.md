# Ubuntu Release Guide (A4 Scanner)

HTTP scanner service on Ubuntu with **systemd**. Default template paths use the **meta-repo**:

**`/opt/Automated_Signature/a4-flating`**

For standalone installs use **`/opt/a4-flating`**. Rewrite paths with:

```bash
PLOTTER_INSTALL_ROOT=/opt/Automated_Signature/plotter-signature   # sibling repo
A4_INSTALL_ROOT=/opt/Automated_Signature/a4-flating
A4_SERVICE_USER=diwan
"$PLOTTER_INSTALL_ROOT/deploy/ubuntu/configure-units.sh" --scanner-only
```

## 1) Install OS packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tesseract-ocr libgl1 libglib2.0-0 v4l-utils curl
```

## 2) Install directory and ownership

**Do not use `sudo` for venv/pip.**

```bash
# Meta repo:
sudo mkdir -p /opt
cd /opt
sudo git clone --recurse-submodules https://github.com/AhmedEllamie/Automated_Signature.git
sudo chown -R $USER:$USER /opt/Automated_Signature
cd /opt/Automated_Signature/a4-flating

# Standalone:
# cd /opt && git clone https://github.com/AhmedEllamie/Scanner.git a4-flating
# sudo chown -R $USER:$USER /opt/a4-flating && cd /opt/a4-flating
```

### Nested folder (ZIP / Windows copy)

If `scanner_service/` is not at the repo root you cloned, find it:

```bash
find /opt/Automated_Signature/a4-flating -path '*/scanner_service/__main__.py'
```

Use that directory for **`WorkingDirectory`** and **`PYTHONPATH`**. Rename paths with spaces if needed.

## 3) Python environment

```bash
cd /opt/Automated_Signature/a4-flating
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -c "import scanner_service; print('ok')"
```

## 4) Service env file

```bash
sudo cp deploy/ubuntu/a4-scanner.env.example /etc/default/a4-scanner
sudo nano /etc/default/a4-scanner
```

Set at least:

- `SCANNER_SERVICE_HOST=0.0.0.0`
- `SCANNER_SERVICE_PORT=8008`
- `SCANNER_SERVICE_TOKEN=<strong-random-token>`

Match the same token in plotter **`/etc/plotter-signature/plotter-signature.env`** as **`SCANNER_SERVICE_TOKEN`**.

Camera (adjust after `v4l2-ctl --list-devices`):

```bash
SCAN_CAMERA_INDEX=0
SCAN_CAMERA_BACKEND=V4L2
SCAN_CAMERA_FOURCC=MJPG
```

## 5) systemd service

```bash
test -f /opt/Automated_Signature/a4-flating/scanner_service/__main__.py && echo OK

export A4_INSTALL_ROOT=/opt/Automated_Signature/a4-flating
export A4_SERVICE_USER=diwan
/opt/Automated_Signature/plotter-signature/deploy/ubuntu/configure-units.sh --scanner-only

sudo cp deploy/ubuntu/scanner-service.service /etc/systemd/system/a4-scanner.service
sudo nano /etc/systemd/system/a4-scanner.service
```

- **`User=`** — your service account (in **`video`** group).
- **`WorkingDirectory`** and **`PYTHONPATH`** — same repo root.
- **`ExecStart`** — `.../.venv/bin/python -m scanner_service`

```bash
sudo usermod -aG video $USER
sudo systemctl daemon-reload
sudo systemctl enable --now a4-scanner
```

## 6) Firewall and LAN access

From the plotter repo (or manually):

```bash
/opt/Automated_Signature/plotter-signature/deploy/ubuntu/ufw-services.sh
# allows 22, 5001, 8008
```

## 7) Verify

```bash
curl http://127.0.0.1:8008/health
sudo ss -tlnp | grep 8008
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8008/session/manual-config
```

Healthy camera example: `"status":"ready"` with `frame_width` / `frame_height` set.

### Camera journal noise

`Not a video capture device` on `/dev/video20+` (Pi ISP) is common during probe. If `/health` is **ready**, try another **`SCAN_CAMERA_INDEX`**. USB camera often uses `/dev/video0` or `/dev/video1` — check with `v4l2-ctl --list-devices`.

### Port in use

```bash
sudo ss -tlnp | grep 8008
sudo systemctl restart a4-scanner
```

## 8) Operations

```bash
sudo systemctl restart a4-scanner
sudo journalctl -u a4-scanner -f
```

## 9) Checklist

- [ ] Repo root contains `scanner_service/` and `.venv`
- [ ] `/etc/default/a4-scanner` configured
- [ ] Unit paths match install root (not stale `/opt/a4-flating` if code lives under `Automated_Signature`)
- [ ] `a4-scanner` active; `curl :8008/health` OK
- [ ] Plotter env: `SCANNER_SERVICE_BASE_URL` + matching token
- [ ] `ufw` allows 8008 if accessing from other machines

Plotter + kiosk: **`plotter-signature/docs/UBUNTU_RELEASE_GUIDE.md`**.

from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import cv2
import numpy as np


def capture_images(camera_index: int, output_dir: Path, prefix: str, max_images: int) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera index {camera_index}")

    print("Capture mode")
    print("- Press SPACE to capture a checkerboard image")
    print("- Press Q to stop")
    print(f"- Images will be saved to: {output_dir}")

    count = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Warning: camera frame read failed.")
                break
            cv2.putText(
                frame,
                f"Captured: {count}/{max_images}",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("Fisheye Calibration Capture", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q")):
                break
            if key == 32:  # Space
                out_path = output_dir / f"{prefix}_{count:03d}.jpg"
                cv2.imwrite(str(out_path), frame)
                count += 1
                print(f"Saved {out_path}")
                if count >= max_images:
                    break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return count


def build_checkerboard_object_points(cols: int, rows: int, square_size: float) -> np.ndarray:
    objp = np.zeros((1, cols * rows, 3), np.float32)
    objp[0, :, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objp *= square_size
    return objp


def calibrate_fisheye(
    image_paths: list[str],
    checkerboard_cols: int,
    checkerboard_rows: int,
    square_size: float,
) -> tuple[float, np.ndarray, np.ndarray, int]:
    if not image_paths:
        raise RuntimeError("No calibration images found.")

    checkerboard = (checkerboard_cols, checkerboard_rows)
    objp = build_checkerboard_object_points(checkerboard_cols, checkerboard_rows, square_size)
    objpoints: list[np.ndarray] = []
    imgpoints: list[np.ndarray] = []
    image_size: tuple[int, int] | None = None

    subpix_criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-6)
    find_flags = (
        cv2.CALIB_CB_ADAPTIVE_THRESH
        + cv2.CALIB_CB_NORMALIZE_IMAGE
        + cv2.CALIB_CB_FAST_CHECK
    )

    for path in image_paths:
        img = cv2.imread(path)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if image_size is None:
            image_size = gray.shape[::-1]
        ok, corners = cv2.findChessboardCorners(gray, checkerboard, find_flags)
        if not ok:
            continue
        corners = cv2.cornerSubPix(gray, corners, (3, 3), (-1, -1), subpix_criteria)
        objpoints.append(objp)
        imgpoints.append(corners)

    valid_count = len(objpoints)
    if valid_count < 8:
        raise RuntimeError(
            f"Not enough valid checkerboard detections: {valid_count}. "
            "Need at least 8, recommended 20-40."
        )
    if image_size is None:
        raise RuntimeError("Could not determine image size from calibration images.")

    k = np.zeros((3, 3), dtype=np.float64)
    d = np.zeros((4, 1), dtype=np.float64)
    rvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in range(valid_count)]
    tvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in range(valid_count)]
    flags = (
        cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
        + cv2.fisheye.CALIB_CHECK_COND
        + cv2.fisheye.CALIB_FIX_SKEW
    )
    rms, k, d, _rvecs, _tvecs = cv2.fisheye.calibrate(
        objpoints,
        imgpoints,
        image_size,
        k,
        d,
        rvecs,
        tvecs,
        flags=flags,
        criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-6),
    )
    return float(rms), k, d, valid_count


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create fisheye calibration .npz (K, D).")
    p.add_argument("--capture", action="store_true", help="Capture calibration images from webcam first.")
    p.add_argument("--camera-index", type=int, default=0, help="Webcam index for --capture.")
    p.add_argument(
        "--capture-dir",
        type=str,
        default="calibration/images",
        help="Output directory for captured images.",
    )
    p.add_argument("--capture-prefix", type=str, default="cb", help="Captured image filename prefix.")
    p.add_argument("--capture-count", type=int, default=30, help="How many images to capture.")
    p.add_argument(
        "--images-glob",
        type=str,
        default="calibration/images/*.jpg",
        help="Glob pattern for calibration images.",
    )
    p.add_argument("--cols", type=int, default=9, help="Checkerboard inner corners columns.")
    p.add_argument("--rows", type=int, default=6, help="Checkerboard inner corners rows.")
    p.add_argument("--square-size", type=float, default=1.0, help="Checker square size (any unit).")
    p.add_argument(
        "--output",
        type=str,
        default="calibration/fisheye_test.npz",
        help="Output .npz file path.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.capture:
        captured = capture_images(
            camera_index=args.camera_index,
            output_dir=Path(args.capture_dir),
            prefix=args.capture_prefix,
            max_images=max(1, args.capture_count),
        )
        print(f"Captured {captured} images.")

    image_paths = sorted(glob.glob(args.images_glob))
    print(f"Found {len(image_paths)} images matching: {args.images_glob}")

    rms, k, d, valid_count = calibrate_fisheye(
        image_paths=image_paths,
        checkerboard_cols=args.cols,
        checkerboard_rows=args.rows,
        square_size=args.square_size,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(str(output_path), K=k, D=d)

    print("Calibration complete")
    print(f"- Valid checkerboard images: {valid_count}")
    print(f"- RMS reprojection error: {rms:.6f}")
    print(f"- Saved file: {output_path}")
    print("\nUse in scanner/config.py:")
    print("fisheye_correction_enabled = True")
    print(f'fisheye_calibration_file = "{output_path.as_posix()}"')
    print("fisheye_balance = 0.2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

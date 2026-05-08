"""YOLOv8 person silhouette → 64x64 mask → ESP32 over USB serial.

Mirrors vision.py (camera, YOLO person seg, conf trackbar, q/s/SPACE controls)
and adds a SerialSender that ships each 64x64 frame to the ESP32 driving the
HUB75 LED panel. TX is throttled to ~30 fps; the preview keeps running at
whatever rate YOLO can sustain. Use --no-serial to tune the CV pipeline alone.
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
import time

import cv2
import numpy as np
from ultralytics import YOLO

from serial_protocol import SerialSender

CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
OUTPUT_SIZE = 64

MODEL = "yolov8n-seg.pt"
PERSON_CLASS_ID = 0
INFER_IMGSZ = 640

TX_MIN_INTERVAL_S = 1.0 / 30.0  # ~30 fps cap on the wire


def open_camera(index: int) -> cv2.VideoCapture:
    """Open a USB camera, trying common backends for portability."""
    for backend in (cv2.CAP_AVFOUNDATION, cv2.CAP_V4L2, cv2.CAP_ANY):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            return cap
        cap.release()
    raise RuntimeError(f"Could not open camera index {index}")


def autodetect_port() -> str | None:
    """Find the first plausible USB-serial port for the ESP32."""
    if sys.platform == "darwin":
        patterns = (
            "/dev/cu.usbserial-*",
            "/dev/cu.SLAB_*",
            "/dev/cu.wchusbserial*",
            "/dev/cu.usbmodem*",
        )
    else:
        patterns = ("/dev/ttyUSB*", "/dev/ttyACM*")
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLO person seg → 64x64 → ESP32")
    p.add_argument("--port", default=None, help="Serial port (auto-detect if omitted)")
    p.add_argument("--baud", type=int, default=1_000_000, help="Baud rate")
    p.add_argument("--no-serial", action="store_true", help="Skip ESP32 send")
    p.add_argument("--camera", type=int, default=CAMERA_INDEX, help="Camera index")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()

    sender: SerialSender | None = None
    if not args.no_serial:
        port = args.port or autodetect_port()
        if port is None:
            print(
                "No serial port found. Plug in the ESP32 or pass --port, "
                "or run with --no-serial to skip TX.",
                file=sys.stderr,
            )
            return 2
        sender = SerialSender(port=port, baudrate=args.baud)
        print(f"Sending 64x64 masks to {port} @ {args.baud} baud. Ctrl-C to stop.")
    else:
        print("Serial disabled (--no-serial). CV pipeline only.")

    print(f"Loading {MODEL} (first run will download ~6 MB)...")
    model = YOLO(MODEL)

    cap = open_camera(args.camera)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    paused = False
    last_frame: np.ndarray | None = None
    start = time.time()
    n_frames = 0
    last_tx = 0.0

    preview_win = "camera + contours"
    cv2.namedWindow(preview_win)
    cv2.createTrackbar("Conf %", preview_win, 40, 95, lambda _: None)
    cv2.setTrackbarMin("Conf %", preview_win, 5)

    print("Press q to quit, s to save 64x64, SPACE to pause. Drag the Conf % slider to tweak YOLO confidence.")

    try:
        while True:
            if not paused:
                ok, frame = cap.read()
                if not ok:
                    print("Camera read failed", file=sys.stderr)
                    break
                last_frame = frame
            else:
                frame = last_frame.copy()

            h, w = frame.shape[:2]
            confidence = cv2.getTrackbarPos("Conf %", preview_win) / 100.0

            results = model.predict(
                frame,
                classes=[PERSON_CLASS_ID],
                conf=confidence,
                imgsz=INFER_IMGSZ,
                verbose=False,
            )
            result = results[0]

            silhouette = np.zeros((h, w), dtype=np.uint8)
            boxes_xyxy: list[tuple[int, int, int, int]] = []
            if result.masks is not None and result.boxes is not None:
                masks = result.masks.data.cpu().numpy()
                boxes = result.boxes.xyxy.cpu().numpy().astype(int)
                for m, (x1, y1, x2, y2) in zip(masks, boxes):
                    if m.shape[0] != h or m.shape[1] != w:
                        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
                    silhouette = np.maximum(
                        silhouette,
                        (m > 0.5).astype(np.uint8) * 255,
                    )
                    boxes_xyxy.append((x1, y1, x2, y2))

            silhouette = cv2.morphologyEx(
                silhouette, cv2.MORPH_CLOSE, kernel, iterations=1
            )

            contours, _ = cv2.findContours(
                silhouette, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            min_area = 0.002 * h * w
            people = [c for c in contours if cv2.contourArea(c) >= min_area]

            clean = np.zeros_like(silhouette)
            cv2.drawContours(clean, people, -1, 255, thickness=cv2.FILLED)

            preview = frame.copy()
            cv2.drawContours(preview, people, -1, (0, 255, 0), 2)
            for (x1, y1, x2, y2) in boxes_xyxy:
                cv2.rectangle(preview, (x1, y1), (x2, y2), (255, 128, 0), 1)

            small = cv2.resize(
                clean, (OUTPUT_SIZE, OUTPUT_SIZE), interpolation=cv2.INTER_AREA
            )
            _, small = cv2.threshold(small, 96, 255, cv2.THRESH_BINARY)
            chunky = cv2.resize(small, (h, h), interpolation=cv2.INTER_NEAREST)

            # Throttle TX to ~30 fps; preview keeps running unthrottled.
            now = time.time()
            if sender is not None and not paused and (now - last_tx) >= TX_MIN_INTERVAL_S:
                mask01 = (small > 0).astype(np.uint8)
                sender.send_frame(mask01)
                last_tx = now

            n_frames += 1
            fps = n_frames / max(time.time() - start, 1e-6)
            tx_line = ""
            if sender is not None:
                tx_line = f"  tx ack: {sender.frames_acked} nak: {sender.frames_naked}"
            cv2.putText(
                preview,
                f"people: {len(people)}  conf: {confidence:.2f}  fps: {fps:.1f}{tx_line}"
                + ("  [PAUSED]" if paused else ""),
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )

            cv2.imshow(preview_win, preview)
            cv2.imshow("silhouette", clean)
            cv2.imshow("64x64 pixelated", chunky)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("s"):
                cv2.imwrite("silhouette_64.png", small)
                print("saved silhouette_64.png")
            elif key == ord(" "):
                paused = not paused
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if sender is not None:
            sent = sender.frames_sent
            ack = sender.frames_acked
            nak = sender.frames_naked
            rate = (ack / sent) if sent else 0.0
            sender.close()
            print(f"sent={sent} ack={ack} nak={nak} success_rate={rate:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

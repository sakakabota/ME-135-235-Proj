"""Unified vision pipeline: YOLO person silhouette + MediaPipe fingertip tracking → ESP32.

Runs both pipelines every frame. Sends binary masks (mode 0) or fingertip positions
(mode 1) based on the ESP32's current mode, toggled by a physical button on the ESP32.
Mode-change notifications (0x10/0x11) from the ESP32 switch the TX format automatically.

Setup:
    pip install opencv-python numpy ultralytics mediapipe pyserial

Controls:
    q          quit
    s          save current 64x64 frame as silhouette_64.png
    SPACE      pause / resume
    Conf %     trackbar — YOLO confidence threshold (5-95%)

Modes (toggled by ESP32 button):
    Mode 0 — Kyle/pot: YOLO silhouette → ESP32 renders with pot-controlled white→red lerp
    Mode 1 — Wen/fingers: MediaPipe fingertip dots, no silhouette
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
import time

import cv2
import mediapipe as mp
import numpy as np
from ultralytics import YOLO

from serial_protocol import (
    Fingertip,
    MODE_MASK,
    MODE_FINGERTIPS,
    SerialSender,
)

CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
OUTPUT_SIZE = 64
PIXEL_SCALE = 12

MODEL = "yolov8n-seg.pt"
PERSON_CLASS_ID = 0
INFER_IMGSZ = 640

TX_MIN_INTERVAL_S = 1.0 / 30.0

TIP_IDS = [4, 8, 12, 16, 20]
TIP_COLORS = [
    (0, 0, 255),
    (0, 255, 0),
    (255, 0, 0),
    (0, 255, 255),
    (255, 0, 255),
]
COLOR_FLIP_SPEED = 3

# ---- MediaPipe ----
mp_hands = mp.solutions.hands


def open_camera(index: int) -> cv2.VideoCapture:
    for backend in (cv2.CAP_AVFOUNDATION, cv2.CAP_V4L2, cv2.CAP_ANY):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            return cap
        cap.release()
    raise RuntimeError(f"Could not open camera index {index}")


def autodetect_port() -> str | None:
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
    p = argparse.ArgumentParser(description="YOLO + MediaPipe → 64×64 → ESP32")
    p.add_argument("--port", default=None, help="Serial port (auto-detect if omitted)")
    p.add_argument("--baud", type=int, default=1_000_000, help="Baud rate")
    p.add_argument("--no-serial", action="store_true", help="Skip ESP32 send")
    p.add_argument("--camera", type=int, default=CAMERA_INDEX, help="Camera index")
    return p.parse_args()


def extract_fingertips(hand_results, shift: int) -> list[Fingertip]:
    """Extract fingertip positions + colors from MediaPipe results."""
    tips: list[Fingertip] = []
    if not hand_results.multi_hand_landmarks:
        return tips

    for hand_landmarks in hand_results.multi_hand_landmarks:
        for i, tip_id in enumerate(TIP_IDS):
            lm = hand_landmarks.landmark[tip_id]
            x = int(lm.x * OUTPUT_SIZE)
            y = int(lm.y * OUTPUT_SIZE)
            if 0 <= x < OUTPUT_SIZE and 0 <= y < OUTPUT_SIZE:
                color = TIP_COLORS[(i + shift) % len(TIP_COLORS)]
                tips.append(Fingertip(x=x, y=y, r=color[2], g=color[1], b=color[0]))
    return tips


def draw_fingertips_camera(frame, hand_results, shift: int):
    if not hand_results.multi_hand_landmarks:
        return
    h, w = frame.shape[:2]
    for hand_landmarks in hand_results.multi_hand_landmarks:
        for i, tip_id in enumerate(TIP_IDS):
            lm = hand_landmarks.landmark[tip_id]
            x, y = int(lm.x * w), int(lm.y * h)
            color = TIP_COLORS[(i + shift) % len(TIP_COLORS)]
            cv2.circle(frame, (x, y), 10, color, -1)
            cv2.circle(frame, (x, y), 18, color, 2)


def draw_fingertips_grid(fingertips: list[Fingertip], grid: np.ndarray):
    for ft in fingertips:
        if 0 <= ft.x < OUTPUT_SIZE and 0 <= ft.y < OUTPUT_SIZE:
            grid[ft.y, ft.x] = (ft.b, ft.g, ft.r)


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
        print(f"Sending to {port} @ {args.baud} baud. Mode toggled by ESP32 button. Ctrl-C to stop.")
    else:
        print("Serial disabled (--no-serial). CV pipeline only.")

    cap: cv2.VideoCapture | None = None
    hands = None
    try:
        print(f"Loading {MODEL}...")
        model = YOLO(MODEL)

        hands = mp_hands.Hands(
            max_num_hands=2,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6,
        )

        cap = open_camera(args.camera)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    except Exception:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        if hands is not None:
            hands.close()
        if sender is not None:
            sender.close()
        raise

    paused = False
    last_frame: np.ndarray | None = None
    start = time.time()
    n_frames = 0
    last_tx = 0.0
    current_mode = 0

    preview_win = "camera + contours"
    cv2.namedWindow(preview_win)
    cv2.createTrackbar("Conf %", preview_win, 40, 95, lambda _: None)
    cv2.setTrackbarMin("Conf %", preview_win, 5)

    print("Press q to quit, s to save 64x64, SPACE to pause. Mode toggled by ESP32 button.")

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
            color_shift = int(time.time() * COLOR_FLIP_SPEED)

            # --- MediaPipe hands ---
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hand_results = hands.process(rgb)
            fingertips = extract_fingertips(hand_results, color_shift)

            # --- YOLO person silhouette ---
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

            draw_fingertips_camera(preview, hand_results, color_shift)

            # --- 64×64 output ---
            small_mask = cv2.resize(
                clean, (OUTPUT_SIZE, OUTPUT_SIZE), interpolation=cv2.INTER_AREA
            )
            _, small_mask = cv2.threshold(small_mask, 96, 255, cv2.THRESH_BINARY)

            # Build preview grid: mode 0 = white mask, mode 1 = fingertips on black
            if current_mode == MODE_MASK:
                small_rgb = np.stack([small_mask] * 3, axis=-1)
            else:
                small_rgb = np.zeros((OUTPUT_SIZE, OUTPUT_SIZE, 3), dtype=np.uint8)
                draw_fingertips_grid(fingertips, small_rgb)

            chunky = cv2.resize(
                small_rgb,
                (OUTPUT_SIZE * PIXEL_SCALE, OUTPUT_SIZE * PIXEL_SCALE),
                interpolation=cv2.INTER_NEAREST,
            )

            # --- Serial TX ---
            now = time.time()
            if sender is not None:
                # Check for mode changes from ESP32
                new_mode = sender.read_mode_change()
                if new_mode is not None:
                    current_mode = new_mode

                if not paused and (now - last_tx) >= TX_MIN_INTERVAL_S:
                    if current_mode == MODE_MASK:
                        mask01 = (small_mask > 0).astype(np.uint8)
                        sender.send_mask(mask01)
                    else:
                        sender.send_fingertips(fingertips)
                    last_tx = now

            n_frames += 1
            fps = n_frames / max(time.time() - start, 1e-6)
            tx_line = ""
            mode_label = "Kyle/pot" if current_mode == MODE_MASK else "Wen/fingers"
            if sender is not None:
                tx_line = f"  tx ack:{sender.frames_acked} nak:{sender.frames_naked}"
            cv2.putText(
                preview,
                f"people: {len(people)}  conf: {confidence:.2f}  fps: {fps:.1f}"
                f"  mode: {mode_label}{tx_line}"
                + ("  [PAUSED]" if paused else ""),
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
            )

            cv2.imshow(preview_win, preview)
            cv2.imshow("silhouette", clean)
            cv2.imshow("64x64 pixel board", chunky)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("s"):
                cv2.imwrite("silhouette_64.png", small_mask)
                print("saved silhouette_64.png")
            elif key == ord(" "):
                paused = not paused
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        if hands is not None:
            hands.close()
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

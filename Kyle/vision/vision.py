"""Standalone vision script YOLO person silhouette no serial 

Grabs a frame runs YOLOv8 seg filtered to the person class ORs the per person
masks into one binary silhouette downsizes to 64x64 Trackbar for confidence 

 q quit
 s save current 64x64 mask as silhouette_64 png
 SPC pause resume
"""

from __future__ import annotations

import sys
import time

import cv2
import numpy as np
from ultralytics import YOLO

CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
OUTPUT_SIZE = 64

MODEL = "yolov8n-seg.pt"
PERSON_CLASS_ID = 0  # COCO person 
INFER_IMGSZ = 640


def open_camera(index):
    for backend in (cv2.CAP_AVFOUNDATION, cv2.CAP_V4L2, cv2.CAP_ANY):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            return cap
        cap.release()
    raise RuntimeError(f"Could not open camera index {index}")


def main():
    print(f"Loading {MODEL} (first run downloads ~6 MB)...")
    model = YOLO(MODEL)

    cap = open_camera(CAMERA_INDEX)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    paused = False
    last_frame = None
    start = time.time()
    n = 0

    win = "camera + contours"
    cv2.namedWindow(win)
    cv2.createTrackbar("Conf %", win, 40, 95, lambda _: None)
    cv2.setTrackbarMin("Conf %", win, 5)

    print("q quit, s save 64x64, SPACE pause. Drag the Conf % slider for YOLO confidence.")

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
        conf = cv2.getTrackbarPos("Conf %", win) / 100.0

        res = model.predict(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=conf,
            imgsz=INFER_IMGSZ,
            verbose=False,
        )[0]

        # OR all person masks into one silhouette
        sil = np.zeros((h, w), dtype=np.uint8)
        boxes_xyxy = []
        if res.masks is not None and res.boxes is not None:
            masks = res.masks.data.cpu().numpy()
            boxes = res.boxes.xyxy.cpu().numpy().astype(int)
            for m, (x1, y1, x2, y2) in zip(masks, boxes):
                if m.shape[0] != h or m.shape[1] != w:
                    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
                sil = np.maximum(sil, (m > 0.5).astype(np.uint8) * 255)
                boxes_xyxy.append((x1, y1, x2, y2))

        # small close to smooth jagged edges
        sil = cv2.morphologyEx(sil, cv2.MORPH_CLOSE, kernel, iterations=1)

        contours, _ = cv2.findContours(sil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = 0.002 * h * w  # 0 2 of frame
        people = [c for c in contours if cv2.contourArea(c) >= min_area]

        clean = np.zeros_like(sil)
        cv2.drawContours(clean, people, -1, 255, thickness=cv2.FILLED)

        preview = frame.copy()
        cv2.drawContours(preview, people, -1, (0, 255, 0), 2)
        for (x1, y1, x2, y2) in boxes_xyxy:
            cv2.rectangle(preview, (x1, y1), (x2, y2), (255, 128, 0), 1)

        # downsize to 64x64 the pixelation
        small = cv2.resize(clean, (OUTPUT_SIZE, OUTPUT_SIZE), interpolation=cv2.INTER_AREA)
        _, small = cv2.threshold(small, 96, 255, cv2.THRESH_BINARY)
        chunky = cv2.resize(small, (h, h), interpolation=cv2.INTER_NEAREST)

        n += 1
        fps = n / max(time.time() - start, 1e-6)
        cv2.putText(
            preview,
            f"people: {len(people)}  conf: {conf:.2f}  fps: {fps:.1f}"
            + ("  [PAUSED]" if paused else ""),
            (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
        )

        cv2.imshow(win, preview)
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

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())

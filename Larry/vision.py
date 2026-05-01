"""Human silhouette + 64x64 pixelation from a USB camera feed.

Detection: YOLOv8 instance segmentation, filtered to the "person" class.
    - Real, class-aware person detector (rejects non-people automatically).
    - Works on a still frame -- no motion required.
    - Per-instance masks, so one clean silhouette per visible person.
    - First run downloads a ~6 MB checkpoint into the working directory
      (yolov8n-seg.pt) and reuses it forever after.

Pipeline per frame:
    1. Grab a frame from a USB camera.
    2. YOLO predicts class + mask for every object; we filter to people.
    3. OR all the person masks together into one binary silhouette.
    4. findContours, drop tiny stray bits, redraw clean silhouette.
    5. Draw contours + boxes on the live preview.
    6. Downscale silhouette to 64x64 (the downsize IS the pixelation).

Setup:
    pip install opencv-python numpy ultralytics
    # ultralytics pulls PyTorch; first model load also downloads yolov8n-seg.pt.

Controls:
    q       quit
    s       save current 64x64 frame as silhouette_64.png
    SPACE   pause / resume
    [ / ]   decrease / increase YOLO confidence threshold
"""

from __future__ import annotations

import sys
import time
from ultralytics import YOLO
import cv2
import numpy as np

CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
OUTPUT_SIZE = 64

# Bigger = slower + more accurate. yolov8n-seg.pt is the smallest/fastest.
# Other options: yolov8s-seg.pt, yolov8m-seg.pt, yolov8l-seg.pt, yolov8x-seg.pt
MODEL = "yolov8n-seg.pt"
PERSON_CLASS_ID = 0  # COCO 'person'
INFER_IMGSZ = 640 # input size YOLO runs at (auto letterboxed)


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


def main() -> int:
    print(f"Loading {MODEL} (first run will download ~6 MB)...")
    model = YOLO(MODEL)

    cap = open_camera(CAMERA_INDEX)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    confidence = 0.40
    paused = False
    last_frame: np.ndarray | None = None
    start = time.time()
    n_frames = 0

    print("Press q to quit, s to save 64x64, [ / ] to tweak confidence, SPACE to pause.")

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

        # Run YOLO and keep only the person class
        results = model.predict(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=confidence,
            imgsz=INFER_IMGSZ,
            verbose=False,
        )
        result = results[0]

        # Combine all per-person masks into one silhouette.
        silhouette = np.zeros((h, w), dtype=np.uint8)
        boxes_xyxy: list[tuple[int, int, int, int]] = []
        if result.masks is not None and result.boxes is not None:
            masks = result.masks.data.cpu().numpy()              # (N, mh, mw)
            boxes = result.boxes.xyxy.cpu().numpy().astype(int)  # (N, 4)
            for m, (x1, y1, x2, y2) in zip(masks, boxes):
                if m.shape[0] != h or m.shape[1] != w:
                    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
                silhouette = np.maximum(
                    silhouette,
                    (m > 0.5).astype(np.uint8) * 255,
                )
                boxes_xyxy.append((x1, y1, x2, y2))

        # Tiny morphological close to smooth jagged mask edges
        silhouette = cv2.morphologyEx(
            silhouette, cv2.MORPH_CLOSE, kernel, iterations=1
        )

        # findContours for the preview + filter out tiny stray bits
        contours, _ = cv2.findContours(
            silhouette, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        min_area = 0.002 * h * w  # 0.2% of frame
        people = [c for c in contours if cv2.contourArea(c) >= min_area]

        clean = np.zeros_like(silhouette)
        cv2.drawContours(clean, people, -1, 255, thickness=cv2.FILLED)

        preview = frame.copy()
        cv2.drawContours(preview, people, -1, (0, 255, 0), 2)
        for (x1, y1, x2, y2) in boxes_xyxy:
            cv2.rectangle(preview, (x1, y1), (x2, y2), (255, 128, 0), 1)

        # Downsize to 64x64 so match led display
        small = cv2.resize(
            clean, (OUTPUT_SIZE, OUTPUT_SIZE), interpolation=cv2.INTER_AREA
        )
        _, small = cv2.threshold(small, 96, 255, cv2.THRESH_BINARY)
        chunky = cv2.resize(small, (h, h), interpolation=cv2.INTER_NEAREST)

        n_frames += 1
        fps = n_frames / max(time.time() - start, 1e-6)
        cv2.putText(
            preview,
            f"people: {len(people)}  conf: {confidence:.2f}  fps: {fps:.1f}"
            + ("  [PAUSED]" if paused else ""),
            (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
        )

        cv2.imshow("camera + contours", preview)
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
        elif key == ord("["):
            confidence = max(0.05, confidence - 0.05)
        elif key == ord("]"):
            confidence = min(0.95, confidence + 0.05)

    cap.release()
    cv2.destroyAllWindows()
    return 0

if __name__ == "__main__":
    sys.exit(main())

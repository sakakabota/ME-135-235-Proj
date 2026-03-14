"""
ME135 Human Detection — CPU-based Computer Vision Pipeline
==========================================================
Captures frames from the PS3 Eye camera, performs background subtraction,
and produces a 400×300 binary matrix (0 = background, 1 = human).

This module is the **CPU fallback**. For CUDA-accelerated processing,
see gpu_accelerated.py which mirrors this API.

Public API (used by main.py):
    - CVPipeline(config)         — constructor, takes parsed config dict
    - pipeline.calibrate()       — build background model (blocking)
    - pipeline.process_frame()   — returns (binary_matrix, raw_frame)
    - pipeline.release()         — release camera resources
"""

import cv2
import numpy as np
import logging

logger = logging.getLogger("me135.cv_pipeline")


class CVPipeline:
    """CPU-based human detection pipeline."""

    def __init__(self, config: dict):
        cam_cfg = config["camera"]
        cal_cfg = config["calibration"]
        proc_cfg = config["processing"]

        # ---- Camera setup ----
        self.cap = cv2.VideoCapture(cam_cfg["device_index"])
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam_cfg["capture_width"])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam_cfg["capture_height"])
        self.cap.set(cv2.CAP_PROP_FPS, cam_cfg["capture_fps"])
        self._warmup_frames = cam_cfg["warmup_frames"]

        # ---- Processing parameters ----
        self.out_w = proc_cfg["output_width"]     # 400
        self.out_h = proc_cfg["output_height"]    # 300
        self.threshold = proc_cfg["threshold"]
        self.morph_k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (proc_cfg["morph_kernel_size"], proc_cfg["morph_kernel_size"]),
        )
        self.min_area = proc_cfg["min_contour_area"]
        self.blur_k = proc_cfg["gaussian_blur_ksize"]

        # ---- Background subtractor ----
        method = cal_cfg["method"]
        if method == "mog2":
            self.bg_sub = cv2.createBackgroundSubtractorMOG2(
                history=cal_cfg["mog2_history"],
                varThreshold=cal_cfg["mog2_var_threshold"],
                detectShadows=cal_cfg["mog2_detect_shadows"],
            )
        elif method == "knn":
            self.bg_sub = cv2.createBackgroundSubtractorKNN(
                history=cal_cfg["knn_history"],
                dist2Threshold=cal_cfg["knn_dist2_threshold"],
                detectShadows=cal_cfg["knn_detect_shadows"],
            )
        elif method == "static_median":
            self.bg_sub = None  # Will be built during calibrate()
            self._bg_model = None
        else:
            raise ValueError(f"Unknown calibration method: {method}")

        self._method = method
        self._cal_frames = cal_cfg["num_frames"]
        self._calibrated = False

        logger.info(
            "CVPipeline (CPU) initialised — method=%s, output=%dx%d",
            method, self.out_w, self.out_h,
        )

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------
    def calibrate(self) -> None:
        """Capture calibration frames and build the background model."""
        logger.info("Starting calibration (%d warmup + %d model frames)…",
                     self._warmup_frames, self._cal_frames)

        # Discard warmup frames (auto-exposure settling)
        for _ in range(self._warmup_frames):
            self.cap.read()

        if self._method == "static_median":
            buf = []
            for i in range(self._cal_frames):
                ret, frame = self.cap.read()
                if not ret:
                    raise RuntimeError("Camera read failed during calibration")
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                buf.append(gray)
            self._bg_model = np.median(np.array(buf), axis=0).astype(np.uint8)
            logger.info("Static median background model built.")
        else:
            # Feed frames into MOG2/KNN so it learns the background
            for i in range(self._cal_frames):
                ret, frame = self.cap.read()
                if not ret:
                    raise RuntimeError("Camera read failed during calibration")
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                self.bg_sub.apply(gray, learningRate=0.05)

            logger.info("Background subtractor trained (%s).", self._method)

        self._calibrated = True

    # ------------------------------------------------------------------
    # Per-frame processing
    # ------------------------------------------------------------------
    def process_frame(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """
        Capture one frame, apply background subtraction, return binary matrix.

        Returns
        -------
        binary_matrix : np.ndarray of shape (300, 400), dtype uint8, values {0,1}
            None if capture failed.
        raw_frame : np.ndarray
            Original BGR frame (for debug display). None if capture failed.
        """
        if not self._calibrated:
            raise RuntimeError("Pipeline not calibrated — call calibrate() first")

        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Camera read failed")
            return None, None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (self.blur_k, self.blur_k), 0)

        # --- Foreground mask ---
        if self._method == "static_median":
            diff = cv2.absdiff(gray, self._bg_model)
            _, fg_mask = cv2.threshold(diff, self.threshold, 255, cv2.THRESH_BINARY)
        else:
            fg_mask = self.bg_sub.apply(gray, learningRate=0.002)  # small rate lets model adapt to slow lighting drift
            # MOG2 shadow pixels = 127; threshold to remove
            _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        # --- Sanity check: >60% foreground means stale background model ---
        if np.mean(fg_mask > 0) > 0.6:
            logger.warning("Foreground overflow (%.0f%% lit) — recalibrate or lock camera exposure",
                           np.mean(fg_mask > 0) * 100)
            return np.zeros((self.out_h, self.out_w), dtype=np.uint8), frame

        # --- Morphological clean-up ---
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.morph_k)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.morph_k)

        # --- Remove small blobs ---
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        clean_mask = np.zeros_like(fg_mask)
        for cnt in contours:
            if cv2.contourArea(cnt) >= self.min_area:
                cv2.drawContours(clean_mask, [cnt], -1, 255, thickness=cv2.FILLED)

        # --- Resize to output dimensions ---
        resized = cv2.resize(
            clean_mask, (self.out_w, self.out_h), interpolation=cv2.INTER_NEAREST
        )

        # --- Convert to binary 0/1 ---
        binary_matrix = (resized > 0).astype(np.uint8)

        return binary_matrix, frame

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False  # Don't suppress exceptions

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def release(self) -> None:
        """Release camera and resources."""
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info("Camera released.")

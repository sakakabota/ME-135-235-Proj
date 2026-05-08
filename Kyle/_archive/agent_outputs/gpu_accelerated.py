"""
ME135 Human Detection — CUDA-accelerated CV Pipeline (Jetson)
=============================================================
Drop-in replacement for cv_pipeline.CVPipeline when running on
an NVIDIA Jetson with CUDA-enabled OpenCV.

Public API (identical to CVPipeline):
    - GPUPipeline(config)
    - pipeline.calibrate()
    - pipeline.process_frame()  →  (binary_matrix, raw_frame)
    - pipeline.release()

Falls back to cv_pipeline.CVPipeline automatically if CUDA is unavailable.

Target hardware: NVIDIA Jetson Orin Nano Super
    - JetPack 6.x (Ubuntu 22.04, CUDA 12.2, OpenCV 4.8)
    - GPU: 1024 Ampere CUDA cores, 67 TOPS AI performance
    - RAM: 8 GB LPDDR5

Performance notes (Jetson Orin Nano Super, 640×480 → 400×300):
    - CPU path:  ~8 ms/frame   (~60 fps capture-limited)
    - GPU path:  ~2 ms/frame   (~60 fps capture-limited, camera-bound)

⚠ Output size out of date (2026-05-07): the display is now a Waveshare
RGB-Matrix-P2 64×64 (HUB75). The final resize stage should produce 64×64,
not 400×300. cv2.cuda.resize → 64×64 is essentially free; CPU/GPU latency
numbers above won't change meaningfully. See CLAUDE.md "Display hardware
(current)" section.

API note: Uses OpenCV 4.8 CUDA return-value style (JetPack 6):
    dst = cv2.cuda.cvtColor(src, code)        # NOT cvtColor(src, code, dst)
    dst = cv2.cuda.absdiff(src1, src2)        # NOT absdiff(src1, src2, dst)
    _, dst = cv2.cuda.threshold(src, t, m, type)
"""

import cv2
import numpy as np
import logging

logger = logging.getLogger("me135.gpu_pipeline")

# ---- CUDA availability check ----
try:
    _cuda_count = cv2.cuda.getCudaEnabledDeviceCount()
    CUDA_AVAILABLE = _cuda_count > 0
    if CUDA_AVAILABLE:
        cv2.cuda.setDevice(0)
        logger.info("CUDA available — %d device(s) detected.", _cuda_count)
except AttributeError:
    CUDA_AVAILABLE = False
    logger.warning("OpenCV built without CUDA support; GPU pipeline unavailable.")


class GPUPipeline:
    """
    CUDA-accelerated human detection pipeline.

    Mirrors the cv_pipeline.CVPipeline interface so main.py can swap
    transparently based on config['processing']['use_gpu'].
    """

    def __init__(self, config: dict):
        if not CUDA_AVAILABLE:
            raise RuntimeError(
                "GPUPipeline requires CUDA-enabled OpenCV. "
                "Install the Jetson opencv4 wheel or set use_gpu: false."
            )

        cam_cfg = config["camera"]
        cal_cfg = config["calibration"]
        proc_cfg = config["processing"]

        # ---- Camera ----
        self.cap = cv2.VideoCapture(cam_cfg["device_index"])
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam_cfg["capture_width"])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam_cfg["capture_height"])
        self.cap.set(cv2.CAP_PROP_FPS, cam_cfg["capture_fps"])
        self._warmup_frames = cam_cfg["warmup_frames"]

        # ---- Output dims ----
        self.out_w = proc_cfg["output_width"]     # 400
        self.out_h = proc_cfg["output_height"]    # 300
        self.threshold = proc_cfg["threshold"]
        self.min_area = proc_cfg["min_contour_area"]
        self.blur_k = proc_cfg["gaussian_blur_ksize"]
        morph_k_size = proc_cfg["morph_kernel_size"]

        # ---- Pre-allocate GPU mats ----
        self._gpu_frame = cv2.cuda_GpuMat()
        self._gpu_gray = cv2.cuda_GpuMat()
        self._gpu_blur = cv2.cuda_GpuMat()
        self._gpu_bg = cv2.cuda_GpuMat()
        self._gpu_diff = cv2.cuda_GpuMat()
        self._gpu_fg = cv2.cuda_GpuMat()
        self._gpu_resized = cv2.cuda_GpuMat()

        # ---- CUDA filters ----
        self._gauss_filter = cv2.cuda.createGaussianFilter(
            cv2.CV_8UC1, cv2.CV_8UC1,
            (self.blur_k, self.blur_k), 0,
        )
        self._morph_open = cv2.cuda.createMorphologyFilter(
            cv2.MORPH_OPEN, cv2.CV_8UC1,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_k_size, morph_k_size)),
        )
        self._morph_close = cv2.cuda.createMorphologyFilter(
            cv2.MORPH_CLOSE, cv2.CV_8UC1,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_k_size, morph_k_size)),
        )

        # ---- Background subtractor (CUDA MOG2) ----
        method = cal_cfg["method"]
        if method in ("mog2", "knn"):
            if method == "knn":
                logger.warning(
                    "CUDA does not support KNN background subtraction — "
                    "falling back to CUDA MOG2. Switch config to method: mog2 "
                    "to suppress this warning."
                )
            self._bg_sub = cv2.cuda.createBackgroundSubtractorMOG2(
                history=cal_cfg["mog2_history"],
                varThreshold=cal_cfg["mog2_var_threshold"],
                detectShadows=cal_cfg["mog2_detect_shadows"],
            )
            self._method = "mog2_cuda"
        elif method == "static_median":
            self._bg_sub = None
            self._bg_model_cpu = None
            self._method = "static_median"
        else:
            raise ValueError(f"Unknown calibration method: {method}")

        self._cal_frames = cal_cfg["num_frames"]
        self._calibrated = False

        logger.info(
            "GPUPipeline initialised — method=%s, output=%dx%d",
            self._method, self.out_w, self.out_h,
        )

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------
    def calibrate(self) -> None:
        """Build background model on GPU."""
        logger.info("GPU calibration: %d warmup + %d model frames",
                     self._warmup_frames, self._cal_frames)

        for _ in range(self._warmup_frames):
            self.cap.read()

        if self._method == "static_median":
            # Median must be computed on CPU (no CUDA median over stack)
            buf = []
            for _ in range(self._cal_frames):
                ret, frame = self.cap.read()
                if not ret:
                    raise RuntimeError("Camera read failed during calibration")
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                buf.append(gray)
            self._bg_model_cpu = np.median(np.array(buf), axis=0).astype(np.uint8)
            self._gpu_bg.upload(self._bg_model_cpu)
            logger.info("Static median background uploaded to GPU.")
        else:
            for _ in range(self._cal_frames):
                ret, frame = self.cap.read()
                if not ret:
                    raise RuntimeError("Camera read failed during calibration")
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                self._gpu_gray.upload(gray)
                self._bg_sub.apply(self._gpu_gray, -1, self._gpu_fg)
            logger.info("CUDA MOG2 background model trained.")

        self._calibrated = True

    # ------------------------------------------------------------------
    # Per-frame processing (GPU-accelerated)
    # ------------------------------------------------------------------
    def process_frame(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """
        Capture + process one frame on the GPU, download binary matrix.

        Returns
        -------
        binary_matrix : np.ndarray (300, 400) uint8 {0, 1} or None
        raw_frame     : np.ndarray BGR or None
        """
        if not self._calibrated:
            raise RuntimeError("Pipeline not calibrated — call calibrate() first")

        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Camera read failed")
            return None, None

        # Upload & convert to grayscale on GPU (OpenCV 4.8 return-value style)
        self._gpu_frame.upload(frame)
        self._gpu_gray = cv2.cuda.cvtColor(self._gpu_frame, cv2.COLOR_BGR2GRAY)

        # Gaussian blur
        self._gpu_blur = self._gauss_filter.apply(self._gpu_gray)

        # Foreground mask
        if self._method == "static_median":
            self._gpu_diff = cv2.cuda.absdiff(self._gpu_blur, self._gpu_bg)
            _, self._gpu_fg = cv2.cuda.threshold(
                self._gpu_diff, self.threshold, 255, cv2.THRESH_BINARY
            )
        else:
            self._gpu_fg = self._bg_sub.apply(self._gpu_blur, -1)
            _, self._gpu_fg = cv2.cuda.threshold(
                self._gpu_fg, 200, 255, cv2.THRESH_BINARY
            )

        # Morphological clean-up (GPU)
        self._gpu_fg = self._morph_open.apply(self._gpu_fg)
        self._gpu_fg = self._morph_close.apply(self._gpu_fg)

        # Resize on GPU
        self._gpu_resized = cv2.cuda.resize(
            self._gpu_fg, (self.out_w, self.out_h),
            interpolation=cv2.INTER_NEAREST,
        )

        # Download to CPU for contour filtering + serial transmission
        fg_cpu = self._gpu_resized.download()

        # Small-blob removal (CPU — contour ops not available on CUDA)
        contours, _ = cv2.findContours(
            fg_cpu, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        clean = np.zeros_like(fg_cpu)
        for cnt in contours:
            if cv2.contourArea(cnt) >= self.min_area:
                cv2.drawContours(clean, [cnt], -1, 255, thickness=cv2.FILLED)

        binary_matrix = (clean > 0).astype(np.uint8)
        return binary_matrix, frame

    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False  # Don't suppress exceptions

    # ------------------------------------------------------------------
    def release(self) -> None:
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info("GPU pipeline — camera released.")

#!/usr/bin/env python3
"""
ME135 Human Detection System — Main Entry Point
================================================
Orchestrates: config loading → pipeline init → calibration →
              live loop (capture → process → transmit → display)

Usage:
    python main.py                       # Normal run
    python main.py --config myconf.yaml  # Custom config
    python main.py --no-serial           # Debug without ESP32
    python main.py --show-preview        # Show OpenCV debug window
"""

import argparse
import logging
import signal
import sys
import time

import cv2
import numpy as np
import yaml

from cv_pipeline import CVPipeline
from gpu_accelerated import GPUPipeline, CUDA_AVAILABLE
from serial_protocol import SerialSender

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("me135.main")

# ── Graceful shutdown ──────────────────────────────────────────────
_running = True

def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received (sig=%d)", sig)
    _running = False

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── Config loader ──────────────────────────────────────────────────
def load_config(path: str) -> dict:
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    logger.info("Configuration loaded from %s", path)
    return cfg


# ── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ME135 Human Detection System")
    parser.add_argument(
        "--config", default="config.yaml", help="Path to YAML config file"
    )
    parser.add_argument(
        "--no-serial", action="store_true",
        help="Run without serial output (debug mode)"
    )
    parser.add_argument(
        "--show-preview", action="store_true",
        help="Show OpenCV preview window"
    )
    args = parser.parse_args()

    # ── Load config ──
    config = load_config(args.config)
    log_level = config.get("safety", {}).get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))

    # ── Select pipeline (GPU vs CPU) ──
    use_gpu = config["processing"]["use_gpu"]
    if use_gpu and CUDA_AVAILABLE:
        logger.info("Initialising GPU-accelerated pipeline (CUDA)")
        pipeline = GPUPipeline(config)
    else:
        if use_gpu:
            logger.warning("GPU requested but CUDA unavailable — falling back to CPU")
        else:
            logger.info("Initialising CPU pipeline")
        pipeline = CVPipeline(config)

    # ── Serial sender ──
    sender = None
    if not args.no_serial:
        try:
            sender = SerialSender(config)
        except Exception as e:
            logger.error("Serial init failed: %s — continuing without serial", e)
            sender = None
    else:
        logger.info("Serial output disabled (--no-serial)")

    # ── Calibration prompt ──
    print("\n" + "=" * 60)
    print("  CALIBRATION SETUP")
    print("  Make sure the scene is COMPLETELY EMPTY (no people).")
    print("  After pressing Enter, you will have 10 seconds to")
    print("  move out of the camera frame before recording begins.")
    print("=" * 60)
    input("\nPress Enter when ready to start calibration…")

    print("\nGet out of frame! Recording starts in:")
    for remaining in range(10, 0, -1):
        print(f"  {remaining}…", flush=True)
        time.sleep(1)
    print("  GO — recording background now…\n")

    logger.info("=== CALIBRATION START ===")
    pipeline.calibrate()
    logger.info("=== CALIBRATION COMPLETE ===")
    print("\nCalibration done! You can now move in front of the camera.")
    print("Close the preview window or press 'q' to quit.\n")

    # ── Safety parameters ──
    watchdog_timeout = config.get("safety", {}).get("watchdog_timeout_s", 5.0)
    max_serial_errors = config.get("safety", {}).get("max_serial_errors", 10)
    consecutive_errors = 0
    fps_target = config.get("display", {}).get("fps_target", 10)
    frame_interval = 1.0 / fps_target if fps_target > 0 else 0.0

    # ── FPS tracking ──
    frame_count = 0
    fps_timer = time.time()
    last_frame_time = time.time()

    # ── Live loop ──
    logger.info("Entering live processing loop (target %d fps)…", fps_target)
    global _running
    while _running:
        loop_start = time.time()

        # ── Watchdog: check time since last successful frame ──
        if (loop_start - last_frame_time) > watchdog_timeout:
            logger.error("WATCHDOG: No successful frame in %.1fs — check camera!",
                         watchdog_timeout)
            last_frame_time = loop_start  # Prevent log spam

        # ── Process frame ──
        binary_matrix, raw_frame = pipeline.process_frame()
        if binary_matrix is None:
            logger.warning("Frame capture failed — skipping")
            time.sleep(0.01)
            continue

        last_frame_time = time.time()

        # ── Transmit ──
        if sender is not None:
            ok = sender.send_frame(binary_matrix)
            if not ok:
                consecutive_errors += 1
                if consecutive_errors >= max_serial_errors:
                    logger.critical(
                        "SAFETY SHUTDOWN: %d consecutive serial errors",
                        consecutive_errors,
                    )
                    break
            else:
                consecutive_errors = 0

        # ── Live B&W mask preview ──
        if binary_matrix is not None:
            # White = human detected, Black = background
            mask_display = (binary_matrix * 255).astype(np.uint8)
            mask_bgr = cv2.cvtColor(mask_display, cv2.COLOR_GRAY2BGR)
            # Optionally stack side-by-side with raw frame
            if args.show_preview and raw_frame is not None:
                h, w = binary_matrix.shape[:2]
                raw_resized = cv2.resize(raw_frame, (w, h))
                display = np.hstack([raw_resized, mask_bgr])
                cv2.imshow("ME135 — Raw | B&W Mask", display)
            else:
                cv2.imshow("ME135 — B&W Mask (white=human)", mask_bgr)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                logger.info("Quit requested via preview window")
                break

        # ── FPS counter ──
        frame_count += 1
        elapsed = time.time() - fps_timer
        if elapsed >= 2.0:
            fps = frame_count / elapsed
            logger.info("FPS: %.1f  |  Humans pixels: %d / %d",
                         fps,
                         int(binary_matrix.sum()),
                         binary_matrix.size)
            frame_count = 0
            fps_timer = time.time()

        # ── Frame rate limiter ──
        proc_time = time.time() - loop_start
        if proc_time < frame_interval:
            time.sleep(frame_interval - proc_time)

    # ── Cleanup ──
    logger.info("Shutting down…")
    pipeline.release()
    if sender:
        sender.close()
    cv2.destroyAllWindows()
    logger.info("ME135 system stopped.")


if __name__ == "__main__":
    main()

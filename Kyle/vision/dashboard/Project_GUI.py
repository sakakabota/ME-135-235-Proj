import sys
import os
import cv2
import numpy as np
import time
from pathlib import Path
from dotenv import load_dotenv
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QSlider, QFrame, QPushButton,
                             QGridLayout, QComboBox, QTextEdit, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QFont
from ultralytics import YOLO
from google import genai

# Make Kyle/vision/serial_protocol.py importable from this script-mode launch.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serial_protocol import Fingertip, MODE_MASK, MODE_FINGERTIPS  # noqa: E402

# SerialWorker lives next to this file
sys.path.insert(0, str(Path(__file__).resolve().parent))
from serial_worker import SerialWorker, list_serial_ports  # noqa: E402

# MediaPipe is optional — if missing, the GUI runs but fingertip mode emits empty tips.
try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    mp = None
    MEDIAPIPE_AVAILABLE = False

# Load environment variables from .env file
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# --- PALETTE (Retro Canva & Pixel Art Colors) ---
PRIMARY_BLUE = "#0148ff"
RETRO_ORANGE = "#ff5757"
BACKGROUND_CREAM = "#fdf5f0"
TEXT_DARK = "#454f00"
WHITE = "#FFFFFF"
BLACK = "#000000"

# --- Dashboard layout constants ---
PANEL_SIZE = 64
TX_INTERVAL_S = 1.0 / 30.0  # cap TX rate at 30 fps
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CHECKER_PATH = ASSETS_DIR / "checker_tile.png"

# MediaPipe fingertip landmark indices (thumb, index, middle, ring, pinky tips)
TIP_IDS = [4, 8, 12, 16, 20]
TIP_COLORS = [
    (0, 0, 255),
    (0, 255, 0),
    (255, 0, 0),
    (0, 255, 255),
    (255, 0, 255),
]
COLOR_FLIP_SPEED = 3


class VisionWorker(QThread):
    """Camera + YOLO + (optional) MediaPipe in a worker thread.

    Emits annotated, full-res silhouette, and 400×400 LED preview for display,
    plus a 64×64 binary mask and a list of Fingertips for serial TX. The GUI
    routes mask vs tips to SerialWorker based on the current ESP32 mode.
    """

    change_pixmap_signal = pyqtSignal(np.ndarray, np.ndarray, np.ndarray)
    status_signal = pyqtSignal(float, int)        # fps, people_count
    mask_ready_signal = pyqtSignal(np.ndarray)    # (64, 64) uint8 binary mask
    tips_ready_signal = pyqtSignal(list)          # list[Fingertip]

    def __init__(self):
        super().__init__()
        self._run_flag = True
        self._reset_flag = False
        self.conf_threshold = 0.5
        self.pixel_threshold = 127
        self.min_area_percent = 0.01
        # MediaPipe sensitivity knobs (used only if mediapipe available)
        self.hand_detection_conf = 0.6
        self.hand_tracking_conf = 0.6
        self.model = YOLO('yolov8n-seg.pt')
        self._hands = None
        self._hands_params = None  # (det_conf, track_conf) for the active hands

    def _ensure_hands(self):
        """Lazy-init MediaPipe hands; recreate if sensitivity knobs changed."""
        if not MEDIAPIPE_AVAILABLE:
            return None
        params = (self.hand_detection_conf, self.hand_tracking_conf)
        if self._hands is None or self._hands_params != params:
            if self._hands is not None:
                self._hands.close()
            self._hands = mp.solutions.hands.Hands(
                max_num_hands=2,
                min_detection_confidence=params[0],
                min_tracking_confidence=params[1],
            )
            self._hands_params = params
        return self._hands

    def run(self):
        cap = cv2.VideoCapture(0)
        prev_time = 0

        try:
            while self._run_flag:
                if self._reset_flag:
                    cap.release()
                    cap = cv2.VideoCapture(0)
                    self._reset_flag = False
                    time.sleep(0.5)
                    continue

                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                # YOLO Inference (person class only)
                results = self.model.predict(frame, conf=self.conf_threshold, verbose=False, classes=[0])

                annotated_frame = frame.copy()
                silhouette = np.zeros((frame.shape[0], frame.shape[1]), dtype=np.uint8)
                people_count = 0
                detection_label = "no detection"

                if results[0].masks is not None:
                    masks = results[0].masks.data.cpu().numpy()
                    people_count = len(masks)
                    combined_mask = np.zeros_like(silhouette)

                    if len(results[0].boxes) > 0:
                        max_conf = results[0].boxes.conf[0].item()
                        detection_label = f"person {max_conf:.2f}"

                    for mask in masks:
                        if np.sum(mask) > (self.min_area_percent * mask.size):
                            mask_resized = cv2.resize(mask, (frame.shape[1], frame.shape[0]))
                            combined_mask = cv2.bitwise_or(combined_mask, (mask_resized * 255).astype(np.uint8))

                    kernel = np.ones((5, 5), np.uint8)
                    silhouette = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)

                    for box in results[0].boxes.xyxy:
                        x1, y1, x2, y2 = map(int, box)
                        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 72, 1), 2)
                        cv2.putText(annotated_frame, detection_label, (x1, max(y1 - 5, 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 72, 1), 2)

                # 64x64 binary mask for the LED panel
                pixel_small = cv2.resize(silhouette, (PANEL_SIZE, PANEL_SIZE), interpolation=cv2.INTER_AREA)
                _, pixel_thresh = cv2.threshold(pixel_small, self.pixel_threshold, 255, cv2.THRESH_BINARY)
                mask01 = (pixel_thresh > 0).astype(np.uint8)
                pixel_preview = cv2.resize(pixel_thresh, (400, 400), interpolation=cv2.INTER_NEAREST)

                # MediaPipe fingertips (optional)
                tips: list[Fingertip] = []
                hands = self._ensure_hands()
                if hands is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    hand_results = hands.process(rgb)
                    if hand_results.multi_hand_landmarks:
                        color_shift = int(time.time() * COLOR_FLIP_SPEED)
                        for hand_landmarks in hand_results.multi_hand_landmarks:
                            for i, tip_id in enumerate(TIP_IDS):
                                lm = hand_landmarks.landmark[tip_id]
                                x = int(lm.x * PANEL_SIZE)
                                y = int(lm.y * PANEL_SIZE)
                                if 0 <= x < PANEL_SIZE and 0 <= y < PANEL_SIZE:
                                    color = TIP_COLORS[(i + color_shift) % len(TIP_COLORS)]
                                    tips.append(Fingertip(x=x, y=y, r=color[2], g=color[1], b=color[0]))
                                    cx = int(lm.x * frame.shape[1])
                                    cy = int(lm.y * frame.shape[0])
                                    cv2.circle(annotated_frame, (cx, cy), 8, color, -1)

                fps = 1 / (time.time() - prev_time + 1e-6)
                prev_time = time.time()

                self.change_pixmap_signal.emit(annotated_frame, silhouette, pixel_preview)
                self.status_signal.emit(fps, people_count)
                self.mask_ready_signal.emit(mask01)
                self.tips_ready_signal.emit(tips)
        finally:
            cap.release()
            if self._hands is not None:
                self._hands.close()

    def reset_camera(self):
        self._reset_flag = True

    def stop(self):
        self._run_flag = False
        self.wait()


class DraggableIcon(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._drag_start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            self.raise_()

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is not None:
            new_pos = self.pos() + event.position().toPoint() - self._drag_start_pos
            self.move(new_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = None


class PixelMirrorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PIXEL MIRROR | ME 135/235")
        self.setMinimumSize(1280, 1080)
        self.setStyleSheet(f"background-color: {BACKGROUND_CREAM};")

        self.current_raw_frame = None
        self.current_mode = MODE_MASK
        self.current_port: str | None = None
        self.connected = False
        self._tx_in_flight = False  # backpressure gate (see SerialWorker docstring)
        self._last_tx_at = 0.0
        # Cumulative link stats from SerialWorker; updated periodically
        self._stats_sent = 0
        self._stats_ack = 0
        self._stats_nak = 0
        self._link_fps_window = []  # rolling timestamps for live frames/sec

        # Vision + serial workers
        self.worker = VisionWorker()
        self.serial_worker = SerialWorker()

        self.init_ui()

        # Vision -> GUI display
        self.worker.change_pixmap_signal.connect(self.update_screens)
        self.worker.status_signal.connect(self.update_camera_status)
        # Vision -> serial routing
        self.worker.mask_ready_signal.connect(self.on_mask_ready)
        self.worker.tips_ready_signal.connect(self.on_tips_ready)

        # Serial worker -> GUI status
        self.serial_worker.connected_signal.connect(self.on_serial_connected)
        self.serial_worker.disconnected_signal.connect(self.on_serial_disconnected)
        self.serial_worker.mode_changed_signal.connect(self.on_mode_changed)
        self.serial_worker.link_stats_signal.connect(self.on_link_stats)
        self.serial_worker.error_signal.connect(self.on_serial_error)
        self.serial_worker.send_complete_signal.connect(self.on_send_complete)

        self.worker.start()
        self.refresh_ports()
        self.update_mode_ui()  # apply initial mode (mask) UI state

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def generate_checkerboard(self):
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        if CHECKER_PATH.exists():
            return
        tile_size = 40
        tile = np.full((tile_size, tile_size, 3), 255, dtype=np.uint8)
        blue = (255, 72, 1)  # BGR
        half = tile_size // 2
        tile[0:half, 0:half] = blue
        tile[half:tile_size, half:tile_size] = blue
        cv2.imwrite(str(CHECKER_PATH), tile)

    def init_ui(self):
        self.generate_checkerboard()
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {BACKGROUND_CREAM};")

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Borders ---
        checker_url = CHECKER_PATH.as_posix()
        top_border = QLabel()
        top_border.setFixedHeight(35)
        top_border.setStyleSheet(
            f"background-image: url('{checker_url}'); background-repeat: repeat-x;"
        )

        bottom_border = QLabel()
        bottom_border.setFixedHeight(35)
        bottom_border.setStyleSheet(
            f"background-image: url('{checker_url}'); background-repeat: repeat-x;"
        )

        # --- Content Container ---
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(40, 20, 40, 20)
        content_lay.setSpacing(15)

        # --- Header ---
        header = QVBoxLayout()
        title = QLabel("PIXEL MIRROR")
        title.setFont(QFont("Fixedsys", 56, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {PRIMARY_BLUE}; background: transparent; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_course = QLabel("ME 135/235")
        info_course.setFont(QFont("Fixedsys", 20))
        info_course.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        info_course.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_names = QLabel("STEPH AKAKABOTA • WEN CAO • KYLE NELSON • LARRY HUI")
        info_names.setFont(QFont("Fixedsys", 16))
        info_names.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        info_names.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header.addWidget(title)
        header.addWidget(info_course)
        header.addWidget(info_names)
        content_lay.addLayout(header)

        # --- Main Viewports (Three Parallel Rectangles) ---
        viewports = QHBoxLayout()
        viewports.setSpacing(20)

        self.box_orig, self.screen_orig = self.create_viewport("ORIGINAL FEED")
        self.box_silh, self.screen_silh = self.create_viewport("SILHOUETTE")
        self.box_led, self.screen_led = self.create_viewport("LED PREVIEW (64x64)")

        viewports.addStretch()
        viewports.addWidget(self.box_orig)
        viewports.addWidget(self.box_silh)
        viewports.addWidget(self.box_led)
        viewports.addStretch()
        content_lay.addLayout(viewports)

        # --- Serial connection bar ---
        content_lay.addLayout(self._build_serial_bar())

        # --- Control panel (sliders + buttons + status) ---
        footer = QHBoxLayout()
        footer.setSpacing(30)

        # Sliders frame — holds two stacks (mask sliders, tip sliders); show/hide by mode
        sliders_frame = QFrame()
        sliders_frame.setStyleSheet(
            f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; padding: 10px; background: transparent; }}"
        )
        s_lay = QVBoxLayout(sliders_frame)

        # Mask-mode sliders
        self.mask_slider_widget = QWidget()
        self.mask_slider_widget.setStyleSheet("background: transparent; border: none;")
        mask_slider_lay = QVBoxLayout(self.mask_slider_widget)
        mask_slider_lay.setContentsMargins(0, 0, 0, 0)
        self.slider_conf = self.create_wireframe_slider(mask_slider_lay, "CONFIDENCE", 0, 100, 50,
                                                        on_change=self.update_mask_params)
        self.slider_led = self.create_wireframe_slider(mask_slider_lay, "LED THR", 0, 255, 127,
                                                       on_change=self.update_mask_params)
        s_lay.addWidget(self.mask_slider_widget)

        # Fingertip-mode sliders (MediaPipe sensitivity, NOT HSV — Wen's pipeline is landmark-based)
        self.tip_slider_widget = QWidget()
        self.tip_slider_widget.setStyleSheet("background: transparent; border: none;")
        tip_slider_lay = QVBoxLayout(self.tip_slider_widget)
        tip_slider_lay.setContentsMargins(0, 0, 0, 0)
        self.slider_hand_det = self.create_wireframe_slider(tip_slider_lay, "HAND CONF", 10, 95, 60,
                                                            on_change=self.update_tip_params)
        self.slider_hand_track = self.create_wireframe_slider(tip_slider_lay, "TRACK CONF", 10, 95, 60,
                                                              on_change=self.update_tip_params)
        s_lay.addWidget(self.tip_slider_widget)
        self.tip_slider_widget.hide()  # mask is initial mode

        footer.addWidget(sliders_frame, stretch=2)

        # Buttons column
        btns = QVBoxLayout()
        self.btn_gemini = self.create_wireframe_btn("AI SCENE ANALYSIS", PRIMARY_BLUE)
        self.btn_reset = self.create_wireframe_btn("RESET CAMERA", RETRO_ORANGE)
        self.btn_blank = self.create_wireframe_btn("BLANK PANEL", RETRO_ORANGE)

        self.btn_reset.clicked.connect(self.worker.reset_camera)
        self.btn_gemini.clicked.connect(self.run_gemini_analysis)
        self.btn_blank.clicked.connect(self.blank_panel)

        btns.addWidget(self.btn_gemini)
        btns.addWidget(self.btn_reset)
        btns.addWidget(self.btn_blank)
        footer.addLayout(btns, stretch=1)

        # Status box (5-row layout)
        footer.addWidget(self._build_status_box())

        content_lay.addLayout(footer)

        # --- Gemini response panel ---
        self.gemini_panel = QTextEdit()
        self.gemini_panel.setReadOnly(True)
        self.gemini_panel.setFixedHeight(120)
        self.gemini_panel.setFont(QFont("Fixedsys", 13))
        self.gemini_panel.setPlaceholderText("AI SCENE ANALYSIS — click the button above to ask Gemini.")
        self.gemini_panel.setStyleSheet(
            f"QTextEdit {{ border: 2px solid {PRIMARY_BLUE}; "
            f"background: {WHITE}; color: {TEXT_DARK}; padding: 8px; }}"
        )
        content_lay.addWidget(self.gemini_panel)

        # --- Background Decorations ---
        self.add_decorations(content)

        # --- Assemble ---
        main_layout.addWidget(top_border)
        main_layout.addWidget(content)
        main_layout.addWidget(bottom_border)

        self.setCentralWidget(central_widget)

    def _build_serial_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(12)

        port_label = QLabel("PORT:")
        port_label.setFont(QFont("Fixedsys", 14, QFont.Weight.Bold))
        port_label.setStyleSheet(f"color: {PRIMARY_BLUE}; background: transparent; border: none;")

        self.port_combo = QComboBox()
        self.port_combo.setFont(QFont("Fixedsys", 12))
        self.port_combo.setMinimumWidth(280)
        self.port_combo.setStyleSheet(
            f"QComboBox {{ border: 2px solid {PRIMARY_BLUE}; padding: 6px; "
            f"background: {WHITE}; color: {TEXT_DARK}; }}"
        )

        self.btn_refresh = self.create_wireframe_btn("REFRESH", PRIMARY_BLUE)
        self.btn_refresh.setFixedHeight(40)
        self.btn_refresh.clicked.connect(self.refresh_ports)

        self.btn_connect = self.create_wireframe_btn("CONNECT", PRIMARY_BLUE)
        self.btn_connect.setFixedHeight(40)
        self.btn_connect.clicked.connect(self.toggle_connection)

        bar.addWidget(port_label)
        bar.addWidget(self.port_combo, stretch=1)
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_connect)
        return bar

    def _build_status_box(self) -> QFrame:
        status_box = QFrame()
        status_box.setFixedSize(380, 180)
        status_box.setStyleSheet(
            f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; padding: 8px; background: transparent; }}"
        )
        grid = QGridLayout(status_box)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setVerticalSpacing(2)

        font = QFont("Fixedsys", 13)

        def make_row(label_text):
            lbl = QLabel(label_text)
            lbl.setFont(font)
            lbl.setStyleSheet(f"color: {RETRO_ORANGE}; border: none; background: transparent;")
            val = QLabel("—")
            val.setFont(font)
            val.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
            return lbl, val

        l_mode, self.lbl_mode = make_row("MODE:")
        l_serial, self.lbl_serial = make_row("SERIAL:")
        l_link, self.lbl_link = make_row("LINK:")
        l_cam, self.lbl_cam = make_row("CAM:")
        l_pot, self.lbl_pot = make_row("POT:")

        for row, (lbl, val) in enumerate([
            (l_mode, self.lbl_mode),
            (l_serial, self.lbl_serial),
            (l_link, self.lbl_link),
            (l_cam, self.lbl_cam),
            (l_pot, self.lbl_pot),
        ]):
            grid.addWidget(lbl, row, 0)
            grid.addWidget(val, row, 1)

        # initial values
        self.lbl_serial.setText("DISCONNECTED")
        self.lbl_link.setText("—")
        self.lbl_cam.setText("FPS 0.0 | People 0")
        self.lbl_pot.setText("t = ?  (TODO: firmware echo)")
        return status_box

    def create_viewport(self, title):
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(title)
        lbl.setFont(QFont("Fixedsys", 14, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {TEXT_DARK}; background: transparent; border: none;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        screen_frame = QFrame()
        screen_frame.setFixedSize(360, 360)
        screen_frame.setStyleSheet(f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; background: {BLACK}; }}")
        screen_lay = QVBoxLayout(screen_frame)
        screen_lay.setContentsMargins(0, 0, 0, 0)

        screen = QLabel()
        screen.setFixedSize(360, 360)
        screen.setScaledContents(True)
        screen.setStyleSheet("border: none; background: transparent;")
        screen_lay.addWidget(screen)

        lay.addWidget(lbl)
        lay.addWidget(screen_frame)
        return container, screen

    def create_wireframe_slider(self, parent_lay, label, min_v, max_v, init_v, on_change=None):
        lay = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFont(QFont("Fixedsys", 16, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        lbl.setFixedWidth(160)

        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setRange(min_v, max_v)
        sld.setValue(init_v)
        sld.setStyleSheet(f"""
            QSlider::groove:horizontal {{ border: 1px solid {PRIMARY_BLUE}; height: 4px; background: {WHITE}; }}
            QSlider::handle:horizontal {{ background: {PRIMARY_BLUE}; width: 15px; height: 15px; margin: -6px 0; }}
        """)
        if on_change is not None:
            sld.valueChanged.connect(on_change)

        lay.addWidget(lbl)
        lay.addWidget(sld)
        parent_lay.addLayout(lay)
        return sld

    def create_wireframe_btn(self, text, color):
        btn = QPushButton(text)
        btn.setFont(QFont("Fixedsys", 16, QFont.Weight.Bold))
        btn.setStyleSheet(f"""
            QPushButton {{
                border: 2px solid {PRIMARY_BLUE};
                color: {color};
                background: {BACKGROUND_CREAM};
                padding: 14px;
            }}
            QPushButton:pressed {{ background: {color}; color: {WHITE}; }}
            QPushButton:disabled {{ color: #999; border-color: #999; }}
        """)
        btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return btn

    def add_decorations(self, parent):
        icons = [
            ("🌙", 120, 60), ("🚀", 250, 150), ("⭐", 150, 240),
            ("💡", 80, 750), ("👆", 200, 880), ("⏳", 400, 800),
            ("😊", 1150, 60), ("☁️", 1000, 150), ("⭐", 1100, 240),
            ("💾", 1200, 750), ("👾", 950, 850), ("↖️", 1100, 880),
        ]
        for icon, x, y in icons:
            lbl = DraggableIcon(icon, parent)
            lbl.setStyleSheet("font-size: 24px; background: transparent; border: none;")
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.move(x, y)

    # ------------------------------------------------------------------
    # Slider handlers
    # ------------------------------------------------------------------

    def update_mask_params(self):
        self.worker.conf_threshold = self.slider_conf.value() / 100.0
        self.worker.pixel_threshold = self.slider_led.value()

    def update_tip_params(self):
        self.worker.hand_detection_conf = self.slider_hand_det.value() / 100.0
        self.worker.hand_tracking_conf = self.slider_hand_track.value() / 100.0

    # ------------------------------------------------------------------
    # Vision -> display
    # ------------------------------------------------------------------

    def update_camera_status(self, fps, count):
        self.lbl_cam.setText(f"FPS {fps:.1f} | People {count}")

    def update_screens(self, img_o, img_s, img_p):
        self.current_raw_frame = img_o
        self.screen_orig.setPixmap(self.convert_cv_qt(img_o))
        self.screen_silh.setPixmap(self.convert_cv_qt(img_s, True))
        self.screen_led.setPixmap(self.convert_cv_qt(img_p, True))

    def convert_cv_qt(self, cv_img, gray=False):
        # Center-crop to square so a 16:9 camera frame doesn't get squashed into a 1:1 viewport.
        h, w = cv_img.shape[:2]
        side = min(h, w)
        y0 = (h - side) // 2
        x0 = (w - side) // 2
        cv_img = cv_img[y0:y0 + side, x0:x0 + side]

        if gray:
            cv_img = np.ascontiguousarray(cv_img)
            h, w = cv_img.shape
            q_img = QImage(cv_img.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        else:
            cv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
            cv_img = np.ascontiguousarray(cv_img)
            h, w, ch = cv_img.shape
            q_img = QImage(cv_img.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(q_img)

    # ------------------------------------------------------------------
    # Vision -> serial routing (with backpressure gate)
    # ------------------------------------------------------------------

    def on_mask_ready(self, mask01: np.ndarray):
        if not self.connected or self.current_mode != MODE_MASK:
            return
        if self._tx_in_flight:
            return  # drop — newer frames will follow
        now = time.monotonic()
        if (now - self._last_tx_at) < TX_INTERVAL_S:
            return  # rate-limit
        self._last_tx_at = now
        self._tx_in_flight = True
        self.serial_worker.send_mask(mask01)

    def on_tips_ready(self, tips: list):
        if not self.connected or self.current_mode != MODE_FINGERTIPS:
            return
        if self._tx_in_flight:
            return
        now = time.monotonic()
        if (now - self._last_tx_at) < TX_INTERVAL_S:
            return
        self._last_tx_at = now
        self._tx_in_flight = True
        self.serial_worker.send_tips(tips)

    def on_send_complete(self, ok: bool):
        self._tx_in_flight = False
        # Live fps window — count successful (ACK'd) sends in the last 1s.
        if ok:
            now = time.monotonic()
            self._link_fps_window.append(now)
            cutoff = now - 1.0
            self._link_fps_window = [t for t in self._link_fps_window if t > cutoff]

    # ------------------------------------------------------------------
    # Serial connection control
    # ------------------------------------------------------------------

    def refresh_ports(self):
        self.port_combo.clear()
        ports = list_serial_ports()
        if not ports:
            self.port_combo.addItem("(no serial ports detected)", None)
            self.port_combo.setEnabled(False)
            self.btn_connect.setEnabled(False)
            return
        for device, desc in ports:
            label = f"{device}  {desc}".strip() if desc else device
            self.port_combo.addItem(label, device)
        self.port_combo.setEnabled(True)
        self.btn_connect.setEnabled(True)

    def toggle_connection(self):
        if self.connected:
            self.serial_worker.disconnect_port()
        else:
            port = self.port_combo.currentData()
            if not port:
                return
            self.btn_connect.setEnabled(False)
            self.serial_worker.connect_to_port(port)

    def on_serial_connected(self, port: str):
        self.connected = True
        self.current_port = port
        self.lbl_serial.setText(f"{port}  [CONNECTED]")
        self.lbl_serial.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
        self.btn_connect.setText("DISCONNECT")
        self.btn_connect.setEnabled(True)
        self._tx_in_flight = False

    def on_serial_disconnected(self):
        self.connected = False
        self.current_port = None
        self.lbl_serial.setText("DISCONNECTED")
        self.lbl_serial.setStyleSheet(f"color: {RETRO_ORANGE}; border: none; background: transparent;")
        self.btn_connect.setText("CONNECT")
        self.btn_connect.setEnabled(True)
        self._tx_in_flight = False

    def on_mode_changed(self, mode: int):
        self.current_mode = mode
        self.update_mode_ui()

    def update_mode_ui(self):
        if self.current_mode == MODE_MASK:
            self.lbl_mode.setText("MASK")
            self.lbl_mode.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
            self.mask_slider_widget.show()
            self.tip_slider_widget.hide()
        else:
            self.lbl_mode.setText("FINGERTIPS")
            self.lbl_mode.setStyleSheet(f"color: {RETRO_ORANGE}; border: none; background: transparent;")
            self.mask_slider_widget.hide()
            self.tip_slider_widget.show()
            if not MEDIAPIPE_AVAILABLE:
                self.gemini_panel.setPlainText(
                    "Warning: MediaPipe is not installed. Fingertip mode will send empty frames. "
                    "Install with: pip install mediapipe"
                )

    def on_link_stats(self, sent: int, ack: int, nak: int):
        self._stats_sent = sent
        self._stats_ack = ack
        self._stats_nak = nak
        ack_pct = (100.0 * ack / sent) if sent > 0 else 0.0
        live_fps = len(self._link_fps_window)  # frames acked in last 1s
        self.lbl_link.setText(f"{live_fps:.0f} fps | ACK {ack_pct:.1f}% | NAK {nak}")

    def on_serial_error(self, msg: str):
        # Surface in the gemini panel too so it's visible
        self.gemini_panel.setPlainText(f"[serial error] {msg}")

    def blank_panel(self):
        if not self.connected:
            return
        self.serial_worker.send_blank()

    # ------------------------------------------------------------------
    # Gemini
    # ------------------------------------------------------------------

    def run_gemini_analysis(self):
        if self.current_raw_frame is None:
            self.gemini_panel.setPlainText("No camera frame yet — waiting for capture.")
            return
        if not GEMINI_KEY:
            self.gemini_panel.setPlainText("GEMINI_API_KEY not set in .env — cannot run analysis.")
            return
        try:
            client = genai.Client(api_key=GEMINI_KEY)
            _, buffer = cv2.imencode('.jpg', self.current_raw_frame)
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=["Describe this for pixel art.",
                          {"inline_data": {"data": buffer.tobytes(), "mime_type": "image/jpeg"}}]
            )
            self.gemini_panel.setPlainText(response.text or "(empty response)")
        except Exception as e:
            self.gemini_panel.setPlainText(f"Gemini error: {e}")

    def closeEvent(self, event):
        self.worker.stop()
        self.serial_worker.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Fixedsys", 10))
    window = PixelMirrorGUI()
    window.show()
    sys.exit(app.exec())

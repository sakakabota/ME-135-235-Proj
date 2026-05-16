import sys
import os
import glob
import time
import logging
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QSlider, QFrame, QPushButton)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QFont
from ultralytics import YOLO
from google import genai

# Pull the vision side serial helpers sibling package 
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from serial_protocol import (  # noqa E402
    Fingertip,
    MODE_MASK,
    MODE_FINGERTIPS,
    SerialSender,
)

try:
    import mediapipe as mp
    _MP_AVAILABLE = True
except ImportError:
    mp = None
    _MP_AVAILABLE = False

# Load environment variables from env file
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# PALETTE Retro Canva Pixel Art Colors 
PRIMARY_BLUE = "#0148ff"
RETRO_ORANGE = "#ff5757"
BACKGROUND_CREAM = "#fdf5f0"
TEXT_DARK = "#454f00"
WHITE = "#FFFFFF"
BLACK = "#000000"

PANEL_SIZE = 64
PREVIEW_SIZE = 400  # upscaled preview size shown in the LED viewport
TX_MIN_INTERVAL_S = 1.0 / 30.0
SERIAL_BAUD = 1_000_000

TIP_IDS = [4, 8, 12, 16, 20]
TIP_COLORS_BGR = [
    (0, 0, 255),    # red
    (0, 255, 0),    # green
    (255, 0, 0),    # blue
    (0, 255, 255),  # yellow
    (255, 0, 255),  # magenta
]
COLOR_FLIP_SPEED = 3


def _autodetect_port() -> str | None:
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    for port in ports:
        # Simple heuristic prioritize CP210x CH340 or standard USB Serial ports often used by ESP32
        desc = port.description.lower()
        if "usb" in desc or "uart" in desc or "cp210" in desc or "ch340" in desc or "serial" in desc:
            return port.device
    
    # Fallback to the first available port if no USB Serial descriptions match
    if ports:
        return ports[0].device
        
    return None


class VisionWorker(QThread):
    """Camera YOLO MediaPipe ESP32 serial Renders a colored 64×64 LED preview
 that mirrors what the physical panel would show pot driven white→red lerp in
 mode 0 fingertip dots on black in mode 1 Mode is toggled by the ESP32 button 
 pot updates stream back from firmware 0x20 byte """

    # original_bgr silhouette_gray led_preview_bgr already upscaled 
    change_pixmap_signal = pyqtSignal(np.ndarray, np.ndarray, np.ndarray)
    # fps people_count mode 0 1 pot 0 255 connected bool ack nak
    status_signal = pyqtSignal(float, int, int, int, bool, int, int)

    def __init__(self, port: str | None = None):
        super().__init__()
        self._run_flag = True
        self._reset_flag = False
        self.conf_threshold = 0.5
        self.pixel_threshold = 127
        self.min_area_percent = 0.01
        self.model = YOLO('yolov8n-seg.pt')
        self._port_override = port

        if _MP_AVAILABLE:
            self._hands = mp.solutions.hands.Hands(
                max_num_hands=2,
                min_detection_confidence=0.6,
                min_tracking_confidence=0.6,
            )
        else:
            self._hands = None

        self._sender: SerialSender | None = None
        self._sender_error: str | None = None
        self.gui_mode = MODE_MASK
        self.gui_pot = 128

    def _connect_serial(self) -> None:
        port = self._port_override or _autodetect_port()
        if port is None:
            self._sender_error = "no ESP32 port detected"
            logging.warning("VisionWorker: %s", self._sender_error)
            return
        try:
            self._sender = SerialSender(port=port, baudrate=SERIAL_BAUD)
            logging.info("VisionWorker: ESP32 connected on %s", port)
        except Exception as exc:  # serial SerialException etc 
            self._sender = None
            self._sender_error = f"{port}: {exc}"
            logging.warning("VisionWorker: serial open failed — %s", self._sender_error)

    def run(self):
        self._connect_serial()
        cap = cv2.VideoCapture(0)
        prev_time = 0.0
        last_tx = 0.0

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

            h, w = frame.shape[:2]

            # YOLO person silhouette 
            results = self.model.predict(frame, conf=self.conf_threshold, verbose=False, classes=[0])
            annotated_frame = frame.copy()
            silhouette = np.zeros((h, w), dtype=np.uint8)
            people_count = 0
            detection_label = "no detection"

            if results[0].masks is not None:
                masks = results[0].masks.data.cpu().numpy()
                people_count = len(masks)
                combined = np.zeros_like(silhouette)

                if len(results[0].boxes) > 0:
                    max_conf = results[0].boxes.conf[0].item()
                    detection_label = f"person {max_conf:.2f}"

                for m in masks:
                    if np.sum(m) > (self.min_area_percent * m.size):
                        m_resized = cv2.resize(m, (w, h))
                        combined = cv2.bitwise_or(combined, (m_resized * 255).astype(np.uint8))

                kernel = np.ones((5, 5), np.uint8)
                silhouette = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)

                for box in results[0].boxes.xyxy:
                    x1, y1, x2, y2 = map(int, box)
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 72, 1), 2)
                    cv2.putText(annotated_frame, detection_label, (x1, max(y1 - 5, 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 72, 1), 2)

            # 64×64 binary mask what would ship to the LED in mode 0
            # center crop the camera frame to square first so people are not squished
            fh_sil, fw_sil = silhouette.shape[:2]
            side_sil = min(fh_sil, fw_sil)
            y0_sil = (fh_sil - side_sil) // 2
            x0_sil = (fw_sil - side_sil) // 2
            silhouette_sq = silhouette[y0_sil:y0_sil + side_sil, x0_sil:x0_sil + side_sil]
            small = cv2.resize(silhouette_sq, (PANEL_SIZE, PANEL_SIZE), interpolation=cv2.INTER_AREA)
            _, mask_64 = cv2.threshold(small, self.pixel_threshold, 255, cv2.THRESH_BINARY)

            # Pull mode pot from ESP32 first mediapipe is only useful in mode 1 
            current_mode = self.gui_mode
            pot_val = self.gui_pot
            connected = self._sender is not None
            ack = nak = 0
            if self._sender is not None:
                self._sender.read_mode_change()  # absorbs pot updates too
                if self._sender.esp32_mode != self.gui_mode:
                    self.gui_mode = self._sender.esp32_mode
                    current_mode = self.gui_mode
                pot_val = self._sender.esp32_pot
                ack = self._sender.frames_acked
                nak = self._sender.frames_naked

            # MediaPipe fingertips mode 1 only skip the cost in mode 0 
            fingertips: list[Fingertip] = []
            if current_mode != MODE_MASK:
                shift = int(time.time() * COLOR_FLIP_SPEED)
                if self._hands is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    hand_results = self._hands.process(rgb)
                    if hand_results.multi_hand_landmarks:
                        fh, fw = frame.shape[:2]
                        for hand in hand_results.multi_hand_landmarks:
                            for i, tip_id in enumerate(TIP_IDS):
                                lm = hand.landmark[tip_id]
                                bgr = TIP_COLORS_BGR[(i + shift) % len(TIP_COLORS_BGR)]
                                
                                # 1 Draw circles on the original camera feed
                                cx = int(lm.x * fw)
                                cy = int(lm.y * fh)
                                cv2.circle(frame, (cx, cy), 10, bgr, -1)
                                cv2.circle(frame, (cx, cy), 18, bgr, 2)

                                # 2 Record for ESP32 and LED Preview
                                x = int(lm.x * PANEL_SIZE)
                                y = int(lm.y * PANEL_SIZE)
                                if 0 <= x < PANEL_SIZE and 0 <= y < PANEL_SIZE:
                                    fingertips.append(Fingertip(
                                        x=x, y=y, r=bgr[2], g=bgr[1], b=bgr[0],
                                    ))
                else:
                    # MOCK FINGERS for preview when mediapipe is unavailable
                    import math
                    t = time.time()
                    fh, fw = frame.shape[:2]
                    for i in range(5):
                        offset = i * 1.2
                        # Mock coordinates in 0 1 scale
                        nx = 0.5 + 0.3 * math.cos(t * 1.5 + offset)
                        ny = 0.5 + 0.3 * math.sin(t * 2.0 + offset)
                        bgr = TIP_COLORS_BGR[(i + shift) % len(TIP_COLORS_BGR)]

                        # 1 Draw mock circles on the original camera feed
                        cx = int(nx * fw)
                        cy = int(ny * fh)
                        cv2.circle(frame, (cx, cy), 10, bgr, -1)
                        cv2.circle(frame, (cx, cy), 18, bgr, 2)

                        # 2 Record for LED preview
                        x = int(nx * PANEL_SIZE)
                        y = int(ny * PANEL_SIZE)
                        if 0 <= x < PANEL_SIZE and 0 <= y < PANEL_SIZE:
                            fingertips.append(Fingertip(
                                x=x, y=y, r=bgr[2], g=bgr[1], b=bgr[0],
                            ))

            if self._sender is not None:
                now = time.time()
                if (now - last_tx) >= TX_MIN_INTERVAL_S:
                    try:
                        if current_mode == MODE_MASK:
                            self._sender.send_mask((mask_64 > 0).astype(np.uint8))
                        else:
                            self._sender.send_fingertips(fingertips)
                        last_tx = now
                    except Exception as exc:
                        logging.warning("serial TX failed: %s", exc)

            # Build colored 64×64 LED preview 
            led_preview = self._render_led_preview(mask_64, fingertips, current_mode, pot_val)

            fps = 1.0 / (time.time() - prev_time + 1e-6)
            prev_time = time.time()

            self.change_pixmap_signal.emit(annotated_frame, silhouette, led_preview)
            self.status_signal.emit(fps, people_count, current_mode, pot_val, connected, ack, nak)

        cap.release()
        if self._sender is not None:
            self._sender.close()

    @staticmethod
    def _render_led_preview(mask_64: np.ndarray, fingertips: list[Fingertip],
                            mode: int, pot: int) -> np.ndarray:
        """Build a 400×400 BGR preview that matches what the LED panel would render."""
        canvas_64 = np.zeros((PANEL_SIZE, PANEL_SIZE, 3), dtype=np.uint8)

        if mode == MODE_MASK:
            t = max(0.0, min(1.0, pot / 255.0))
            r = 255
            g = int((1.0 - t) * 255.0)
            b = int((1.0 - t) * 255.0)
            on = (mask_64 > 0)
            canvas_64[on] = (b, g, r)  # BGR
        else:
            for ft in fingertips:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        px, py = ft.x + dx, ft.y + dy
                        if 0 <= px < PANEL_SIZE and 0 <= py < PANEL_SIZE:
                            canvas_64[py, px] = (ft.b, ft.g, ft.r)

        return cv2.resize(canvas_64, (PREVIEW_SIZE, PREVIEW_SIZE), interpolation=cv2.INTER_NEAREST)

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
        self.setMinimumSize(1280, 980)
        self.setStyleSheet(f"background-color: {BACKGROUND_CREAM};")

        self.current_raw_frame = None

        self.worker = VisionWorker()

        self.init_ui()

        self.worker.change_pixmap_signal.connect(self.update_screens)
        self.worker.status_signal.connect(self.update_status)
        self.worker.start()

    def generate_checkerboard(self):
        tile_size = 40
        tile = np.full((tile_size, tile_size, 3), 255, dtype=np.uint8)
        blue = (255, 72, 1)  # BGR
        half = tile_size // 2
        tile[0:half, 0:half] = blue
        tile[half:tile_size, half:tile_size] = blue
        cv2.imwrite("checker_tile.png", tile)

    def get_rainbow_text(self, text):
        colors = ["#ff5757", "#ffaa00", "#aaaa00", "#55ff55", "#5555ff", "#aa00aa", "#ff55ff"]
        result = ""
        color_idx = 0
        for char in text:
            if char == ' ':
                result += "&nbsp;"
                continue
            color = colors[color_idx % len(colors)]
            result += f"<span style='color: {color};'>{char}</span>"
            color_idx += 1
        return result

    def init_ui(self):
        self.generate_checkerboard()
        central_widget = QWidget()
        central_widget.setStyleSheet(f"background-color: {BACKGROUND_CREAM};")

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        top_border = QLabel()
        top_border.setFixedHeight(35)
        top_border.setStyleSheet("background-image: url('checker_tile.png'); background-repeat: repeat-x;")

        bottom_border = QLabel()
        bottom_border.setFixedHeight(35)
        bottom_border.setStyleSheet("background-image: url('checker_tile.png'); background-repeat: repeat-x;")

        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(40, 20, 40, 20)
        content_lay.setSpacing(15)

        # Header 
        header = QVBoxLayout()
        title = QLabel("PIXEL MIRROR")
        title.setFont(QFont("Fixedsys", 56, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {PRIMARY_BLUE}; background: transparent; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_course = QLabel("ME 135/235")
        info_course.setFont(QFont("Fixedsys", 20))
        info_course.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        info_course.setAlignment(Qt.AlignmentFlag.AlignCenter)

        info_names = QLabel("STEPH AKAKABOTA | WEN CAO | KYLE NELSON | LARRY HUI")
        info_names.setFont(QFont("Fixedsys", 16))
        info_names.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        info_names.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header.addWidget(title)
        header.addWidget(info_course)
        header.addWidget(info_names)
        content_lay.addLayout(header)

        # Main Viewports 
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

        # Bottom Panels 
        bottom_panels = QHBoxLayout()
        bottom_panels.setSpacing(20)

        # LEFT Confidence 
        conf_container = QWidget()
        conf_container.setFixedWidth(360)
        conf_lay = QVBoxLayout(conf_container)
        conf_lay.setContentsMargins(0, 0, 0, 0)
        
        conf_frame = QFrame()
        conf_frame.setFixedHeight(60)
        conf_frame.setStyleSheet(f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; background: transparent; }}")
        slider_lay = QHBoxLayout(conf_frame)
        slider_lay.setContentsMargins(10, 0, 10, 0)
        
        conf_lbl = QLabel("CONFIDENCE")
        conf_lbl.setFont(QFont("Fixedsys", 12, QFont.Weight.Bold))
        conf_lbl.setStyleSheet(f"color: {TEXT_DARK}; border: none;")
        self.slider_conf = QSlider(Qt.Orientation.Horizontal)
        self.slider_conf.setRange(0, 100)
        self.slider_conf.setValue(50)
        self.slider_conf.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_conf.setTickInterval(10)
        self.slider_conf.setStyleSheet(f"""
            QSlider::groove:horizontal {{ border: 1px solid {PRIMARY_BLUE}; height: 2px; background: transparent; }}
            QSlider::handle:horizontal {{ background: {PRIMARY_BLUE}; width: 8px; height: 16px; margin: -7px 0; }}
        """)
        self.slider_conf.valueChanged.connect(self.update_params)
        slider_lay.addWidget(conf_lbl)
        slider_lay.addWidget(self.slider_conf)
        conf_lay.addWidget(conf_frame)

        pot_frame = QFrame()
        pot_frame.setFixedHeight(60)
        pot_frame.setStyleSheet(f"QFrame {{ border: 2px solid {{PRIMARY_BLUE}}; background: transparent; }}")
        pot_lay = QHBoxLayout(pot_frame)
        pot_lay.setContentsMargins(10, 0, 10, 0)
        
        pot_lbl = QLabel("POT (MOCK)")
        pot_lbl.setFont(QFont("Fixedsys", 12, QFont.Weight.Bold))
        pot_lbl.setStyleSheet(f"color: {RETRO_ORANGE}; border: none;")
        self.slider_pot = QSlider(Qt.Orientation.Horizontal)
        self.slider_pot.setRange(0, 255)
        self.slider_pot.setValue(128)
        self.slider_pot.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_pot.setTickInterval(25)
        self.slider_pot.setStyleSheet(f"""
            QSlider::groove:horizontal {{ border: 1px solid {{PRIMARY_BLUE}}; height: 2px; background: transparent; }}
            QSlider::handle:horizontal {{ background: {{RETRO_ORANGE}}; width: 8px; height: 16px; margin: -7px 0; }}
        """)
        self.slider_pot.valueChanged.connect(self.update_params)
        pot_lay.addWidget(pot_lbl)
        pot_lay.addWidget(self.slider_pot)
        
        conf_lay.addWidget(pot_frame)
        conf_lay.addStretch()

        # CENTER Camera Controls 
        cam_container = QWidget()
        cam_container.setFixedWidth(360)
        cam_lay = QVBoxLayout(cam_container)
        cam_lay.setContentsMargins(0, 0, 0, 0)

        mode_panel = QFrame()
        mode_panel.setStyleSheet(f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; background: transparent; }}")
        m_lay = QVBoxLayout(mode_panel)
        m_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_lay.setSpacing(10)

        cam_lbl = QLabel("CAMERA CONTROLS")
        cam_lbl.setFont(QFont("Fixedsys", 16, QFont.Weight.Bold))
        cam_lbl.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        cam_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_lay.addWidget(cam_lbl)

        btn_style = f"""
            QPushButton {{
                border: 2px solid {PRIMARY_BLUE};
                color: {WHITE};
                background: {PRIMARY_BLUE};
                padding: 6px;
            }}
            QPushButton:pressed {{ background: {BACKGROUND_CREAM}; color: {PRIMARY_BLUE}; }}
        """

        self.btn_refresh = QPushButton("REFRESH CAMERA")
        self.btn_refresh.setFixedSize(220, 35)
        self.btn_refresh.setFont(QFont("Fixedsys", 14, QFont.Weight.Bold))
        self.btn_refresh.setStyleSheet(btn_style)
        self.btn_refresh.clicked.connect(self.worker.reset_camera)
        m_lay.addWidget(self.btn_refresh, alignment=Qt.AlignmentFlag.AlignCenter)

        mode_lbl = QLabel("MODE SELECT")
        mode_lbl.setFont(QFont("Fixedsys", 14, QFont.Weight.Bold))
        mode_lbl.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        mode_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_lay.addWidget(mode_lbl)

        toggle_lay = QHBoxLayout()
        lbl_finger = QLabel(self.get_rainbow_text("FINGER GLOWING"))
        lbl_finger.setFont(QFont("Fixedsys", 12, QFont.Weight.Bold))
        lbl_finger.setStyleSheet("border: none; background: transparent;")
        lbl_finger.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        self.mode_toggle = QSlider(Qt.Orientation.Horizontal)
        self.mode_toggle.setRange(0, 1)
        self.mode_toggle.setFixedSize(40, 20)
        self.mode_toggle.setStyleSheet(f"""
            QSlider::groove:horizontal {{ border: 2px solid {PRIMARY_BLUE}; height: 8px; background: {BLACK}; }}
            QSlider::handle:horizontal {{ background: {RETRO_ORANGE}; width: 14px; height: 20px; margin: -6px 0; }}
        """)
        self.mode_toggle.valueChanged.connect(self.update_params)
        
        lbl_red = QLabel("RED MODE")
        lbl_red.setFont(QFont("Fixedsys", 12, QFont.Weight.Bold))
        lbl_red.setStyleSheet(f"color: {RETRO_ORANGE}; background: transparent; border: none;")
        lbl_red.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        toggle_lay.addStretch()
        toggle_lay.addWidget(lbl_finger)
        toggle_lay.addWidget(self.mode_toggle)
        toggle_lay.addWidget(lbl_red)
        toggle_lay.addStretch()
        toggle_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_lay.addLayout(toggle_lay)

        cam_lay.addWidget(mode_panel)
        cam_lay.addStretch()

        # RIGHT Status Panel 
        stat_container = QWidget()
        stat_container.setFixedWidth(400) # Increased width to prevent text cutoff
        stat_lay = QVBoxLayout(stat_container)
        stat_lay.setContentsMargins(0, 0, 0, 0)
        
        status_box = QFrame()
        status_box.setFixedHeight(120)
        status_box.setStyleSheet(f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; padding: 5px; background: transparent; }}")
        status_lay = QVBoxLayout(status_box)
        
        live_lay = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {PRIMARY_BLUE}; font-size: 18px; border: none; background: transparent;")
        self.live_txt = QLabel("ESP32: SCANNING...")
        self.live_txt.setFont(QFont("Fixedsys", 14))
        self.live_txt.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
        live_lay.addWidget(self.dot); live_lay.addWidget(self.live_txt); live_lay.addStretch()
        
        self.status_data = QLabel("FPS: 0.0 | People: 0")
        self.status_data.setFont(QFont("Fixedsys", 14))
        self.status_data.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
        
        self.status_esp = QLabel("MODE: -- | POT: ---% | ACK 0/0")
        self.status_esp.setFont(QFont("Fixedsys", 14))
        self.status_esp.setStyleSheet(f"color: {RETRO_ORANGE}; border: none; background: transparent;")

        status_lay.addLayout(live_lay)
        status_lay.addWidget(self.status_data)
        status_lay.addWidget(self.status_esp)
        
        stat_lay.addWidget(status_box)
        stat_lay.addStretch()

        # Combine
        bottom_panels.addStretch()
        bottom_panels.addWidget(conf_container)
        bottom_panels.addWidget(cam_container)
        bottom_panels.addWidget(stat_container)
        bottom_panels.addStretch()

        content_lay.addLayout(bottom_panels)

        # Background Decorations 
        self.add_decorations(content)

        # Assemble 
        main_layout.addWidget(top_border)
        main_layout.addWidget(content)
        main_layout.addWidget(bottom_border)

        self.setCentralWidget(central_widget)

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

    def add_decorations(self, parent):
        icons = [
            ("🌙", 120, 60), ("🚀", 250, 150), ("⭐", 150, 240),
            ("💡", 80, 750), ("👆", 200, 880), ("⏳", 400, 800),
            ("😊", 1150, 60), ("☁️", 1000, 150), ("⭐", 1100, 240),
            ("💾", 1200, 750), ("👾", 950, 850), ("↖️", 1100, 880)
        ]
        for icon, x, y in icons:
            lbl = DraggableIcon(icon, parent)
            lbl.setStyleSheet("font-size: 24px; background: transparent; border: none;")
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.move(x, y)

    def update_params(self):
        self.worker.conf_threshold = self.slider_conf.value() / 100.0
        self.worker.gui_mode = MODE_MASK if self.mode_toggle.value() == 1 else MODE_FINGERTIPS
        self.worker.gui_pot = self.slider_pot.value()

    def update_status(self, fps, count, mode, pot, connected, ack, nak):
        self.status_data.setText(f"FPS: {fps:.1f} | People: {count}")
        
        # Sync slider if hardware button changed the mode
        self.mode_toggle.blockSignals(True)
        self.mode_toggle.setValue(1 if mode == MODE_MASK else 0)
        self.mode_toggle.blockSignals(False)

        if connected:
            self.slider_pot.blockSignals(True)
            self.slider_pot.setValue(pot)
            self.slider_pot.blockSignals(False)
            mode_label = "0 KYLE/POT" if mode == MODE_MASK else "1 WEN/FINGERS"
            self.live_txt.setText("ESP32: LIVE")
            self.dot.setStyleSheet(f"color: {{PRIMARY_BLUE}}; font-size: 18px; border: none; background: transparent;")
            self.status_esp.setText(f"MODE: {{mode_label}} | POT: {{pot * 100 // 255}}% | ACK {{ack}}/{{ack + nak}}")
        else:
            self.live_txt.setText("ESP32: NOT CONNECTED")
            self.dot.setStyleSheet(f"color: {{RETRO_ORANGE}}; font-size: 18px; border: none; background: transparent;")
            self.status_esp.setText(f"MODE: -- | POT: {{pot * 100 // 255}}% | (no serial)")

    def update_screens(self, img_o, img_s, img_p):
        self.current_raw_frame = img_o
        self.screen_orig.setPixmap(self.convert_cv_qt(img_o))
        self.screen_silh.setPixmap(self.convert_cv_qt(img_s, gray=True))
        self.screen_led.setPixmap(self.convert_cv_qt(img_p))  # color preview

    def convert_cv_qt(self, cv_img, gray=False):
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

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setFont(QFont("Fixedsys", 10))
    window = PixelMirrorGUI()
    window.show()
    sys.exit(app.exec())

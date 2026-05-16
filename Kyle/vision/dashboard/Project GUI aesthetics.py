import sys
import os
import cv2
import numpy as np
import time
from dotenv import load_dotenv
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QSlider, QFrame, QPushButton, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QFont
from ultralytics import YOLO
from google import genai

# Load environment variables from env file
load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# PALETTE Retro Canva Pixel Art Colors 
PRIMARY_BLUE = "#0148ff"
RETRO_ORANGE = "#ff5757" # The vibrant coral orange from Canva
BACKGROUND_CREAM = "#fdf5f0"
TEXT_DARK = "#454f00"
WHITE = "#FFFFFF"
BLACK = "#000000"

class VisionWorker(QThread):
    """Handles Camera and YOLO AI in a separate thread """
    change_pixmap_signal = pyqtSignal(np.ndarray, np.ndarray, np.ndarray)
    status_signal = pyqtSignal(float, int)

    def __init__(self):
        super().__init__()
        self._run_flag = True
        self._reset_flag = False
        self.conf_threshold = 0.5
        self.pixel_threshold = 127
        self.min_area_percent = 0.01
        self.model = YOLO('yolov8n-seg.pt')

    def run(self):
        cap = cv2.VideoCapture(0)
        prev_time = 0
        
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

            # YOLO Inference
            results = self.model.predict(frame, conf=self.conf_threshold, verbose=False, classes=[0])
            
            annotated_frame = frame.copy()
            silhouette = np.zeros((frame.shape[0], frame.shape[1]), dtype=np.uint8)
            people_count = 0
            detection_label = "no detection"

            if results[0].masks is not None:
                masks = results[0].masks.data.cpu().numpy()
                people_count = len(masks)
                combined_mask = np.zeros_like(silhouette)
                
                # Get max confidence for the label
                if len(results[0].boxes) > 0:
                    max_conf = results[0].boxes.conf[0].item()
                    detection_label = f"person {max_conf:.2f}"

                for mask in masks:
                    if np.sum(mask) > (self.min_area_percent * mask.size):
                        mask_resized = cv2.resize(mask, (frame.shape[1], frame.shape[0]))
                        combined_mask = cv2.bitwise_or(combined_mask, (mask_resized * 255).astype(np.uint8))
                
                kernel = np.ones((5,5), np.uint8)
                silhouette = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
                
                # Draw simple blue wireframe box and label for detections
                for box in results[0].boxes.xyxy:
                    x1, y1, x2, y2 = map(int, box)
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 72, 1), 2) # Blue wireframe
                    cv2.putText(annotated_frame, detection_label, (x1, max(y1-5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 72, 1), 2)

            # 64x64 Pixelation
            pixel_small = cv2.resize(silhouette, (64, 64), interpolation=cv2.INTER_AREA)
            _, pixel_thresh = cv2.threshold(pixel_small, self.pixel_threshold, 255, cv2.THRESH_BINARY)
            pixel_preview = cv2.resize(pixel_thresh, (400, 400), interpolation=cv2.INTER_NEAREST)

            fps = 1 / (time.time() - prev_time + 1e-6)
            prev_time = time.time()

            self.change_pixmap_signal.emit(annotated_frame, silhouette, pixel_preview)
            self.status_signal.emit(fps, people_count)

        cap.release()

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
        
        # Setup Vision Thread MUST BE BEFORE init_ui to connect signals slots 
        self.worker = VisionWorker()
        
        self.init_ui()
        
        self.worker.change_pixmap_signal.connect(self.update_screens)
        self.worker.status_signal.connect(self.update_status)
        self.worker.start()

    def generate_checkerboard(self):
        tile_size = 40
        tile = np.full((tile_size, tile_size, 3), 255, dtype=np.uint8)
        blue = (255, 72, 1) # BGR
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

        # Borders 
        top_border = QLabel()
        top_border.setFixedHeight(35)
        top_border.setStyleSheet("background-image: url('checker_tile.png'); background-repeat: repeat-x;")
        
        bottom_border = QLabel()
        bottom_border.setFixedHeight(35)
        bottom_border.setStyleSheet("background-image: url('checker_tile.png'); background-repeat: repeat-x;")

        # Content Container 
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

        # Main Viewports Three Parallel Rectangles 
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
        conf_lbl.setStyleSheet(f"color: {TEXT_DARK}; border: none;") # Earthy green
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

        # Buttons blue with white text Same size 
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
        self.btn_refresh.setFixedSize(220, 35) # Make sure text fits
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

        self.btn_normal = QPushButton("NORMAL MODE")
        self.btn_normal.setFixedSize(220, 35)
        self.btn_normal.setFont(QFont("Fixedsys", 14, QFont.Weight.Bold))
        self.btn_normal.setStyleSheet(btn_style)
        m_lay.addWidget(self.btn_normal, alignment=Qt.AlignmentFlag.AlignCenter)

        cam_lay.addWidget(mode_panel)
        cam_lay.addStretch()

        # RIGHT Status Panel 
        stat_container = QWidget()
        stat_container.setFixedWidth(360)
        stat_lay = QVBoxLayout(stat_container)
        stat_lay.setContentsMargins(0, 0, 0, 0)
        
        status_box = QFrame()
        status_box.setFixedHeight(100)
        status_box.setStyleSheet(f"QFrame {{ border: 2px solid {PRIMARY_BLUE}; padding: 5px; background: transparent; }}")
        status_lay = QVBoxLayout(status_box)
        
        live_lay = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {PRIMARY_BLUE}; font-size: 18px; border: none; background: transparent;")
        live_txt = QLabel("SYSTEM LIVE")
        live_txt.setFont(QFont("Fixedsys", 14))
        live_txt.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
        live_lay.addWidget(dot); live_lay.addWidget(live_txt); live_lay.addStretch()
        
        self.status_data = QLabel("FPS: 0.0 | People: 0")
        self.status_data.setFont(QFont("Fixedsys", 14))
        self.status_data.setStyleSheet(f"color: {PRIMARY_BLUE}; border: none; background: transparent;")
        
        status_lay.addLayout(live_lay)
        status_lay.addWidget(self.status_data)
        
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
        """)
        return btn

    def add_decorations(self, parent):
        # Adding floating pixel art emojis pushed to extreme edges
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

    def update_status(self, fps, count):
        self.status_data.setText(f"FPS: {fps:.1f} | People: {count}")

    def update_screens(self, img_o, img_s, img_p):
        self.current_raw_frame = img_o
        self.screen_orig.setPixmap(self.convert_cv_qt(img_o))
        self.screen_silh.setPixmap(self.convert_cv_qt(img_s, True))
        self.screen_led.setPixmap(self.convert_cv_qt(img_p, True))

    def convert_cv_qt(self, cv_img, gray=False):
        # Center crop to square so a 16 9 camera frame doesn t get squashed into a 1 1 viewport 
        h, w = cv_img.shape[:2]
        side = min(h, w)
        y0 = (h - side) // 2
        x0 = (w - side) // 2
        cv_img = cv_img[y0:y0+side, x0:x0+side]

        if gray:
            cv_img = np.ascontiguousarray(cv_img)
            h, w = cv_img.shape
            q_img = QImage(cv_img.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        else:
            cv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
            cv_img = np.ascontiguousarray(cv_img)
            h, w, ch = cv_img.shape
            q_img = QImage(cv_img.data, w, h, ch*w, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(q_img)

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Attempt to set a clean pixel like font if available
    app.setFont(QFont("Fixedsys", 10))
    window = PixelMirrorGUI()
    window.show()
    sys.exit(app.exec())
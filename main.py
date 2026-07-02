import sys
import cv2
import numpy as np
import mss
import pyautogui
import ctypes
import time
import threading
import json
import os

from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                             QPushButton, QFrame, QTabWidget, QComboBox, QGridLayout,
                             QDoubleSpinBox, QCheckBox, QSpinBox, QFileDialog, QGroupBox,
                             QListWidget, QScrollArea)
from PyQt6.QtGui import QImage, QPixmap, QIcon, QPainter, QColor, QPen
from PyQt6.QtCore import Qt, QTimer, QObject, QEvent

# Integracja pozostałych plików
from config import APP_CONFIG, SHARED_STATE, VK_DICT, load_templates, save_config_to_file, state_templates, \
    btn_templates, alert_templates
import bot

# Wymuszenie własnego ID dla paska zadań Windows, aby dynamiczna ikona działała poprawnie
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("my.metin.changer.v1")


def cv2_to_qpixmap(cv_img):
    if cv_img is None: return None
    if len(cv_img.shape) == 2:
        rgb_img = cv2.cvtColor(cv_img, cv2.COLOR_GRAY2RGB)
    else:
        rgb_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_img.shape
    return QPixmap.fromImage(QImage(rgb_img.data, w, h, ch * w, QImage.Format.Format_RGB888))


class IgnoreSpaceFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                return True
        return super().eventFilter(obj, event)


# ==========================================
# OKNO NAKŁADKI (OVERLAY) EKRANU
# ==========================================
class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        # Tworzy niewidoczne, przenikalne przez kliknięcia okno na wierzchu
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) # Moved here!

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(100)

    def paintEvent(self, event):
        painter = QPainter(self)

        # ODCZYT CH (Niebieski)
        painter.setPen(QPen(QColor("#03A9F4"), 2, Qt.PenStyle.SolidLine))
        sm = APP_CONFIG.get("state_monitor", {})
        if sm.get("width", 0) > 0:
            painter.drawRect(sm["left"], sm["top"], sm["width"], sm["height"])
            painter.drawText(sm["left"], sm["top"] - 5, "Odczyt CH (State)")

        # PRZYCISKI CH (Fioletowy)
        painter.setPen(QPen(QColor("#9C27B0"), 2, Qt.PenStyle.SolidLine))
        bm = APP_CONFIG.get("btn_monitor", {})
        if bm.get("width", 0) > 0:
            painter.drawRect(bm["left"], bm["top"], bm["width"], bm["height"])
            painter.drawText(bm["left"], bm["top"] - 5, "Przyciski CH (Btn)")

        # ALERTY (Pomarańczowy)
        painter.setPen(QPen(QColor("#FF5722"), 2, Qt.PenStyle.SolidLine))
        for i in range(1, 4):
            am = APP_CONFIG.get(f"alert_{i}_monitor", {})
            if am.get("width", 0) > 0:
                painter.drawRect(am["left"], am["top"], am["width"], am["height"])
                painter.drawText(am["left"], am["top"] - 5, f"Alert {i}")

        painter.end()


# ==========================================
# INTERFEJS UŻYTKOWNIKA (HUD)
# ==========================================
class ChangerHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.key_states = {}
        self.binding_action = None
        self.next_auto_ch_time = 0

        # === KLUCZOWA POPRAWKA: Wczytujemy config na samym początku, przed rysowaniem okna ===
        try:
            with open("changer_config.json", "r", encoding="utf-8") as f:
                saved = json.load(f)
                APP_CONFIG.update(saved)
        except FileNotFoundError:
            pass

        self.overlay = OverlayWindow()

        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Zmieniacz CH - Panel")

        # Teraz pobrane z pliku wartości X i Y zostaną prawidłowo zaaplikowane:
        rect = APP_CONFIG.get("gui_rect", {"x": 100, "y": 100, "w": 650, "h": 850})
        self.setGeometry(rect["x"], rect["y"], rect["w"], rect["h"])

        self.icon_running = self.create_status_icon("#4CAF50")
        self.icon_stopped = self.create_status_icon("#F44336")

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.setStyleSheet("""
            QWidget { background-color: #222222; color: white; font-family: 'Segoe UI', Arial; }
            QLabel { font-weight: bold; background: transparent; }
            QPushButton { background-color: #3F51B5; color: white; border: none; border-radius: 4px; padding: 8px; font-weight: bold; }
            QPushButton:hover { background-color: #5C6BC0; }
            QTabWidget::pane { border: 1px solid #444; border-radius: 4px; }
            QTabBar::tab { background: #333; padding: 10px; border: 1px solid #444; }
            QTabBar::tab:selected { background: #555; font-weight: bold; }
            QComboBox { background-color: #333; padding: 4px; border: 1px solid #555; color: white;}
            QDoubleSpinBox, QSpinBox { background-color: #333; color: white; border: 1px solid #555; padding: 4px; border-radius: 2px;}
            QCheckBox { font-size: 13px; font-weight: bold; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 10px; font-weight: bold; padding-top: 15px;}
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 3px; color: #FF9800; }
            QListWidget { background-color: #333; border: 1px solid #555; font-size: 14px; }
            QListWidget::item { padding: 5px; }
            QListWidget::item:selected { background-color: #3F51B5; color: white; }
        """)

        # (Stary blok try-except, który stał tutaj wywalamy, bo daliśmy go na samą górę)
        load_templates()

        self.tabs = QTabWidget()
        self.tab_main = QWidget()
        self.tab_wizard = QWidget()
        self.tab_editor = QWidget()
        self.tab_automation = QWidget()
        self.tab_settings = QWidget()

        self.tabs.addTab(self.tab_main, "Główny")
        self.tabs.addTab(self.tab_automation, "Automatyzacja")
        self.tabs.addTab(self.tab_settings, "Ustawienia")
        self.tabs.addTab(self.tab_wizard, "Kreator")
        self.tabs.addTab(self.tab_editor, "Edytor")

        self.wizard_step = 0
        self.wizard_points = []
        self.wizard_message = "Naciśnij ZBINDOWANY KLAWISZ w LEWYM-GÓRNYM rogu pierwszego obszaru."
        self.editor_points = []
        self.editor_message = "Wybierz z listy co chcesz podmienić i użyj klawisza przechwytywania."

        self.WIZARD_STEPS = [
            {"type": "monitor", "key": "state_monitor",
             "desc": "OBSZAR ODCZYTU:\nZaznacz prostokąt, w którym pojawia się\nnapis np. 'Sovelia, CH1'.",
             "color": "#03A9F4"},
            {"type": "monitor", "key": "btn_monitor",
             "desc": "OBSZAR PRZYCISKÓW:\nZaznacz jeden, długi pionowy prostokąt\nobejmujący WSZYSTKIE przyciski CH1-CH6.",
             "color": "#03A9F4"}
        ]
        for i in range(1, 7):
            self.WIZARD_STEPS.append({"type": "image", "key": f"ch{i}_state",
                                      "desc": f"OBRAZEK (Tekst):\nWytnij IDEALNIE sam tekst 'CH{i}'.",
                                      "color": "#E91E63"})
            self.WIZARD_STEPS.append(
                {"type": "image", "key": f"ch{i}_btn", "desc": f"OBRAZEK (Przycisk):\nWytnij IDEALNIE kółko 'CH{i}'.",
                 "color": "#9C27B0"})

        self.setup_main_tab()
        self.setup_automation_tab()
        self.setup_settings_tab()
        self.setup_wizard_tab()
        self.setup_editor_tab()

        self.apply_main_toggle_style()

        self.tabs.currentChanged.connect(self.on_tab_changed)
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        self.timer_keys = QTimer(self)
        self.timer_keys.timeout.connect(self.check_hardware_keys)
        self.timer_keys.start(20)

        self.timer_ui = QTimer(self)
        self.timer_ui.timeout.connect(self.update_ui)
        self.timer_ui.start(100)



    def create_status_icon(self, hex_color):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor(hex_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.end()
        return QIcon(pixmap)

    def on_tab_changed(self, index):
        if self.tabs.currentWidget() == self.tab_editor: self.refresh_editor_preview()
        if self.binding_action:
            self.binding_action = None
            self.refresh_settings_ui()
            self.refresh_automation_ui()

    def get_key_name(self, vk_code):
        if vk_code is None: return "BRAK"
        return VK_DICT.get(vk_code, f"HEX: {hex(vk_code)}")

    def toggle_main_bot(self):
        if APP_CONFIG.get("is_running", True):
            bot.bot_pause()
        else:
            bot.bot_resume()

        save_config_to_file()
        self.apply_main_toggle_style()

    def apply_main_toggle_style(self):
        is_running = APP_CONFIG.get("is_running", True)
        if is_running:
            self.btn_toggle_bot.setText("STAN PROGRAMU: WŁĄCZONY (Kliknij, aby wstrzymać)")
            self.btn_toggle_bot.setStyleSheet(
                "background-color: #4CAF50; color: white; font-size: 15px; font-weight: bold; padding: 12px;")
            self.setWindowIcon(self.icon_running)
        else:
            self.btn_toggle_bot.setText("STAN PROGRAMU: ZATRZYMANY (Zablokowano wszystkie akcje)")
            self.btn_toggle_bot.setStyleSheet(
                "background-color: #F44336; color: white; font-size: 15px; font-weight: bold; padding: 12px;")
            self.setWindowIcon(self.icon_stopped)

    def setup_main_tab(self):
        layout = QVBoxLayout()
        self.btn_toggle_bot = QPushButton()
        self.btn_toggle_bot.clicked.connect(self.toggle_main_bot)
        layout.addWidget(self.btn_toggle_bot)

        line_top = QFrame()
        line_top.setFrameShape(QFrame.Shape.HLine)
        line_top.setStyleSheet("background-color: #555; margin: 10px 0px;")
        layout.addWidget(line_top)

        self.lbl_title = QLabel("Podgląd radaru")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setStyleSheet("color: #FFC107; font-size: 16px; margin-bottom: 10px;")
        layout.addWidget(self.lbl_title)

        self.lbl_current_ch = QLabel("Obecnie jesteś na: Szukam...")
        self.lbl_current_ch.setStyleSheet("color: #03A9F4; font-size: 18px;")
        self.lbl_current_ch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_current_ch)

        self.lbl_status = QLabel(SHARED_STATE["ch_message"])
        self.lbl_status.setStyleSheet("color: #8BC34A; font-size: 13px; margin-top: 10px;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_status)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #555; margin: 15px 0px;")
        layout.addWidget(line)

        self.lbl_preview_main = QLabel()
        self.lbl_preview_main.setFixedSize(300, 100)
        self.lbl_preview_main.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview_main.setStyleSheet("border: 1px dashed #555; background-color: #151515;")
        layout.addWidget(self.lbl_preview_main, alignment=Qt.AlignmentFlag.AlignCenter)

        self.lbl_confidence = QLabel("Pewność odczytu: --%")
        self.lbl_confidence.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_confidence.setStyleSheet("color: #9E9E9E; font-size: 11px; margin-top: 2px;")
        layout.addWidget(self.lbl_confidence)
        self.tab_main.setLayout(layout)

    def change_alert_prop(self, prop_key, alert_idx, value):
        if prop_key not in APP_CONFIG: APP_CONFIG[prop_key] = {}
        APP_CONFIG[prop_key][str(alert_idx)] = value
        save_config_to_file()

    def change_alert_sound(self, alert_idx, combo_idx, combo_widget):
        sound_opts = ["Brak", "Windows: Exclamation", "Windows: Asterisk", "Windows: Hand",
                      "Własny plik (.wav / .mp3)..."]
        selected = sound_opts[combo_idx]
        if selected == "Własny plik (.wav / .mp3)...":
            path, _ = QFileDialog.getOpenFileName(self, f"Wybierz plik dla Alertu {alert_idx}", "",
                                                  "Dźwięki (*.wav *.mp3)")
            if path:
                if "alert_custom_paths" not in APP_CONFIG: APP_CONFIG["alert_custom_paths"] = {}
                APP_CONFIG["alert_custom_paths"][str(alert_idx)] = path
            else:
                combo_widget.setCurrentText(APP_CONFIG.get("alert_sounds", {}).get(str(alert_idx), "Brak"))
                return
        self.change_alert_prop("alert_sounds", alert_idx, selected)

    def toggle_audio_test(self, checked, alert_idx, btn):
        if checked:
            btn.setText("⏹ Zatrzymaj")
            bot.trigger_alert_sound(alert_idx)
        else:
            btn.setText("▶ Testuj Dźwięk")
            bot.stop_custom_audio()

    def setup_automation_tab(self):
        # 1. Główny układ zakładki
        main_tab_layout = QVBoxLayout(self.tab_automation)
        main_tab_layout.setContentsMargins(0, 0, 0, 0)

        # 2. Utworzenie obszaru przewijania (Scroll Area)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # Wymusza dopasowanie szerokości zawartości do okna
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 3. Utworzenie "pojemnika" (widgetu) na wszystkie opcje
        container = QWidget()

        # 4. Inicjalizacja layoutu (teraz podpinamy go pod kontener, a nie pod self.tab_automation)
        layout = QVBoxLayout(container)

        h_auto_top = QHBoxLayout()
        self.chk_auto_ch = QCheckBox("Włącz auto-zmianę CH")
        self.chk_auto_ch.setChecked(APP_CONFIG.get("auto_ch_enabled"))
        self.chk_auto_ch.toggled.connect(lambda c: [APP_CONFIG.update({"auto_ch_enabled": c}), save_config_to_file()])
        h_auto_top.addWidget(self.chk_auto_ch)

        self.cmb_auto_mode = QComboBox()
        self.cmb_auto_mode.addItems(["Następny Kanał", "Poprzedni Kanał"])
        self.cmb_auto_mode.setCurrentIndex(0 if APP_CONFIG.get("auto_ch_mode") == "next" else 1)
        self.cmb_auto_mode.currentIndexChanged.connect(
            lambda idx: [APP_CONFIG.update({"auto_ch_mode": "next" if idx == 0 else "prev"}), save_config_to_file()])
        h_auto_top.addWidget(self.cmb_auto_mode)

        self.spin_auto_int = QDoubleSpinBox()
        self.spin_auto_int.setRange(0.1, 3600.0)
        self.spin_auto_int.setValue(float(APP_CONFIG.get("auto_ch_interval", 60.0)))
        self.spin_auto_int.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"auto_ch_interval": val}), save_config_to_file()])
        h_auto_top.addWidget(QLabel("co (s):"))
        h_auto_top.addWidget(self.spin_auto_int)
        layout.addLayout(h_auto_top)

        self.chk_channels = {}
        ch_layout = QHBoxLayout()
        allowed = APP_CONFIG.get("allowed_channels", [1, 2, 3, 4, 5, 6])
        for i in range(1, 7):
            chk = QCheckBox(f"CH{i}")
            chk.setChecked(i in allowed)
            chk.toggled.connect(self.update_allowed_channels)
            self.chk_channels[i] = chk
            ch_layout.addWidget(chk)
        layout.addLayout(ch_layout)

        self.lbl_auto_countdown = QLabel("Oczekiwanie: Wyłączone")
        self.lbl_auto_countdown.setStyleSheet("color: #9E9E9E; font-size: 11px;")
        layout.addWidget(self.lbl_auto_countdown)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine))

        self.chk_post_space = QCheckBox("Automatyczny atak z konia (Spamowanie Spacji)")
        self.chk_post_space.setChecked(APP_CONFIG.get("post_hold_space"))
        self.chk_post_space.toggled.connect(
            lambda c: [APP_CONFIG.update({"post_hold_space": c}), save_config_to_file()])
        layout.addWidget(self.chk_post_space)

        h_hold_key = QHBoxLayout()
        self.chk_hold_key = QCheckBox("Trzymaj wciśnięty klawisz (Farming): ")
        self.chk_hold_key.setChecked(APP_CONFIG.get("hold_key_enabled"))
        self.chk_hold_key.toggled.connect(self.toggle_hold_key)
        h_hold_key.addWidget(self.chk_hold_key)
        self.btn_hold_key = QPushButton()
        self.btn_hold_key.setFixedWidth(100)
        self.btn_hold_key.clicked.connect(lambda: self.start_binding("auto_hold_key"))
        h_hold_key.addWidget(self.btn_hold_key)
        layout.addLayout(h_hold_key)

        layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine))

        h_booster = QHBoxLayout()
        self.chk_auto_booster = QCheckBox("Auto Dopalacze (Klawisz)")
        self.chk_auto_booster.setChecked(APP_CONFIG.get("auto_booster_enabled", False))
        self.chk_auto_booster.toggled.connect(
            lambda checked: [APP_CONFIG.update({"auto_booster_enabled": checked}), save_config_to_file()])
        h_booster.addWidget(self.chk_auto_booster)
        self.btn_booster_key = QPushButton()
        self.btn_booster_key.setFixedWidth(80)
        self.btn_booster_key.clicked.connect(lambda: self.start_binding("auto_booster_key"))
        h_booster.addWidget(self.btn_booster_key)
        h_booster.addWidget(QLabel(" co "))
        self.spin_booster_interval = QDoubleSpinBox()
        self.spin_booster_interval.setRange(0.1, 3600.0)
        self.spin_booster_interval.setValue(float(APP_CONFIG.get("auto_booster_interval", 120.0)))
        self.spin_booster_interval.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"auto_booster_interval": val}), save_config_to_file()])
        h_booster.addWidget(self.spin_booster_interval)
        h_booster.addWidget(QLabel("s"))
        layout.addLayout(h_booster)

        # --- AUTO PELERYNKA (Niezależna akcja) ---
        h_indep = QHBoxLayout()
        self.chk_indep = QCheckBox("Auto Pelerynka (Niezależna akcja)")
        self.chk_indep.setChecked(APP_CONFIG.get("indep_action_enabled", False))
        self.chk_indep.toggled.connect(
            lambda checked: [APP_CONFIG.update({"indep_action_enabled": checked}), save_config_to_file()])
        h_indep.addWidget(self.chk_indep)

        self.btn_indep_key = QPushButton()
        self.btn_indep_key.setFixedWidth(80)
        self.btn_indep_key.clicked.connect(lambda: self.start_binding("indep_action_key"))
        h_indep.addWidget(self.btn_indep_key)

        h_indep.addWidget(QLabel(" co "))
        self.spin_indep_interval = QDoubleSpinBox()
        self.spin_indep_interval.setRange(0.1, 3600.0)
        self.spin_indep_interval.setValue(float(APP_CONFIG.get("indep_action_interval", 5.0)))
        self.spin_indep_interval.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"indep_action_interval": val}), save_config_to_file()])
        h_indep.addWidget(self.spin_indep_interval)
        h_indep.addWidget(QLabel("s"))
        layout.addLayout(h_indep)
        # ------------------------------------------

        # --- INTELIGENTNA PELERYNKA (MYSZKA LUB KLAWISZ) ---
        v_smart_cape = QVBoxLayout()

        # Linia 1: Włącznik i Rozwijane Menu Trybu
        h_smart_cape_top = QHBoxLayout()
        self.chk_smart_cape = QCheckBox("Inteligentna pelerynka po CH")
        self.chk_smart_cape.setChecked(APP_CONFIG.get("post_cape_enabled", False))
        self.chk_smart_cape.toggled.connect(
            lambda checked: [APP_CONFIG.update({"post_cape_enabled": checked}), save_config_to_file()])
        h_smart_cape_top.addWidget(self.chk_smart_cape)

        self.cmb_smart_cape_mode = QComboBox()
        self.cmb_smart_cape_mode.addItems(["Myszka (Prawym)", "Klawisz (Klawiatura)"])
        self.cmb_smart_cape_mode.setCurrentIndex(0 if APP_CONFIG.get("smart_cape_mode", "mouse") == "mouse" else 1)
        self.cmb_smart_cape_mode.currentIndexChanged.connect(
            lambda idx: [APP_CONFIG.update({"smart_cape_mode": "mouse" if idx == 0 else "key"}), save_config_to_file(),
                         self.update_smart_cape_ui()])
        h_smart_cape_top.addWidget(self.cmb_smart_cape_mode)
        v_smart_cape.addLayout(h_smart_cape_top)

        # Linia 2: Przycisk konfiguracji (Współrzędne LUB Klawisz)
        h_smart_cape_mid = QHBoxLayout()
        h_smart_cape_mid.setContentsMargins(20, 0, 0, 0)

        # Przycisk Myszki
        self.btn_smart_cape_coords = QPushButton()
        coords = APP_CONFIG.get("smart_cape_coords", [0, 0])
        self.btn_smart_cape_coords.setText(f"Pozycja: {coords[0]}, {coords[1]}")
        self.btn_smart_cape_coords.setFixedWidth(130)
        self.btn_smart_cape_coords.setStyleSheet("background-color: #3F51B5; color: white;")
        self.btn_smart_cape_coords.clicked.connect(self.start_cape_coord_capture)
        h_smart_cape_mid.addWidget(self.btn_smart_cape_coords)

        # Przycisk Klawisza
        self.btn_smart_cape_key = QPushButton()
        self.btn_smart_cape_key.setFixedWidth(130)
        self.btn_smart_cape_key.clicked.connect(lambda: self.start_binding("post_cape_key"))
        h_smart_cape_mid.addWidget(self.btn_smart_cape_key)

        h_smart_cape_mid.addStretch()
        v_smart_cape.addLayout(h_smart_cape_mid)

        # Linia 3: Opóźnienia
        h_smart_cape_bot = QHBoxLayout()
        h_smart_cape_bot.setContentsMargins(20, 0, 0, 0)
        h_smart_cape_bot.addWidget(QLabel("→ 1. klik po: "))
        self.spin_smart_cape_delay1 = QDoubleSpinBox()
        self.spin_smart_cape_delay1.setRange(0.0, 5.0)
        self.spin_smart_cape_delay1.setSingleStep(0.1)
        self.spin_smart_cape_delay1.setValue(float(APP_CONFIG.get("post_cape_delay_1", 0.3)))
        self.spin_smart_cape_delay1.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"post_cape_delay_1": val}), save_config_to_file()])
        h_smart_cape_bot.addWidget(self.spin_smart_cape_delay1)

        h_smart_cape_bot.addWidget(QLabel("s  |  2. klik po: "))
        self.spin_smart_cape_delay2 = QDoubleSpinBox()
        self.spin_smart_cape_delay2.setRange(0.1, 10.0)
        self.spin_smart_cape_delay2.setSingleStep(0.1)
        self.spin_smart_cape_delay2.setValue(float(APP_CONFIG.get("post_cape_delay_2", 2.0)))
        self.spin_smart_cape_delay2.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"post_cape_delay_2": val}), save_config_to_file()])
        h_smart_cape_bot.addWidget(self.spin_smart_cape_delay2)
        h_smart_cape_bot.addWidget(QLabel("s"))
        h_smart_cape_bot.addStretch()
        v_smart_cape.addLayout(h_smart_cape_bot)

        layout.addLayout(v_smart_cape)
        # -----------------------------------------------------------------

        lbl_alerts = QLabel("Alerty Ekranowe (Dodaj obszary w zakładce Edytor)")
        lbl_alerts.setStyleSheet("color: #F44336; font-size: 15px; margin-top: 10px;")
        layout.addWidget(lbl_alerts)

        sound_opts = ["Brak", "Windows: Exclamation", "Windows: Asterisk", "Windows: Hand",
                      "Własny plik (.wav / .mp3)..."]
        action_opts = ["Brak", "Zatrzymaj Bota (Pauza)", "Auto Skille"]
        cond_opts = ["Gdy się pojawi", "Gdy ZNIKNIE (np. koniec buffa)"]

        for i in range(1, 4):
            gb = QGroupBox(f"Konfiguracja Alertu {i}")
            g_layout = QGridLayout()

            chk_en = QCheckBox("Włącz nasłuch tego alertu")
            chk_en.setChecked(APP_CONFIG.get("alerts_enabled", {}).get(str(i), False))
            chk_en.toggled.connect(lambda c, a_idx=i: self.change_alert_prop("alerts_enabled", a_idx, c))
            g_layout.addWidget(chk_en, 0, 0, 1, 2)

            g_layout.addWidget(QLabel("Warunek:"), 1, 0)
            cb_cond = QComboBox()
            cb_cond.addItems(cond_opts)
            curr_cond = APP_CONFIG.get("alert_conditions", {}).get(str(i), "appears")
            cb_cond.setCurrentIndex(0 if curr_cond == "appears" else 1)
            cb_cond.currentIndexChanged.connect(lambda idx, a_idx=i: self.change_alert_prop("alert_conditions", a_idx,
                                                                                            "appears" if idx == 0 else "disappears"))
            g_layout.addWidget(cb_cond, 1, 1)

            g_layout.addWidget(QLabel("Akcja:"), 2, 0)
            cb_act = QComboBox()
            cb_act.addItems(action_opts)
            cb_act.setCurrentText(APP_CONFIG.get("alert_actions", {}).get(str(i), "Brak"))
            cb_act.currentIndexChanged.connect(
                lambda idx, a_idx=i, cb=cb_act: self.change_alert_prop("alert_actions", a_idx, cb.currentText()))
            g_layout.addWidget(cb_act, 2, 1)

            g_layout.addWidget(QLabel("Dźwięk:"), 3, 0)
            cb_sound = QComboBox()
            cb_sound.addItems(sound_opts)
            curr_snd = APP_CONFIG.get("alert_sounds", {}).get(str(i), "Brak")
            cb_sound.setCurrentText(curr_snd if curr_snd in sound_opts else "Brak")
            cb_sound.currentIndexChanged.connect(
                lambda idx, a_idx=i, c_box=cb_sound: self.change_alert_sound(a_idx, idx, c_box))
            g_layout.addWidget(cb_sound, 3, 1)

            # --- NOWY KOD DO WLEJENIA ---

            # 1. Interwał (Co jaki czas ma działać akcja/alert)
            g_layout.addWidget(QLabel("Interwał (s):"), 4, 0)
            spin_int = QDoubleSpinBox()
            spin_int.setRange(1.0, 3600.0)
            spin_int.setValue(float(APP_CONFIG.get("alert_intervals", {}).get(str(i), 5.0)))
            spin_int.valueChanged.connect(lambda val, a_idx=i: self.change_alert_prop("alert_intervals", a_idx, val))
            g_layout.addWidget(spin_int, 4, 1)

            # 2. Głośność i Opcja Pętli
            g_layout.addWidget(QLabel("Głośność / Pętla:"), 5, 0)
            h_vol = QHBoxLayout()
            spin_vol = QSpinBox()
            spin_vol.setRange(0, 100)
            spin_vol.setValue(int(APP_CONFIG.get("alert_volumes", {}).get(str(i), 100)))
            spin_vol.valueChanged.connect(lambda val, a_idx=i: self.change_alert_prop("alert_volumes", a_idx, val))
            h_vol.addWidget(spin_vol)
            h_vol.addWidget(QLabel("%"))

            chk_loop = QCheckBox("Pętla (tylko .mp3/.wav)")
            chk_loop.setChecked(APP_CONFIG.get("alert_loops", {}).get(str(i), False))
            chk_loop.toggled.connect(lambda c, a_idx=i: self.change_alert_prop("alert_loops", a_idx, c))
            h_vol.addWidget(chk_loop)
            g_layout.addLayout(h_vol, 5, 1)

            # 3. Przyciski odtwarzania i zatrzymania (Zamienione na Toggle)
            h_test = QHBoxLayout()
            btn_audio_toggle = QPushButton("▶ Testuj Dźwięk")
            btn_audio_toggle.setCheckable(True)  # Pozwala przyciskowi działać jak przełącznik
            btn_audio_toggle.setStyleSheet("""
                            QPushButton { background-color: #4CAF50; color: white; }
                            QPushButton:checked { background-color: #F44336; color: white; }
                        """)
            btn_audio_toggle.clicked.connect(
                lambda checked, a_idx=i, btn=btn_audio_toggle: self.toggle_audio_test(checked, a_idx, btn))
            h_test.addWidget(btn_audio_toggle)

            g_layout.addLayout(h_test, 6, 0, 1, 2)

            # --- NOWE OPCJE ZAAWANSOWANE ALERTA (De-bounce i CH) ---
            h_adv = QHBoxLayout()

            chk_ign_ch = QCheckBox("Pauzuj podczas zmiany CH")
            chk_ign_ch.setChecked(APP_CONFIG.get("alert_ignore_ch", {}).get(str(i), True))
            chk_ign_ch.toggled.connect(lambda c, a_idx=i: self.change_alert_prop("alert_ignore_ch", a_idx, c))
            h_adv.addWidget(chk_ign_ch)

            chk_deb = QCheckBox("Ignoruj mignięcia (Wymaga 3 odczytów)")
            chk_deb.setChecked(APP_CONFIG.get("alert_debounce", {}).get(str(i), False))
            chk_deb.toggled.connect(lambda c, a_idx=i: self.change_alert_prop("alert_debounce", a_idx, c))
            h_adv.addWidget(chk_deb)

            # Dodajemy to w nowym rzędzie (rząd 7) do siatki
            g_layout.addLayout(h_adv, 7, 0, 1, 2)
            # ---------------------------------------------------------

            gb.setLayout(g_layout)
            layout.addWidget(gb)

        layout.addStretch()
        self.tab_automation.setLayout(layout)
        self.refresh_automation_ui()
        scroll_area.setWidget(container)
        main_tab_layout.addWidget(scroll_area)
        self.update_smart_cape_ui()

    def update_allowed_channels(self):
        allowed = [ch for ch, chk in self.chk_channels.items() if chk.isChecked()]
        if not allowed:
            self.chk_channels[1].setChecked(True)
            allowed = [1]
        APP_CONFIG["allowed_channels"] = allowed
        save_config_to_file()

    def toggle_hold_key(self, checked):
        APP_CONFIG["hold_key_enabled"] = checked
        save_config_to_file()
        vk = APP_CONFIG["hotkeys"].get("auto_hold_key")
        if vk:
            if checked and APP_CONFIG.get("is_running", True):
                bot.pdi_down(vk)
            else:
                bot.pdi_up(vk)

    def refresh_automation_ui(self):
        buttons = [
            ("auto_hold_key", getattr(self, "btn_hold_key", None)),
            ("auto_booster_key", getattr(self, "btn_booster_key", None)),
            ("indep_action_key", getattr(self, "btn_indep_key", None)),
            ("post_cape_key", getattr(self, "btn_smart_cape_key", None))
        ]
        for action_id, btn in buttons:
            if btn is None: continue
            if self.binding_action == action_id:
                btn.setText("Naciśnij...")
                btn.setStyleSheet("background-color: #F44336; color: white;")
            else:
                # Pobiera nazwę klawisza z konfiguracji
                vk = APP_CONFIG["hotkeys"].get(action_id)
                btn.setText(self.get_key_name(vk))
                btn.setStyleSheet("background-color: #3F51B5; color: white;")

    def setup_settings_tab(self):
        layout = QVBoxLayout()

        gb_window = QGroupBox("Wywołanie / Przywracanie Okna")
        l_win = QGridLayout()

        self.btn_show_gui = QPushButton()
        self.btn_show_gui.setFixedWidth(120)
        self.btn_show_gui.clicked.connect(lambda: self.start_binding("show_gui_key"))
        self.hotkey_buttons = {"show_gui_key": self.btn_show_gui}

        l_win.addWidget(QLabel("Skrót wywołujący GUI na wierzch:"), 0, 0)
        l_win.addWidget(self.btn_show_gui, 0, 1)
        l_win.addWidget(QLabel("Pozycja X:"), 1, 0)
        self.spin_x = QSpinBox()
        self.spin_x.setRange(0, 3000)
        self.spin_x.setValue(APP_CONFIG.get("gui_rect", {}).get("x", 100))
        self.spin_x.valueChanged.connect(lambda v: [APP_CONFIG["gui_rect"].update({"x": v}), save_config_to_file()])
        l_win.addWidget(self.spin_x, 1, 1)
        l_win.addWidget(QLabel("Pozycja Y:"), 2, 0)
        self.spin_y = QSpinBox()
        self.spin_y.setRange(0, 2000)
        self.spin_y.setValue(APP_CONFIG.get("gui_rect", {}).get("y", 100))
        self.spin_y.valueChanged.connect(lambda v: [APP_CONFIG["gui_rect"].update({"y": v}), save_config_to_file()])
        l_win.addWidget(self.spin_y, 2, 1)

        gb_window.setLayout(l_win)
        layout.addWidget(gb_window)

        info = QLabel("Konfiguracja Klawiszy (Sprzętowych)")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: #03A9F4; font-size: 16px; margin-top: 10px; margin-bottom: 5px;")
        layout.addWidget(info)

        self.lbl_settings_status = QLabel("Kliknij przycisk, aby ustawić nowy klawisz.")
        self.lbl_settings_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_settings_status.setStyleSheet("color: #BBB; font-size: 12px;")
        layout.addWidget(self.lbl_settings_status)

        grid = QGridLayout()
        actions = [
            ("global_pause_key", "Pauza (Global)"),
            ("macro_seq_key", "Auto Skille (Makro)"),
            ("capture", "Kreator (Przechwyt)"),
            ("next_ch", "Następny Kanał"),
            ("prev_ch", "Poprzedni Kanał"),
            ("ch1", "Zmień na CH1"), ("ch2", "Zmień na CH2"),
            ("ch3", "Zmień na CH3"), ("ch4", "Zmień na CH4"),
            ("ch5", "Zmień na CH5"), ("ch6", "Zmień na CH6")
        ]

        for i, (action_id, desc) in enumerate(actions):
            lbl = QLabel(desc)
            if action_id == "global_pause_key": lbl.setStyleSheet("color: #FF5722;")
            if action_id == "macro_seq_key": lbl.setStyleSheet("color: #9C27B0;")

            btn = QPushButton()
            btn.setFixedWidth(120)
            btn.clicked.connect(lambda checked, act=action_id: self.start_binding(act))
            self.hotkey_buttons[action_id] = btn
            grid.addWidget(lbl, i % 6, (i // 6) * 2)
            grid.addWidget(btn, i % 6, (i // 6) * 2 + 1)

        layout.addLayout(grid)
        layout.addStretch()
        self.tab_settings.setLayout(layout)
        self.refresh_settings_ui()

    def refresh_settings_ui(self):
        for action_id, btn in self.hotkey_buttons.items():
            if self.binding_action == action_id:
                btn.setText("Naciśnij...")
                btn.setStyleSheet("background-color: #F44336; color: white;")
            else:
                vk = APP_CONFIG["hotkeys"].get(action_id)
                btn.setText(self.get_key_name(vk))
                btn.setStyleSheet("background-color: #3F51B5; color: white;")

    def start_binding(self, action_id):
        self.binding_action = action_id
        vk_hold = APP_CONFIG["hotkeys"].get("auto_hold_key")
        if vk_hold: bot.pdi_up(vk_hold)

        msg = "Naciśnij klawisz..."
        if self.tabs.currentWidget() == self.tab_settings:
            self.lbl_settings_status.setText(msg)
            self.lbl_settings_status.setStyleSheet("color: #FFEB3B; font-size: 12px;")

        self.key_states.clear()
        self.refresh_settings_ui()
        self.refresh_automation_ui()

    def check_hardware_keys(self):
        if self.binding_action:
            allowed_vks = [2, 4, 5, 6] + list(range(8, 255))
            for vk in allowed_vks:
                if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                    self.finish_binding(vk)
                    time.sleep(0.3)
                    return

        hotkeys = APP_CONFIG.get("hotkeys", {})
        ignored_actions = ["auto_post_key", "auto_hold_key", "indep_action_key", "auto_booster_key","post_cape_key"]

        is_running = APP_CONFIG.get("is_running", True)
        is_editor_tab = (self.tabs.currentWidget() == self.tab_editor)
        is_wizard_tab = (self.tabs.currentWidget() == self.tab_wizard)

        for action, vk in hotkeys.items():
            if vk is None or action in ignored_actions: continue

            # SPECJALNA LOGIKA DLA "CAPTURE"
            if action == "capture":
                # Klawisz działa TYLKO jak jesteśmy w Edytorze/Kreatorze, ignoruje pauzę bota
                if not (is_editor_tab or is_wizard_tab):
                    continue
            else:
                # Standardowa logika dla reszty bota
                if not is_running and action not in ["global_pause_key", "show_gui_key"]:
                    continue

            pressed = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
            if vk not in self.key_states: self.key_states[vk] = False

            if pressed and not self.key_states[vk]:
                self.key_states[vk] = True
                self.trigger_hotkey_action(action)
            elif not pressed:
                self.key_states[vk] = False

    def finish_binding(self, vk):
        for act, assigned_vk in APP_CONFIG["hotkeys"].items():
            if assigned_vk == vk and act != self.binding_action:
                if self.tabs.currentWidget() == self.tab_settings:
                    self.lbl_settings_status.setText(f"Odrzucono! '{self.get_key_name(vk)}' jest używany.")
                    self.lbl_settings_status.setStyleSheet("color: #F44336; font-size: 12px; font-weight: bold;")
                self.binding_action = None
                self.refresh_settings_ui()
                self.refresh_automation_ui()
                if APP_CONFIG.get("is_running", True) and APP_CONFIG.get("hold_key_enabled"):
                    old_vk = APP_CONFIG["hotkeys"].get("auto_hold_key")
                    if old_vk: bot.pdi_down(old_vk)
                return

        APP_CONFIG["hotkeys"][self.binding_action] = vk
        save_config_to_file()

        if APP_CONFIG.get("is_running", True) and APP_CONFIG.get("hold_key_enabled"):
            new_vk = APP_CONFIG["hotkeys"].get("auto_hold_key")
            if new_vk: bot.pdi_down(new_vk)

        if self.tabs.currentWidget() == self.tab_settings:
            self.lbl_settings_status.setText(f"Zapisano! Klawisz: {self.get_key_name(vk)}")
            self.lbl_settings_status.setStyleSheet("color: #8BC34A; font-size: 12px;")

        self.binding_action = None
        self.refresh_settings_ui()
        self.refresh_automation_ui()

    def trigger_hotkey_action(self, action):
        if action == "global_pause_key":
            self.toggle_main_bot()
        elif action == "show_gui_key":
            # Jeśli okno jest na wierzchu i jest aktywne -> zminimalizuj
            if self.isActiveWindow() and not self.isMinimized():
                self.showMinimized()
            # W przeciwnym razie -> przywróć i ustaw na zadanej pozycji
            else:
                rect = APP_CONFIG.get("gui_rect", {"x": 100, "y": 100, "w": 650, "h": 850})
                self.showNormal()
                self.activateWindow()
                self.raise_()
                self.setGeometry(rect["x"], rect["y"], rect["w"], rect["h"])
        elif action == "macro_seq_key":
            threading.Thread(target=bot.execute_auto_skills, daemon=True).start()
        elif action == "next_ch":
            bot.next_channel_routine()
        elif action == "prev_ch":
            bot.prev_channel_routine()
        elif action.startswith("ch"):
            threading.Thread(target=bot.change_channel_routine, args=(int(action[-1]),), daemon=True).start()
        elif action == "capture":
            if self.tabs.currentWidget() == self.tab_wizard:
                self.handle_wizard_capture()
            elif self.tabs.currentWidget() == self.tab_editor:
                self.handle_editor_capture(self.list_editor.currentRow())

    def update_ui(self):
        is_running = APP_CONFIG.get("is_running", True)
        if is_running and "ZATRZYMANY" in self.btn_toggle_bot.text():
            self.apply_main_toggle_style()
        elif not is_running and "WŁĄCZONY" in self.btn_toggle_bot.text():
            self.apply_main_toggle_style()

        if APP_CONFIG.get("auto_ch_enabled") and not SHARED_STATE["is_changing_ch"] and is_running:
            rem_time = self.next_auto_ch_time - time.time()
            if rem_time <= 0:
                if APP_CONFIG.get("auto_ch_mode") == "next":
                    bot.next_channel_routine()
                else:
                    bot.prev_channel_routine()
                self.next_auto_ch_time = time.time() + float(APP_CONFIG.get("auto_ch_interval"))
                self.lbl_auto_countdown.setText("Oczekiwanie: Wykonuję skok...")
            else:
                self.lbl_auto_countdown.setText(f"Następ skok za: {rem_time:.1f}s")
        else:
            if not is_running:
                self.lbl_auto_countdown.setText("ZAPRZESTANO (WYŁĄCZONY)")
            else:
                self.lbl_auto_countdown.setText("Wyłączone")

        if self.tabs.currentWidget() == self.tab_main:
            self.lbl_status.setText(SHARED_STATE["ch_message"])
            current_ch, confidence = bot.get_current_channel()

            if SHARED_STATE["latest_frame"] is not None:
                pixmap = cv2_to_qpixmap(SHARED_STATE["latest_frame"])
                if pixmap: self.lbl_preview_main.setPixmap(
                    pixmap.scaled(self.lbl_preview_main.width(), self.lbl_preview_main.height(),
                                  Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

            if current_ch:
                self.lbl_current_ch.setText(f"Obecnie jesteś na: CH{current_ch}")
                self.lbl_confidence.setStyleSheet(
                    f"color: {'#4CAF50' if confidence > 0.85 else '#FFC107'}; font-size: 11px; margin-top: 2px;")
            else:
                self.lbl_current_ch.setText("Obecnie jesteś na: Szukam...")
                self.lbl_confidence.setStyleSheet("color: #9E9E9E; font-size: 11px; margin-top: 2px;")
            self.lbl_confidence.setText(f"Pewność odczytu: {confidence * 100:.1f}%")

    def setup_wizard_tab(self):
        layout = QVBoxLayout()
        self.lbl_wizard_progress = QLabel("Krok 1 / 14")
        self.lbl_wizard_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_wizard_progress.setStyleSheet("font-size: 16px; color: #E040FB;")
        layout.addWidget(self.lbl_wizard_progress)
        self.lbl_wizard_task = QLabel(self.WIZARD_STEPS[0]["desc"])
        self.lbl_wizard_task.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_wizard_task.setStyleSheet(
            f"font-size: 13px; color: {self.WIZARD_STEPS[0]['color']}; margin: 10px 0px;")
        layout.addWidget(self.lbl_wizard_task)
        self.lbl_wizard_status = QLabel(self.wizard_message)
        self.lbl_wizard_status.setStyleSheet("color: #FFEB3B; font-size: 13px; font-weight: normal;")
        self.lbl_wizard_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_wizard_status)
        layout.addWidget(QLabel("Zrzut z ostatniego kroku:"))
        self.lbl_preview_wizard = QLabel("Pusto")
        self.lbl_preview_wizard.setFixedSize(300, 150)
        self.lbl_preview_wizard.setStyleSheet("border: 1px dashed #555;")
        layout.addWidget(self.lbl_preview_wizard, alignment=Qt.AlignmentFlag.AlignCenter)
        btn_reset = QPushButton("Zacznij kreator od nowa")
        btn_reset.setStyleSheet("background-color: #F44336;")
        btn_reset.clicked.connect(self.reset_wizard)
        layout.addWidget(btn_reset)
        self.tab_wizard.setLayout(layout)

    def reset_wizard(self):
        self.wizard_step = 0
        self.wizard_points.clear()
        self.wizard_message = f"Zresetowano! Naciśnij klawisz w LEWYM-GÓRNYM rogu dla:\n{self.WIZARD_STEPS[0]['desc']}"
        self.lbl_wizard_status.setText(self.wizard_message)

    def handle_wizard_capture(self):
        if self.wizard_step >= len(self.WIZARD_STEPS): return
        x, y = pyautogui.position()
        if len(self.wizard_points) == 0:
            self.wizard_points.append((x, y))
            self.wizard_message = f"🎯 Punkt 1 Zapisany!\nTeraz najedź w PRAWY-DOLNY róg i wciśnij klawisz."
        else:
            self.wizard_points.append((x, y))
            p1, p2 = self.wizard_points[0], self.wizard_points[1]
            monitor = {"top": min(p1[1], p2[1]), "left": min(p1[0], p2[0]), "width": max(abs(p1[0] - p2[0]), 2),
                       "height": max(abs(p1[1] - p2[1]), 2)}
            step_data = self.WIZARD_STEPS[self.wizard_step]
            with mss.mss() as sct:
                img_bgra = np.array(sct.grab(monitor))
                img_gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
                if step_data["type"] == "monitor":
                    APP_CONFIG[step_data["key"]] = monitor
                elif step_data["type"] == "image":
                    cv2.imwrite(step_data["key"] + ".png", img_gray)
            save_config_to_file()
            load_templates()
            self.wizard_step += 1
            self.wizard_points.clear()
            if self.wizard_step < len(self.WIZARD_STEPS):
                self.wizard_message = f"✅ Zapisano!\nTeraz klawisz (Lewy-Górny) dla:\n{self.WIZARD_STEPS[self.wizard_step]['desc']}"
                self.lbl_wizard_task.setText(self.WIZARD_STEPS[self.wizard_step]['desc'])
            else:
                self.wizard_message = "🎉 KONIEC! Wszystko skonfigurowane idealnie."
        self.lbl_wizard_status.setText(self.wizard_message)

    def setup_editor_tab(self):
        layout = QVBoxLayout()

        self.chk_overlay = QCheckBox("Pokaż Overlay (Rysuje prostokąty na ekranie)")
        self.chk_overlay.setStyleSheet("color: #03A9F4; font-weight: bold; margin-bottom: 5px;")
        self.chk_overlay.toggled.connect(self.toggle_overlay)
        layout.addWidget(self.chk_overlay)

        self.list_editor = QListWidget()
        items = ["0. Obszar na ekranie: Czytanie CH (State)", "1. Obszar na ekranie: Przyciski CH (Btn)"]
        for i in range(1, 7): items.extend(
            [f"{i * 2}. Obrazek: Napis 'CH{i}'", f"{i * 2 + 1}. Obrazek: Przycisk 'CH{i}'"])
        for i in range(1, 7): items.append(f"{13 + i}. Współrzędne kliknięcia 'CH{i}'")
        for i in range(1, 4): items.extend(
            [f"{19 + (i - 1) * 2 + 1}. Obszar na ekranie: Alert {i}", f"{19 + (i - 1) * 2 + 2}. Obrazek: Alert {i}"])

        self.list_editor.addItems(items)
        self.list_editor.setCurrentRow(0)
        self.list_editor.currentRowChanged.connect(self.refresh_editor_preview)
        layout.addWidget(self.list_editor)

        self.lbl_editor_status = QLabel(self.editor_message)
        self.lbl_editor_status.setStyleSheet("color: #FFEB3B; font-size: 12px; margin-top: 5px;")
        self.lbl_editor_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_editor_status)

        self.lbl_preview_editor = QLabel("Wybierz z listy...")
        self.lbl_preview_editor.setFixedSize(300, 200)
        self.lbl_preview_editor.setStyleSheet("border: 1px solid #777;")
        layout.addWidget(self.lbl_preview_editor, alignment=Qt.AlignmentFlag.AlignCenter)
        self.tab_editor.setLayout(layout)

    def toggle_overlay(self, checked):
        if checked:
            self.overlay.show()
        else:
            self.overlay.hide()

    def handle_editor_capture(self, mode_index):
        if mode_index < 0: return

        if mode_index >= 20:
            rel_idx = mode_index - 20
            alert_num = (rel_idx // 2) + 1
            is_img = (rel_idx % 2) != 0

            x, y = pyautogui.position()
            if len(self.editor_points) == 0:
                self.editor_points.append((x, y))
                self.editor_message = f"🎯 Zapisano Punkt 1 (Lewy-Górny) dla Alertu {alert_num}."
            else:
                self.editor_points.append((x, y))
                p1, p2 = self.editor_points[0], self.editor_points[1]
                monitor = {"top": min(p1[1], p2[1]), "left": min(p1[0], p2[0]), "width": max(abs(p1[0] - p2[0]), 2),
                           "height": max(abs(p1[1] - p2[1]), 2)}

                if not is_img:
                    APP_CONFIG[f"alert_{alert_num}_monitor"] = monitor
                    save_config_to_file()
                    self.editor_message = f"✅ Zapisano obszar dla Alert {alert_num}!"
                else:
                    with mss.mss() as sct:
                        img_bgra = np.array(sct.grab(monitor))
                        img_gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
                        cv2.imwrite(f"alert_{alert_num}.png", img_gray)
                    load_templates()
                    self.editor_message = f"✅ Zapisano obrazek dla Alert {alert_num}!"

                self.editor_points.clear()
                self.refresh_editor_preview()
            self.lbl_editor_status.setText(self.editor_message)
            return

        if mode_index >= 14 and mode_index < 20:
            ch_num = mode_index - 13
            x, y = pyautogui.position()
            if "ch_coords" not in APP_CONFIG: APP_CONFIG["ch_coords"] = {}
            APP_CONFIG["ch_coords"][str(ch_num)] = [x, y]
            save_config_to_file()
            self.editor_message = f"✅ Zapisano współrzędne kliknięcia CH{ch_num}: X={x}, Y={y}"
            self.editor_points.clear()
            self.refresh_editor_preview()
            self.lbl_editor_status.setText(self.editor_message)
            return

        x, y = pyautogui.position()
        if len(self.editor_points) == 0:
            self.editor_points.append((x, y))
            self.editor_message = "🎯 Zapisano Punkt 1 (Lewy-Górny).\nTeraz Prawy-Dolny róg i klawisz przechwytywania."
        else:
            self.editor_points.append((x, y))
            p1, p2 = self.editor_points[0], self.editor_points[1]
            monitor = {"top": min(p1[1], p2[1]), "left": min(p1[0], p2[0]), "width": max(abs(p1[0] - p2[0]), 2),
                       "height": max(abs(p1[1] - p2[1]), 2)}
            with mss.mss() as sct:
                if mode_index == 0:
                    APP_CONFIG["state_monitor"] = monitor
                elif mode_index == 1:
                    APP_CONFIG["btn_monitor"] = monitor
                else:
                    img_bgra = np.array(sct.grab(monitor))
                    img_gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
                    idx_offset = mode_index - 2
                    ch_num = (idx_offset // 2) + 1
                    is_btn = (idx_offset % 2) != 0
                    filename = f"ch{ch_num}_btn.png" if is_btn else f"ch{ch_num}_state.png"
                    cv2.imwrite(filename, img_gray)
            save_config_to_file()
            load_templates()
            self.editor_message = "✅ Pomyślnie podmieniono wybrany element!"
            self.editor_points.clear()
            self.refresh_editor_preview()
        self.lbl_editor_status.setText(self.editor_message)

    def refresh_editor_preview(self):
        idx = self.list_editor.currentRow()
        if idx < 0: return
        pixmap = None

        if idx >= 20:
            rel_idx = idx - 20
            alert_num = (rel_idx // 2) + 1
            is_img = (rel_idx % 2) != 0

            if not is_img:
                monitor = APP_CONFIG.get(f"alert_{alert_num}_monitor")
                if monitor and monitor.get("width", 0) > 0:
                    with mss.mss() as sct: pixmap = cv2_to_qpixmap(np.array(sct.grab(monitor)))
            else:
                img_cv = alert_templates.get(alert_num)
                if img_cv is not None: pixmap = cv2_to_qpixmap(img_cv)

            if pixmap is not None:
                self.lbl_preview_editor.setPixmap(
                    pixmap.scaled(self.lbl_preview_editor.width(), self.lbl_preview_editor.height(),
                                  Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                self.lbl_preview_editor.setText(f"Brak zapisanych danych dla Alertu {alert_num}.")
            return

        if idx >= 14:
            ch_num = str(idx - 13)
            coords = APP_CONFIG.get("ch_coords", {}).get(ch_num, [0, 0])
            self.lbl_preview_editor.clear()
            self.lbl_preview_editor.setText(
                f"Zapisane współrzędne dla CH{ch_num}:\nOś X: {coords[0]}\nOś Y: {coords[1]}")
            return

        if idx == 0:
            monitor = APP_CONFIG.get("state_monitor")
            if monitor and monitor.get("width", 0) > 0:
                with mss.mss() as sct: pixmap = cv2_to_qpixmap(np.array(sct.grab(monitor)))
        elif idx == 1:
            monitor = APP_CONFIG.get("btn_monitor")
            if monitor and monitor.get("width", 0) > 0:
                with mss.mss() as sct: pixmap = cv2_to_qpixmap(np.array(sct.grab(monitor)))
        else:
            ch_num = ((idx - 2) // 2) + 1
            is_btn = ((idx - 2) % 2) != 0
            img_cv = btn_templates.get(ch_num) if is_btn else state_templates.get(ch_num)
            if img_cv is not None: pixmap = cv2_to_qpixmap(img_cv)

        if pixmap is not None:
            self.lbl_preview_editor.setPixmap(
                pixmap.scaled(self.lbl_preview_editor.width(), self.lbl_preview_editor.height(),
                              Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.lbl_preview_editor.setText("Brak zapisanych danych.")

    def moveEvent(self, event):
        super().moveEvent(event)
        # Aktualizujemy "na żywo" wartości w spinboxach w zakładce Ustawienia,
        # żeby pokazywały faktyczne współrzędne w trakcie przeciągania okna
        if hasattr(self, 'spin_x') and hasattr(self, 'spin_y'):
            self.spin_x.blockSignals(True)
            self.spin_y.blockSignals(True)
            self.spin_x.setValue(self.x())
            self.spin_y.setValue(self.y())
            self.spin_x.blockSignals(False)
            self.spin_y.blockSignals(False)

    def start_cape_coord_capture(self):
        # Uruchamia 3-sekundowe odliczanie
        self.btn_smart_cape_coords.setText("Najedź i czekaj (3s)...")
        self.btn_smart_cape_coords.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;")
        QTimer.singleShot(3000, self.finish_cape_coord_capture)

    def finish_cape_coord_capture(self):
        # Pobiera koordynaty i zapisuje je w konfiguracji
        x, y = pyautogui.position()
        APP_CONFIG["smart_cape_coords"] = [x, y]
        save_config_to_file()
        self.btn_smart_cape_coords.setText(f"Pozycja: {x}, {y}")
        self.btn_smart_cape_coords.setStyleSheet("background-color: #3F51B5; color: white;")

    def update_smart_cape_ui(self):
        if hasattr(self, 'cmb_smart_cape_mode') and hasattr(self, 'btn_smart_cape_coords') and hasattr(self,
                                                                                                       'btn_smart_cape_key'):
            mode = APP_CONFIG.get("smart_cape_mode", "mouse")
            if mode == "mouse":
                self.btn_smart_cape_coords.setVisible(True)
                self.btn_smart_cape_key.setVisible(False)
            else:
                self.btn_smart_cape_coords.setVisible(False)
                self.btn_smart_cape_key.setVisible(True)

    def closeEvent(self, event):
        # Przed zamknięciem programu zapisujemy aktualne X i Y do pliku
        if "gui_rect" not in APP_CONFIG:
            APP_CONFIG["gui_rect"] = {}
        APP_CONFIG["gui_rect"]["x"] = self.x()
        APP_CONFIG["gui_rect"]["y"] = self.y()
        save_config_to_file()
        super().closeEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    space_filter = IgnoreSpaceFilter()
    app.installEventFilter(space_filter)

    threading.Thread(target=bot.horse_attack_worker, daemon=True).start()
    threading.Thread(target=bot.independent_action_worker, daemon=True).start()
    threading.Thread(target=bot.auto_booster_worker, daemon=True).start()
    threading.Thread(target=bot.alert_monitor_worker, daemon=True).start()

    hud = ChangerHUD()
    hud.show()
    sys.exit(app.exec())
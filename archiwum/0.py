import sys
import cv2
import numpy as np
import mss
import pyautogui
import pydirectinput
import ctypes
import time
import threading
import json
import os

from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
                             QPushButton, QFrame, QTabWidget, QComboBox, QGridLayout,
                             QDoubleSpinBox, QCheckBox, QSpinBox)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt, QTimer, QObject, QEvent

# Minimalizujemy wbudowane opóźnienia biblioteki pydirectinput
pydirectinput.PAUSE = 0.01

# ==========================================
# CTYPES - SPRZĘTOWE SYMULOWANIE KLAWISZY (SCANCODES)
# ==========================================
SendInput = ctypes.windll.user32.SendInput

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short),
                ("wParamH", ctypes.c_ushort)]


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]


def press_key_hardware(hexKeyCode):
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, hexKeyCode, 0x0008, 0, ctypes.pointer(extra))  # 0x0008 = KEYEVENTF_SCANCODE
    x = Input(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def release_key_hardware(hexKeyCode):
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, hexKeyCode, 0x0008 | 0x0002, 0, ctypes.pointer(extra))  # 0x0002 = KEYEVENTF_KEYUP
    x = Input(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


# ==========================================
# 1. ZMIENNE GLOBALNE I WZORCE
# ==========================================

THRESHOLD_STATE = 120
THRESHOLD_BTN = 150

APP_CONFIG = {
    "is_running": True,
    "state_monitor": {'top': 100, 'left': 100, 'width': 150, 'height': 30},
    "btn_monitor": {'top': 150, 'left': 100, 'width': 50, 'height': 200},
    "click_mode": "image",
    "change_delay": 1.0,
    "ch_coords": {"1": [0, 0], "2": [0, 0], "3": [0, 0], "4": [0, 0], "5": [0, 0], "6": [0, 0]},

    # USTAWIENIA AUTOMATYZACJI
    "auto_ch_enabled": False,
    "auto_ch_mode": "next",
    "auto_ch_interval": 60.0,
    "allowed_channels": [1, 2, 3, 4, 5, 6],

    "post_hold_space": False,
    "post_press_enabled": False,
    "post_press_delay": 0.0,
    "post_press_count": 1,
    "hold_key_enabled": False,

    # NIEZALEŻNY AUTO-KLIKACZ W TLE
    "indep_action_enabled": False,
    "indep_action_interval": 5.0,

    "hotkeys": {
        "next_ch": 0x09,
        "prev_ch": None,
        "ch1": 0x61, "ch2": 0x62, "ch3": 0x63,
        "ch4": 0x64, "ch5": 0x65, "ch6": 0x66,
        "capture": 0x76,
        "auto_post_key": None,
        "auto_hold_key": None,
        "indep_action_key": None  # Klawisz dla niezależnego klikacza
    }
}

VK_DICT = {
    0x02: "PPM (Mysz)", 0x04: "Środek (Mysz)", 0x05: "Boczny Tył", 0x06: "Boczny Przód",
    0x09: "TAB", 0x1B: "ESC", 0x20: "SPACJA", 0x0D: "ENTER",
    0x10: "SHIFT", 0x11: "CTRL", 0x12: "ALT", 0x76: "F7",
    0xC0: "` (Tylda)",  # Dodana łatka dla klawisza tyldy
}
for i in range(1, 13): VK_DICT[0x6F + i] = f"F{i}"
for i in range(0x41, 0x5B): VK_DICT[i] = chr(i)
for i in range(0x30, 0x3A): VK_DICT[i] = chr(i)
for i in range(0x60, 0x6A): VK_DICT[i] = f"NUM {i - 0x60}"

VK_TO_PDI = {
    0x02: 'right', 0x04: 'middle',
    0x09: 'tab', 0x1B: 'esc', 0x20: 'space', 0x0D: 'enter',
    0x10: 'shift', 0x11: 'ctrl', 0x12: 'alt', 0x76: 'f7'
}
for i in range(0x30, 0x3A): VK_TO_PDI[i] = chr(i)
for i in range(0x41, 0x5B): VK_TO_PDI[i] = chr(i).lower()
for i in range(1, 13): VK_TO_PDI[0x6F + i] = f"f{i}"
for i in range(0x60, 0x6A): VK_TO_PDI[i] = f"num{i - 0x60}"

state_templates = {}
btn_templates = {}

is_changing_ch = False
is_horse_attacking = False
ch_message = "Gotowy do pracy"
latest_frame = None


def load_templates():
    global state_templates, btn_templates
    state_templates.clear()
    btn_templates.clear()
    for i in range(1, 7):
        if os.path.exists(f"ch{i}_state.png"): state_templates[i] = cv2.imread(f"ch{i}_state.png", cv2.IMREAD_GRAYSCALE)
        if os.path.exists(f"ch{i}_btn.png"): btn_templates[i] = cv2.imread(f"ch{i}_btn.png", cv2.IMREAD_GRAYSCALE)


def save_config_to_file():
    try:
        with open("changer_config.json", "w", encoding="utf-8") as f:
            json.dump(APP_CONFIG, f, indent=4)
    except Exception:
        pass


def pdi_press(vk):
    key = VK_TO_PDI.get(vk)
    if not key: return
    if key in ['right', 'middle']:
        pydirectinput.click(button=key)
    else:
        pydirectinput.press(key)


def pdi_down(vk):
    key = VK_TO_PDI.get(vk)
    if not key: return
    if key in ['right', 'middle']:
        pydirectinput.mouseDown(button=key)
    else:
        pydirectinput.keyDown(key)


def pdi_up(vk):
    key = VK_TO_PDI.get(vk)
    if not key: return
    if key in ['right', 'middle']:
        pydirectinput.mouseUp(button=key)
    else:
        pydirectinput.keyUp(key)


# ==========================================
# WĄTEK ATAKU Z KONIA (CTYPES)
# ==========================================
def horse_attack_worker():
    """
    Używa sprzętowych scancodów (ctypes) oraz odpowiednich opóźnień (50ms w dół, 20ms w górę),
    aby zminimalizować gubienie ciosów przez silnik gry.
    """
    global is_horse_attacking, is_changing_ch
    SPACE_SCANCODE = 0x39  # Sprzętowy kod Spacji

    while True:
        should_attack = is_horse_attacking and APP_CONFIG.get("is_running", True) and not is_changing_ch

        if should_attack:
            press_key_hardware(SPACE_SCANCODE)
            time.sleep(0.05)  # Trzyma klawisz przez 50 ms
            release_key_hardware(SPACE_SCANCODE)
            time.sleep(0.02)  # Puszcza klawisz na 20 ms
        else:
            time.sleep(0.05)


# ==========================================
# WĄTEK NIEZALEŻNEGO AUTO-KLIKACZA
# ==========================================
def independent_action_worker():
    """
    Niezależny wątek, który wciska wyznaczony klawisz co X sekund.
    Używa sprzętowych scancodów (ctypes) z opóźnieniem, aby był niezawodny w grze.
    """
    last_press_time = time.time()
    while True:
        is_running = APP_CONFIG.get("is_running", True)
        is_enabled = APP_CONFIG.get("indep_action_enabled", False)

        if is_running and is_enabled:
            interval = float(APP_CONFIG.get("indep_action_interval", 5.0))

            if time.time() - last_press_time >= interval:
                vk = APP_CONFIG.get("hotkeys", {}).get("indep_action_key")
                if vk:
                    # Tłumaczenie Virtual Key (VK) prosto na sprzętowy Scancode
                    scancode = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
                    if scancode:
                        press_key_hardware(scancode)
                        time.sleep(0.05)  # Trzymamy klawisz 50ms, żeby gra go zauważyła
                        release_key_hardware(scancode)

                last_press_time = time.time()

        time.sleep(0.1)  # Lekki sen, aby nie obciążać procesora


# ==========================================
# KREATOR I EDYTOR RĘCZNY
# ==========================================
WIZARD_STEPS = [
    {"type": "monitor", "key": "state_monitor",
     "desc": "OBSZAR ODCZYTU:\nZaznacz prostokąt, w którym pojawia się\nnapis np. 'Sovelia, CH1' (obejmij też nad i pod).",
     "color": "#03A9F4"},
    {"type": "monitor", "key": "btn_monitor",
     "desc": "OBSZAR PRZYCISKÓW:\nZaznacz jeden, długi pionowy prostokąt\nobejmujący WSZYSTKIE przyciski CH1-CH6.",
     "color": "#03A9F4"}
]
for i in range(1, 7):
    WIZARD_STEPS.append(
        {"type": "image", "key": f"ch{i}_state", "desc": f"OBRAZEK (Tekst):\nWytnij IDEALNIE sam tekst 'CH{i}'.",
         "color": "#E91E63"})
    WIZARD_STEPS.append(
        {"type": "image", "key": f"ch{i}_btn", "desc": f"OBRAZEK (Przycisk):\nWytnij IDEALNIE kółko 'CH{i}'.",
         "color": "#9C27B0"})

wizard_step = 0
wizard_points = []
wizard_message = "Naciśnij ZBINDOWANY KLAWISZ w LEWYM-GÓRNYM rogu pierwszego obszaru."
wizard_last_img = None
editor_points = []
editor_message = "Wybierz z listy u góry co chcesz podmienić i użyj klawisza przechwytywania."


def reset_wizard():
    global wizard_step, wizard_points, wizard_message, wizard_last_img
    wizard_step = 0
    wizard_points.clear()
    wizard_message = f"Zresetowano! Naciśnij klawisz w LEWYM-GÓRNYM rogu dla:\n{WIZARD_STEPS[0]['desc']}"
    wizard_last_img = None


def handle_wizard_capture():
    global wizard_step, wizard_points, wizard_message, wizard_last_img
    if wizard_step >= len(WIZARD_STEPS): return
    x, y = pyautogui.position()
    if len(wizard_points) == 0:
        wizard_points.append((x, y))
        wizard_message = f"🎯 Punkt 1 Zapisany!\nTeraz najedź w PRAWY-DOLNY róg i wciśnij klawisz."
    else:
        wizard_points.append((x, y))
        p1, p2 = wizard_points[0], wizard_points[1]
        monitor = {"top": min(p1[1], p2[1]), "left": min(p1[0], p2[0]), "width": max(abs(p1[0] - p2[0]), 2),
                   "height": max(abs(p1[1] - p2[1]), 2)}
        step_data = WIZARD_STEPS[wizard_step]
        with mss.mss() as sct:
            img_bgra = np.array(sct.grab(monitor))
            img_gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
            wizard_last_img = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
            if step_data["type"] == "monitor":
                APP_CONFIG[step_data["key"]] = monitor
            elif step_data["type"] == "image":
                cv2.imwrite(step_data["key"] + ".png", img_gray)
        save_config_to_file()
        load_templates()
        wizard_step += 1
        wizard_points.clear()
        if wizard_step < len(WIZARD_STEPS):
            wizard_message = f"✅ Zapisano!\nTeraz klawisz (Lewy-Górny) dla:\n{WIZARD_STEPS[wizard_step]['desc']}"
        else:
            wizard_message = "🎉 KONIEC! Wszystko skonfigurowane idealnie."


def handle_editor_capture(mode_index):
    global editor_points, editor_message
    if mode_index >= 14:
        ch_num = mode_index - 13
        x, y = pyautogui.position()
        if "ch_coords" not in APP_CONFIG: APP_CONFIG["ch_coords"] = {}
        APP_CONFIG["ch_coords"][str(ch_num)] = [x, y]
        save_config_to_file()
        editor_message = f"✅ Zapisano współrzędne kliknięcia CH{ch_num}: X={x}, Y={y}"
        editor_points.clear()
        if hasattr(hud, 'refresh_editor_preview'): hud.refresh_editor_preview()
        return

    x, y = pyautogui.position()
    if len(editor_points) == 0:
        editor_points.append((x, y))
        editor_message = "🎯 Zapisano Punkt 1 (Lewy-Górny).\nTeraz Prawy-Dolny róg i klawisz przechwytywania."
    else:
        editor_points.append((x, y))
        p1, p2 = editor_points[0], editor_points[1]
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
        editor_message = "✅ Pomyślnie podmieniono wybrany element!"
        editor_points.clear()
        if hasattr(hud, 'refresh_editor_preview'): hud.refresh_editor_preview()


# ==========================================
# LOGIKA ZMIANY KANAŁÓW I AUTOMATYZACJI
# ==========================================
def get_current_channel():
    global latest_frame
    if not state_templates: return None, 0.0
    with mss.mss() as sct:
        monitor = APP_CONFIG.get("state_monitor")
        if not monitor: return None, 0.0
        try:
            img_array = np.array(sct.grab(monitor))
            screen_gray = cv2.cvtColor(img_array, cv2.COLOR_BGRA2GRAY)
            _, screen_thresh = cv2.threshold(screen_gray, THRESHOLD_STATE, 255, cv2.THRESH_BINARY)
            debug_img = cv2.cvtColor(screen_thresh, cv2.COLOR_GRAY2BGR)
        except Exception:
            return None, 0.0

        best_match_ch = None
        highest_confidence = 0.0
        for ch_num, template in state_templates.items():
            if template is None: continue
            if template.shape[0] > screen_thresh.shape[0] or template.shape[1] > screen_thresh.shape[1]: continue
            _, template_thresh = cv2.threshold(template, THRESHOLD_STATE, 255, cv2.THRESH_BINARY)
            result = cv2.matchTemplate(screen_thresh, template_thresh, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > highest_confidence:
                highest_confidence = max_val
                best_match_ch = ch_num

        latest_frame = debug_img
        if highest_confidence > 0.70: return best_match_ch, highest_confidence
        return None, highest_confidence


def execute_post_change_automation():
    def automation_routine():
        global is_horse_attacking

        # 1. Ciągłe trzymanie klawisza (Farming)
        if APP_CONFIG.get("hold_key_enabled"):
            vk_hold = APP_CONFIG.get("hotkeys", {}).get("auto_hold_key")
            if vk_hold: pdi_down(vk_hold)

        # 2. Natychmiastowe włączenie ataku spacją
        if APP_CONFIG.get("post_hold_space"):
            is_horse_attacking = True

        # 3. Dodatkowe kliknięcie (np. pelerynki) po zmianie CH
        if APP_CONFIG.get("post_press_enabled"):
            vk_post = APP_CONFIG.get("hotkeys", {}).get("auto_post_key")
            if vk_post:
                delay = APP_CONFIG.get("post_press_delay", 0.0)
                if delay > 0:
                    time.sleep(delay)

                count = APP_CONFIG.get("post_press_count", 1)
                for _ in range(count):
                    pdi_press(vk_post)
                    time.sleep(0.05)

    threading.Thread(target=automation_routine, daemon=True).start()


def change_channel_routine(target_ch):
    global ch_message, is_changing_ch, is_horse_attacking
    if is_changing_ch: return

    is_changing_ch = True
    is_horse_attacking = False

    try:
        mode = APP_CONFIG.get("click_mode", "image")
        delay = APP_CONFIG.get("change_delay", 1.0)
        success = False

        if mode == "coords":
            coords = APP_CONFIG.get("ch_coords", {}).get(str(target_ch), [0, 0])
            if coords == [0, 0]:
                ch_message = f"Błąd: Brak przypisanych współrzędnych dla CH{target_ch}!"
                return
            ch_message = f"Klikam we współrzędne CH{target_ch}..."
            orig_x, orig_y = pyautogui.position()

            pydirectinput.moveTo(coords[0], coords[1])
            time.sleep(0.05)
            pydirectinput.mouseDown()
            time.sleep(0.05)
            pydirectinput.mouseUp()
            time.sleep(0.1)
            pydirectinput.moveTo(orig_x, orig_y)
            success = True
        else:
            template = btn_templates.get(target_ch)
            if template is None:
                ch_message = f"Brak pliku obrazka ch{target_ch}_btn.png!"
                return

            with mss.mss() as sct:
                monitor = APP_CONFIG.get("btn_monitor")
                if not monitor: return
                img_array = np.array(sct.grab(monitor))
                screen_gray = cv2.cvtColor(img_array, cv2.COLOR_BGRA2GRAY)
                _, screen_thresh = cv2.threshold(screen_gray, THRESHOLD_BTN, 255, cv2.THRESH_BINARY)
                _, template_thresh = cv2.threshold(template, THRESHOLD_BTN, 255, cv2.THRESH_BINARY)

                if template_thresh.shape[0] > screen_thresh.shape[0] or template_thresh.shape[1] > screen_thresh.shape[
                    1]:
                    ch_message = "Błąd: Przycisk CH jest większy niż badany obszar!"
                    return

                result = cv2.matchTemplate(screen_thresh, template_thresh, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val > 0.75:
                    w, h = template_thresh.shape[::-1]
                    global_x = monitor['left'] + max_loc[0] + (w // 2)
                    global_y = monitor['top'] + max_loc[1] + (h // 2)
                    orig_x, orig_y = pyautogui.position()

                    pydirectinput.moveTo(global_x, global_y)
                    time.sleep(0.05)
                    pydirectinput.mouseDown()
                    time.sleep(0.05)
                    pydirectinput.mouseUp()
                    time.sleep(0.1)
                    pydirectinput.moveTo(orig_x, orig_y)
                    success = True
                else:
                    ch_message = f"Nie widzę przycisku CH{target_ch}!"

        if success:
            ch_message = f"Zmieniono na -> CH{target_ch}! (Czekam {delay}s na ekran ładowania)"
            time.sleep(delay)
            ch_message = "Odpalam automatyzację po zmianie CH..."
            execute_post_change_automation()
            ch_message = "Gotowy do pracy"

    finally:
        is_changing_ch = False


def next_channel_routine():
    global ch_message, is_changing_ch
    if is_changing_ch: return
    allowed = APP_CONFIG.get("allowed_channels", [1, 2, 3, 4, 5, 6])
    if not allowed: return
    allowed.sort()
    current_ch, _ = get_current_channel()
    if current_ch:
        larger_channels = [c for c in allowed if c > current_ch]
        target_ch = larger_channels[0] if larger_channels else allowed[0]
        threading.Thread(target=change_channel_routine, args=(target_ch,), daemon=True).start()


def prev_channel_routine():
    global ch_message, is_changing_ch
    if is_changing_ch: return
    allowed = APP_CONFIG.get("allowed_channels", [1, 2, 3, 4, 5, 6])
    if not allowed: return
    allowed.sort()
    current_ch, _ = get_current_channel()
    if current_ch:
        smaller_channels = [c for c in allowed if c < current_ch]
        target_ch = smaller_channels[-1] if smaller_channels else allowed[-1]
        threading.Thread(target=change_channel_routine, args=(target_ch,), daemon=True).start()


def cv2_to_qpixmap(cv_img):
    if cv_img is None: return None
    if len(cv_img.shape) == 2:
        rgb_img = cv2.cvtColor(cv_img, cv2.COLOR_GRAY2RGB)
    else:
        rgb_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb_img.shape
    bytes_per_line = ch * w
    return QPixmap.fromImage(QImage(rgb_img.data, w, h, bytes_per_line, QImage.Format.Format_RGB888))


# ==========================================
# GLOBALNY ZABÓJCA SPACJI W INTERFEJSIE
# ==========================================
class IgnoreSpaceFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                return True
        return super().eventFilter(obj, event)


# ==========================================
# INTERFEJS UŻYTKOWNIKA (HUD)
# ==========================================
class ChangerHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.key_states = {}
        self.binding_action = None
        self.next_auto_ch_time = 0

        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Zmieniacz CH - Panel")
        self.setGeometry(100, 100, 600, 780)

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
        """)

        try:
            with open("changer_config.json", "r", encoding="utf-8") as f:
                saved = json.load(f)
                APP_CONFIG.update(saved)
        except FileNotFoundError:
            pass

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
        APP_CONFIG["is_running"] = not APP_CONFIG.get("is_running", True)
        save_config_to_file()
        self.apply_main_toggle_style()

    def apply_main_toggle_style(self):
        if APP_CONFIG.get("is_running", True):
            self.btn_toggle_bot.setText("STAN PROGRAMU: WŁĄCZONY (Kliknij, aby wstrzymać)")
            self.btn_toggle_bot.setStyleSheet(
                "background-color: #4CAF50; color: white; font-size: 15px; font-weight: bold; padding: 12px;")
        else:
            self.btn_toggle_bot.setText("STAN PROGRAMU: ZATRZYMANY (Kliknij, aby wznowić)")
            self.btn_toggle_bot.setStyleSheet(
                "background-color: #F44336; color: white; font-size: 15px; font-weight: bold; padding: 12px;")

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

        self.lbl_status = QLabel(ch_message)
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

    def setup_automation_tab(self):
        layout = QVBoxLayout()

        # PĘTLA CH
        lbl1 = QLabel("Pętla zmiany Kanałów")
        lbl1.setStyleSheet("color: #E91E63; font-size: 15px;")
        layout.addWidget(lbl1)

        self.chk_auto_ch = QCheckBox("Włącz automatyczną zmianę CH")
        self.chk_auto_ch.setChecked(APP_CONFIG.get("auto_ch_enabled"))
        self.chk_auto_ch.toggled.connect(self.toggle_auto_ch)
        layout.addWidget(self.chk_auto_ch)

        lbl_ch_select = QLabel("Zaznacz kanały, między którymi chcesz skakać:")
        lbl_ch_select.setStyleSheet("color: #BBB; font-size: 12px; margin-top: 5px;")
        layout.addWidget(lbl_ch_select)

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

        h_auto = QHBoxLayout()
        h_auto.addWidget(QLabel("Przełącz na:"))
        self.cmb_auto_mode = QComboBox()
        self.cmb_auto_mode.addItems(["Następny Kanał", "Poprzedni Kanał"])
        self.cmb_auto_mode.setCurrentIndex(0 if APP_CONFIG.get("auto_ch_mode") == "next" else 1)
        self.cmb_auto_mode.currentIndexChanged.connect(self.change_auto_mode)
        h_auto.addWidget(self.cmb_auto_mode)
        h_auto.addWidget(QLabel("co (sekund):"))

        self.spin_auto_int = QDoubleSpinBox()
        self.spin_auto_int.setRange(0.1, 3600.0)
        self.spin_auto_int.setSingleStep(0.5)
        self.spin_auto_int.setValue(float(APP_CONFIG.get("auto_ch_interval", 60.0)))
        self.spin_auto_int.valueChanged.connect(self.change_auto_int)
        h_auto.addWidget(self.spin_auto_int)
        layout.addLayout(h_auto)

        self.lbl_auto_countdown = QLabel("Oczekiwanie: Wyłączone")
        self.lbl_auto_countdown.setStyleSheet("color: #9E9E9E; font-size: 11px;")
        layout.addWidget(self.lbl_auto_countdown)

        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setStyleSheet("background-color: #555; margin: 5px 0px;")
        layout.addWidget(line1)

        # AKCJE PO ZMIANIE CH
        lbl2 = QLabel("Akcje wykonywane po zmianie Kanału")
        lbl2.setStyleSheet("color: #03A9F4; font-size: 15px;")
        layout.addWidget(lbl2)

        self.chk_post_space = QCheckBox("Automatyczny atak z konia (Spamowanie Spacji)")
        self.chk_post_space.setChecked(APP_CONFIG.get("post_hold_space"))
        self.chk_post_space.toggled.connect(self.toggle_post_space)
        layout.addWidget(self.chk_post_space)

        h_post_key = QHBoxLayout()
        self.chk_post_key = QCheckBox("Odczekaj:")
        self.chk_post_key.setChecked(APP_CONFIG.get("post_press_enabled"))
        self.chk_post_key.toggled.connect(self.toggle_post_key)
        h_post_key.addWidget(self.chk_post_key)

        self.spin_post_delay = QDoubleSpinBox()
        self.spin_post_delay.setRange(0.0, 60.0)
        self.spin_post_delay.setSingleStep(0.5)
        self.spin_post_delay.setValue(APP_CONFIG.get("post_press_delay", 0.0))
        self.spin_post_delay.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"post_press_delay": val}), save_config_to_file()])
        h_post_key.addWidget(self.spin_post_delay)
        h_post_key.addWidget(QLabel("s i kliknij"))

        self.spin_post_count = QSpinBox()
        self.spin_post_count.setRange(1, 100)
        self.spin_post_count.setValue(APP_CONFIG.get("post_press_count", 1))
        self.spin_post_count.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"post_press_count": val}), save_config_to_file()])
        h_post_key.addWidget(self.spin_post_count)
        h_post_key.addWidget(QLabel("x klawisz:"))

        self.btn_post_key = QPushButton()
        self.btn_post_key.setFixedWidth(80)
        self.btn_post_key.clicked.connect(lambda: self.start_binding("auto_post_key"))
        h_post_key.addWidget(self.btn_post_key)
        h_post_key.addStretch()
        layout.addLayout(h_post_key)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet("background-color: #555; margin: 5px 0px;")
        layout.addWidget(line2)

        # FARMING CIĄGŁY
        lbl3 = QLabel("Ciągłe trzymanie Klawisza (Farming)")
        lbl3.setStyleSheet("color: #FFC107; font-size: 15px;")
        layout.addWidget(lbl3)

        h_hold_key = QHBoxLayout()
        self.chk_hold_key = QCheckBox("Trzymaj wciśnięty przypisany klawisz: ")
        self.chk_hold_key.setChecked(APP_CONFIG.get("hold_key_enabled"))
        self.chk_hold_key.toggled.connect(self.toggle_hold_key)
        h_hold_key.addWidget(self.chk_hold_key)

        self.btn_hold_key = QPushButton()
        self.btn_hold_key.setFixedWidth(100)
        self.btn_hold_key.clicked.connect(lambda: self.start_binding("auto_hold_key"))
        h_hold_key.addWidget(self.btn_hold_key)
        layout.addLayout(h_hold_key)

        line3 = QFrame()
        line3.setFrameShape(QFrame.Shape.HLine)
        line3.setStyleSheet("background-color: #555; margin: 5px 0px;")
        layout.addWidget(line3)

        # NOWOŚĆ: NIEZALEŻNY AUTO-KLIKACZ
        lbl4 = QLabel("Niezależne klikanie klawisza w tle (Auto-Klikacz)")
        lbl4.setStyleSheet("color: #4CAF50; font-size: 15px;")
        layout.addWidget(lbl4)

        h_indep = QHBoxLayout()
        self.chk_indep_action = QCheckBox("Klikaj klawisz")
        self.chk_indep_action.setChecked(APP_CONFIG.get("indep_action_enabled", False))
        self.chk_indep_action.toggled.connect(self.toggle_indep_action)
        h_indep.addWidget(self.chk_indep_action)

        self.btn_indep_key = QPushButton()
        self.btn_indep_key.setFixedWidth(80)
        self.btn_indep_key.clicked.connect(lambda: self.start_binding("indep_action_key"))
        h_indep.addWidget(self.btn_indep_key)

        h_indep.addWidget(QLabel(" co "))

        self.spin_indep_interval = QDoubleSpinBox()
        self.spin_indep_interval.setRange(0.1, 3600.0)
        self.spin_indep_interval.setSingleStep(1.0)
        self.spin_indep_interval.setValue(float(APP_CONFIG.get("indep_action_interval", 5.0)))
        self.spin_indep_interval.valueChanged.connect(self.change_indep_interval)
        h_indep.addWidget(self.spin_indep_interval)

        h_indep.addWidget(QLabel("sekund."))
        h_indep.addStretch()
        layout.addLayout(h_indep)

        layout.addStretch()
        self.tab_automation.setLayout(layout)
        self.refresh_automation_ui()

    def update_allowed_channels(self):
        allowed = [ch for ch, chk in self.chk_channels.items() if chk.isChecked()]
        if not allowed:
            self.chk_channels[1].setChecked(True)
            allowed = [1]
        APP_CONFIG["allowed_channels"] = allowed
        save_config_to_file()

    def refresh_automation_ui(self):
        buttons = [
            ("auto_post_key", self.btn_post_key),
            ("auto_hold_key", self.btn_hold_key),
            ("indep_action_key", self.btn_indep_key)
        ]
        for action_id, btn in buttons:
            if self.binding_action == action_id:
                btn.setText("Naciśnij...")
                btn.setStyleSheet("background-color: #F44336; color: white;")
            else:
                vk = APP_CONFIG["hotkeys"].get(action_id)
                btn.setText(self.get_key_name(vk))
                btn.setStyleSheet("background-color: #3F51B5; color: white;")

    def toggle_auto_ch(self, checked):
        APP_CONFIG["auto_ch_enabled"] = checked
        if checked: self.next_auto_ch_time = time.time() + APP_CONFIG["auto_ch_interval"]
        save_config_to_file()

    def change_auto_mode(self, idx):
        APP_CONFIG["auto_ch_mode"] = "next" if idx == 0 else "prev"
        save_config_to_file()

    def change_auto_int(self, val):
        APP_CONFIG["auto_ch_interval"] = float(val)
        self.next_auto_ch_time = time.time() + val
        save_config_to_file()

    def toggle_post_space(self, checked):
        global is_horse_attacking
        APP_CONFIG["post_hold_space"] = checked
        if not checked:
            is_horse_attacking = False
        save_config_to_file()

    def toggle_post_key(self, checked):
        APP_CONFIG["post_press_enabled"] = checked
        save_config_to_file()

    def toggle_hold_key(self, checked):
        APP_CONFIG["hold_key_enabled"] = checked
        save_config_to_file()
        vk = APP_CONFIG["hotkeys"].get("auto_hold_key")
        if vk:
            if checked:
                pdi_down(vk)
            else:
                pdi_up(vk)

    def toggle_indep_action(self, checked):
        APP_CONFIG["indep_action_enabled"] = checked
        save_config_to_file()

    def change_indep_interval(self, val):
        APP_CONFIG["indep_action_interval"] = float(val)
        save_config_to_file()

    def setup_settings_tab(self):
        layout = QVBoxLayout()
        lbl_mode_title = QLabel("Ustawienia Operacyjne:")
        lbl_mode_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_mode_title.setStyleSheet("color: #03A9F4; font-size: 16px; margin-bottom: 5px;")
        layout.addWidget(lbl_mode_title)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Rozpoznawanie Obrazu (Wycinki przycisków)")
        self.combo_mode.addItem("Współrzędne (Sztywne klikanie w punkt X, Y)")
        self.combo_mode.setCurrentIndex(0 if APP_CONFIG.get("click_mode", "image") == "image" else 1)
        self.combo_mode.currentIndexChanged.connect(
            lambda idx: [APP_CONFIG.update({"click_mode": "image" if idx == 0 else "coords"}), save_config_to_file()])
        layout.addWidget(self.combo_mode)

        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel("Opóźnienie po zmianie CH (w sekundach):"))
        self.spin_delay = QDoubleSpinBox()
        self.spin_delay.setRange(0.0, 5.0)
        self.spin_delay.setSingleStep(0.1)
        self.spin_delay.setValue(APP_CONFIG.get("change_delay", 1.0))
        self.spin_delay.valueChanged.connect(
            lambda val: [APP_CONFIG.update({"change_delay": round(val, 2)}), save_config_to_file()])
        delay_layout.addWidget(self.spin_delay)
        layout.addLayout(delay_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #555; margin: 10px 0px;")
        layout.addWidget(line)

        info = QLabel("Konfiguracja Głębokich Klawiszy")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: #03A9F4; font-size: 16px; margin-bottom: 5px;")
        layout.addWidget(info)

        self.lbl_settings_status = QLabel("Kliknij przycisk, aby ustawić nowy klawisz.")
        self.lbl_settings_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_settings_status.setStyleSheet("color: #BBB; font-size: 12px;")
        layout.addWidget(self.lbl_settings_status)

        grid = QGridLayout()
        self.hotkey_buttons = {}
        actions = [
            ("next_ch", "Następny Kanał"), ("prev_ch", "Poprzedni Kanał"),
            ("capture", "Kreator (Przechwyt)"), ("ch1", "Zmień na CH1"),
            ("ch2", "Zmień na CH2"), ("ch3", "Zmień na CH3"),
            ("ch4", "Zmień na CH4"), ("ch5", "Zmień na CH5"), ("ch6", "Zmień na CH6")
        ]

        for i, (action_id, desc) in enumerate(actions):
            lbl = QLabel(desc)
            btn = QPushButton()
            btn.setFixedWidth(120)
            btn.clicked.connect(lambda checked, act=action_id: self.start_binding(act))
            self.hotkey_buttons[action_id] = btn
            grid.addWidget(lbl, i % 5, (i // 5) * 2)
            grid.addWidget(btn, i % 5, (i // 5) * 2 + 1)

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
        msg = "Naciśnij klawisz..."
        if self.tabs.currentWidget() == self.tab_settings:
            self.lbl_settings_status.setText(msg)
            self.lbl_settings_status.setStyleSheet("color: #FFEB3B; font-size: 12px;")
        self.key_states.clear()
        self.refresh_settings_ui()
        self.refresh_automation_ui()

    def check_hardware_keys(self):
        if not APP_CONFIG.get("is_running", True) and not self.binding_action:
            return

        if self.binding_action:
            allowed_vks = [2, 4, 5, 6] + list(range(8, 255))
            for vk in allowed_vks:
                if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                    self.finish_binding(vk)
                    time.sleep(0.3)
                    return

        hotkeys = APP_CONFIG.get("hotkeys", {})
        # Ignorujemy klawisze, które są obsługiwane w innych wątkach
        ignored_actions = ["auto_post_key", "auto_hold_key", "indep_action_key"]

        for action, vk in hotkeys.items():
            if vk is None or action in ignored_actions: continue

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
                return

        if self.binding_action == "auto_hold_key" and APP_CONFIG["hold_key_enabled"]:
            old_vk = APP_CONFIG["hotkeys"].get("auto_hold_key")
            if old_vk: pdi_up(old_vk)

        APP_CONFIG["hotkeys"][self.binding_action] = vk
        save_config_to_file()

        if self.binding_action == "auto_hold_key" and APP_CONFIG["hold_key_enabled"]:
            pdi_down(vk)

        if self.tabs.currentWidget() == self.tab_settings:
            self.lbl_settings_status.setText(f"Zapisano! Klawisz: {self.get_key_name(vk)}")
            self.lbl_settings_status.setStyleSheet("color: #8BC34A; font-size: 12px;")

        self.binding_action = None
        self.refresh_settings_ui()
        self.refresh_automation_ui()

    def trigger_hotkey_action(self, action):
        if action == "next_ch":
            next_channel_routine()
        elif action == "prev_ch":
            prev_channel_routine()
        elif action.startswith("ch"):
            threading.Thread(target=change_channel_routine, args=(int(action[-1]),), daemon=True).start()
        elif action == "capture":
            if self.tabs.currentWidget() == self.tab_wizard:
                handle_wizard_capture()
            elif self.tabs.currentWidget() == self.tab_editor:
                handle_editor_capture(self.combo_editor.currentIndex())

    def update_ui(self):
        global ch_message, latest_frame

        if APP_CONFIG.get("auto_ch_enabled") and not is_changing_ch and APP_CONFIG.get("is_running", True):
            rem_time = self.next_auto_ch_time - time.time()
            if rem_time <= 0:
                if APP_CONFIG.get("auto_ch_mode") == "next":
                    next_channel_routine()
                else:
                    prev_channel_routine()
                self.next_auto_ch_time = time.time() + float(APP_CONFIG.get("auto_ch_interval"))
                self.lbl_auto_countdown.setText("Oczekiwanie: Wykonuję skok...")
            else:
                self.lbl_auto_countdown.setText(f"Następny skok CH za: {rem_time:.1f}s")
        else:
            if not APP_CONFIG.get("is_running", True):
                self.lbl_auto_countdown.setText("Oczekiwanie: ZAPRZESTANO (BOT WYŁĄCZONY)")
            else:
                self.lbl_auto_countdown.setText("Oczekiwanie: Wyłączone")

        if self.tabs.currentWidget() == self.tab_main:
            self.lbl_status.setText(ch_message)
            current_ch, confidence = get_current_channel()

            if latest_frame is not None:
                pixmap = cv2_to_qpixmap(latest_frame)
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
        self.lbl_wizard_task = QLabel(WIZARD_STEPS[0]["desc"])
        self.lbl_wizard_task.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_wizard_task.setStyleSheet(f"font-size: 13px; color: {WIZARD_STEPS[0]['color']}; margin: 10px 0px;")
        layout.addWidget(self.lbl_wizard_task)
        self.lbl_wizard_status = QLabel(wizard_message)
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
        btn_reset.clicked.connect(reset_wizard)
        layout.addWidget(btn_reset)
        self.tab_wizard.setLayout(layout)

    def setup_editor_tab(self):
        layout = QVBoxLayout()
        self.combo_editor = QComboBox()
        self.combo_editor.addItems(
            ["0. Obszar na ekranie: Czytanie CH (State)", "1. Obszar na ekranie: Przyciski CH (Btn)"])
        for i in range(1, 7): self.combo_editor.addItems(
            [f"{i * 2}. Obrazek: Napis 'CH{i}'", f"{i * 2 + 1}. Obrazek: Przycisk kółka 'CH{i}'"])
        for i in range(1, 7): self.combo_editor.addItem(f"{13 + i}. Współrzędne do kliknięcia 'CH{i}'")
        self.combo_editor.currentIndexChanged.connect(self.refresh_editor_preview)
        layout.addWidget(self.combo_editor)
        self.lbl_editor_status = QLabel(editor_message)
        self.lbl_editor_status.setStyleSheet("color: #FFEB3B; font-size: 12px; margin-top: 5px;")
        self.lbl_editor_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_editor_status)
        self.lbl_preview_editor = QLabel("Wybierz z listy...")
        self.lbl_preview_editor.setFixedSize(300, 200)
        self.lbl_preview_editor.setStyleSheet("border: 1px solid #777;")
        layout.addWidget(self.lbl_preview_editor, alignment=Qt.AlignmentFlag.AlignCenter)
        self.tab_editor.setLayout(layout)

    def refresh_editor_preview(self):
        idx = self.combo_editor.currentIndex()
        pixmap = None
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


if __name__ == '__main__':
    app = QApplication(sys.argv)

    space_filter = IgnoreSpaceFilter()
    app.installEventFilter(space_filter)

    # Odpalamy wątek atakowania spacji w tle
    threading.Thread(target=horse_attack_worker, daemon=True).start()

    # Odpalamy wątek niezależnego klikacza w tle
    threading.Thread(target=independent_action_worker, daemon=True).start()

    hud = ChangerHUD()
    hud.show()
    sys.exit(app.exec())
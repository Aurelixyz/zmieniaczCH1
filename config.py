import os
import json
import cv2

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
    "post_cape_delay_1": 0.3,  # Czas (w sekundach) przed PIERWSZYM kliknięciem pelerynki,
    "smart_cape_mode": "mouse",  # "mouse" dla myszki, "key" dla klawiatury
    "auto_ch_enabled": False,
    "auto_ch_mode": "next",
    "auto_ch_interval": 60.0,
    "allowed_channels": [1, 2, 3, 4, 5, 6],
    "alert_ignore_ch": {"1": True, "2": True, "3": True},
    "alert_debounce": {"1": False, "2": False, "3": False},

    "post_hold_space": False,
    "post_press_enabled": False,
    "post_press_delay": 0.0,
    "post_press_count": 1,
    "hold_key_enabled": False,
    "post_cape_enabled": False,
    "post_cape_delay_2": 2.0,
    "smart_cape_coords": [0, 0],

    "indep_action_enabled": False,
    "indep_action_interval": 5.0,

    # KONFIGURACJA ALERTÓW
    "alert_1_monitor": {}, "alert_2_monitor": {}, "alert_3_monitor": {},
    "alerts_enabled": {"1": False, "2": False, "3": False},
    "alert_conditions": {"1": "appears", "2": "disappears", "3": "appears"},
    "alert_actions": {"1": "Brak", "2": "Auto Skille", "3": "Brak"},
    "alert_sounds": {"1": "Brak", "2": "Brak", "3": "Brak"},
    "alert_custom_paths": {"1": "", "2": "", "3": ""},
    # NOWE OPCJE
    "alert_intervals": {"1": 5.0, "2": 5.0, "3": 5.0},
    "alert_volumes": {"1": 100, "2": 100, "3": 100},
    "alert_loops": {"1": False, "2": False, "3": False},

    "auto_booster_enabled": False,
    "auto_booster_interval": 120.0,

    "gui_rect": {"x": 100, "y": 100, "w": 650, "h": 850},

    "hotkeys": {
        "global_pause_key": None,
        "macro_seq_key": None,
        "auto_booster_key": None,
        "show_gui_key": None,
        "next_ch": 0x09,
        "prev_ch": None,
        "ch1": 0x61, "ch2": 0x62, "ch3": 0x63,
        "ch4": 0x64, "ch5": 0x65, "ch6": 0x66,
        "capture": 0x76,
        "auto_post_key": None,
        "auto_hold_key": None,
        "post_cape_key": None,
        "indep_action_key": None
    }
}

# Stan dzielony między wątkami
SHARED_STATE = {
    "is_changing_ch": False,
    "is_horse_attacking": False,
    "ch_message": "Gotowy do pracy",
    "latest_frame": None
}

VK_DICT = {
    0x02: "PPM (Mysz)", 0x04: "Środek (Mysz)", 0x05: "Boczny Tył", 0x06: "Boczny Przód",
    0x09: "TAB", 0x1B: "ESC", 0x20: "SPACJA", 0x0D: "ENTER",
    0x10: "SHIFT", 0x11: "CTRL", 0x12: "ALT",
    0x13: "PAUSE", 0x2C: "PRTSC", 0x91: "SCRLK",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "` (Tylda)", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    0x21: "PAGE UP", 0x22: "PAGE DOWN", 0x23: "END", 0x24: "HOME",
    0x25: "STRZAŁKA W LEWO", 0x26: "STRZAŁKA W GÓRĘ", 0x27: "STRZAŁKA W PRAWO", 0x28: "STRZAŁKA W DÓŁ",
    0x2D: "INSERT", 0x2E: "DELETE"
}
for i in range(1, 13): VK_DICT[0x6F + i] = f"F{i}"
for i in range(0x41, 0x5B): VK_DICT[i] = chr(i)
for i in range(0x30, 0x3A): VK_DICT[i] = chr(i)
for i in range(0x60, 0x6A): VK_DICT[i] = f"NUM {i - 0x60}"

VK_TO_PDI = {
    0x02: 'right', 0x04: 'middle',
    0x09: 'tab', 0x1B: 'esc', 0x20: 'space', 0x0D: 'enter',
    0x10: 'shift', 0x11: 'ctrl', 0x12: 'alt',
    0x13: "pause", 0x2C: "printscreen", 0x91: "scrolllock",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    0x21: "pageup", 0x22: "pagedown", 0x23: "end", 0x24: "home",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x2D: "insert", 0x2E: "delete"
}
for i in range(0x30, 0x3A): VK_TO_PDI[i] = chr(i)
for i in range(0x41, 0x5B): VK_TO_PDI[i] = chr(i).lower()
for i in range(1, 13): VK_TO_PDI[0x6F + i] = f"f{i}"
for i in range(0x60, 0x6A): VK_TO_PDI[i] = f"num{i - 0x60}"

state_templates = {}
btn_templates = {}
alert_templates = {}


def load_templates():
    state_templates.clear()
    btn_templates.clear()
    alert_templates.clear()
    for i in range(1, 7):
        if os.path.exists(f"ch{i}_state.png"): state_templates[i] = cv2.imread(f"ch{i}_state.png", cv2.IMREAD_GRAYSCALE)
        if os.path.exists(f"ch{i}_btn.png"): btn_templates[i] = cv2.imread(f"ch{i}_btn.png", cv2.IMREAD_GRAYSCALE)
    for i in range(1, 4):
        if os.path.exists(f"alert_{i}.png"): alert_templates[i] = cv2.imread(f"alert_{i}.png", cv2.IMREAD_GRAYSCALE)


def save_config_to_file():
    try:
        with open("changer_config.json", "w", encoding="utf-8") as f:
            json.dump(APP_CONFIG, f, indent=4)
    except Exception:
        pass
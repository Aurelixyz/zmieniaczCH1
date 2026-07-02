import time
import threading
import ctypes
import cv2
import numpy as np
import mss
import pyautogui
import pydirectinput
import winsound
import os

from config import (APP_CONFIG, SHARED_STATE, state_templates, btn_templates,
                    alert_templates, VK_TO_PDI)

pydirectinput.PAUSE = 0.01

# ==========================================
# CTYPES - SPRZĘTOWE SYMULOWANIE KLAWISZY
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
    ii_.ki = KeyBdInput(0, hexKeyCode, 0x0008, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def release_key_hardware(hexKeyCode):
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()
    ii_.ki = KeyBdInput(0, hexKeyCode, 0x0008 | 0x0002, 0, ctypes.pointer(extra))
    x = Input(ctypes.c_ulong(1), ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def press_single_ctypes(vk):
    # Konwersja kodu wirtualnego (VK) na sprzętowy Scan Code
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    press_key_hardware(scan)
    time.sleep(0.05)
    release_key_hardware(scan)


def press_combo_ctypes(vk1, vk2):
    # Konwersja dla obu klawiszy (np. CTRL i G)
    scan1 = ctypes.windll.user32.MapVirtualKeyW(vk1, 0)
    scan2 = ctypes.windll.user32.MapVirtualKeyW(vk2, 0)

    press_key_hardware(scan1)  # Wciśnij i trzymaj pierwszy (np. CTRL)
    time.sleep(0.05)
    press_key_hardware(scan2)  # Kliknij drugi (np. G)
    time.sleep(0.05)
    release_key_hardware(scan2)  # Puść drugi
    time.sleep(0.02)
    release_key_hardware(scan1)  # Puść pierwszy

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
# AUDIO & PAUZA
# ==========================================
def play_custom_audio(file_path, volume=100, loop=False):
    try:
        ctypes.windll.winmm.mciSendStringW('close alertaudio', None, 0, None)
        ctypes.windll.winmm.mciSendStringW(f'open "{file_path}" alias alertaudio', None, 0, None)
        # Skala głośności dla mciSendString to 0-1000
        ctypes.windll.winmm.mciSendStringW(f'setaudio alertaudio volume to {volume * 10}', None, 0, None)
        cmd = 'play alertaudio repeat' if loop else 'play alertaudio'
        ctypes.windll.winmm.mciSendStringW(cmd, None, 0, None)
    except Exception as e:
        print(f"Audio Error: {e}")

def stop_custom_audio():
    try:
        ctypes.windll.winmm.mciSendStringW('close alertaudio', None, 0, None)
    except:
        pass

def trigger_alert_sound(alert_idx):
    sound_mode = APP_CONFIG.get("alert_sounds", {}).get(str(alert_idx), "Brak")
    volume = APP_CONFIG.get("alert_volumes", {}).get(str(alert_idx), 100)
    loop = APP_CONFIG.get("alert_loops", {}).get(str(alert_idx), False)

    if sound_mode != "Brak":
        if sound_mode == "Windows: Exclamation":
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif sound_mode == "Windows: Asterisk":
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif sound_mode == "Windows: Hand":
            winsound.MessageBeep(winsound.MB_ICONHAND)
        elif sound_mode == "Własny plik (.wav / .mp3)...":
            path = APP_CONFIG.get("alert_custom_paths", {}).get(str(alert_idx), "")
            if os.path.exists(path):
                play_custom_audio(path, volume, loop)


# ==========================================
# WĄTKI W TLE
# ==========================================
def horse_attack_worker():
    SPACE_SCANCODE = 0x39
    while True:
        if SHARED_STATE["is_horse_attacking"] and APP_CONFIG.get("is_running", True) and not SHARED_STATE[
            "is_changing_ch"]:
            press_key_hardware(SPACE_SCANCODE)
            time.sleep(0.05)
            release_key_hardware(SPACE_SCANCODE)
            time.sleep(0.02)
        else:
            time.sleep(0.05)


def independent_action_worker():
    last_press_time = time.time()
    while True:
        if APP_CONFIG.get("is_running", True) and APP_CONFIG.get("indep_action_enabled", False):
            interval = float(APP_CONFIG.get("indep_action_interval", 5.0))
            if time.time() - last_press_time >= interval:
                vk = APP_CONFIG.get("hotkeys", {}).get("indep_action_key")
                if vk: press_single_ctypes(vk)
                last_press_time = time.time()
        time.sleep(0.1)


def auto_booster_worker():
    last_press_time = time.time()
    while True:
        if APP_CONFIG.get("is_running", True) and APP_CONFIG.get("auto_booster_enabled", False):
            interval = float(APP_CONFIG.get("auto_booster_interval", 120.0))
            if time.time() - last_press_time >= interval:
                vk = APP_CONFIG.get("hotkeys", {}).get("auto_booster_key")
                if vk: press_single_ctypes(vk)
                last_press_time = time.time()
        time.sleep(0.1)


def bot_pause():
    APP_CONFIG["is_running"] = False

    # Reset flagi ataku z konia
    SHARED_STATE["is_horse_attacking"] = False

    # Jeśli bot trzymał wciśnięty klawisz, wymuszamy jego fizyczne puszczenie
    vk_hold = APP_CONFIG.get("hotkeys", {}).get("auto_hold_key")
    if vk_hold and APP_CONFIG.get("hold_key_enabled"):
        pdi_up(vk_hold)


def bot_resume():
    APP_CONFIG["is_running"] = True

    # Wznowienie trzymania klawisza, jeśli opcja jest nadal aktywna w GUI
    vk_hold = APP_CONFIG.get("hotkeys", {}).get("auto_hold_key")
    if vk_hold and APP_CONFIG.get("hold_key_enabled"):
        pdi_down(vk_hold)


def alert_monitor_worker():
    cooldowns = {1: 0.0, 2: 0.0, 3: 0.0}
    # Słownik liczący ile razy z rzędu dany warunek został spełniony (dla opcji mignięć)
    streak_counter = {1: 0, 2: 0, 3: 0}

    while True:
        if APP_CONFIG.get("is_running", True):
            with mss.mss() as sct:
                for i in range(1, 4):
                    # 1. Czy alert jest w ogóle włączony?
                    if not APP_CONFIG.get("alerts_enabled", {}).get(str(i), False):
                        streak_counter[i] = 0
                        continue

                    # 2. Czy ma pauzować na czas zmiany kanału?
                    ignore_ch = APP_CONFIG.get("alert_ignore_ch", {}).get(str(i), True)
                    if ignore_ch and SHARED_STATE.get("is_changing_ch", False):
                        streak_counter[i] = 0  # Resetujemy licznik, żeby nie wariował
                        continue

                    if time.time() < cooldowns[i]:
                        continue

                    monitor = APP_CONFIG.get(f"alert_{i}_monitor")
                    template = alert_templates.get(i)
                    if monitor and monitor.get("width", 0) > 0 and template is not None:
                        try:
                            img_array = np.array(sct.grab(monitor))
                            screen_gray = cv2.cvtColor(img_array, cv2.COLOR_BGRA2GRAY)
                            result = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
                            _, max_val, _, _ = cv2.minMaxLoc(result)

                            cond = APP_CONFIG.get("alert_conditions", {}).get(str(i), "appears")
                            use_debounce = APP_CONFIG.get("alert_debounce", {}).get(str(i), False)
                            is_triggered = False

                            # Sprawdzanie warunku: Pojawia się (wartość > 0.8)
                            if cond == "appears":
                                if max_val > 0.8:
                                    if use_debounce:
                                        streak_counter[i] += 1
                                        if streak_counter[i] >= 3:
                                            is_triggered = True
                                            streak_counter[i] = 0
                                    else:
                                        is_triggered = True
                                else:
                                    streak_counter[i] = 0

                            # Sprawdzanie warunku: Znika (wartość < 0.6)
                            elif cond == "disappears":
                                if max_val < 0.6:
                                    if use_debounce:
                                        streak_counter[i] += 1
                                        if streak_counter[i] >= 3:
                                            is_triggered = True
                                            streak_counter[i] = 0
                                    else:
                                        is_triggered = True
                                else:
                                    streak_counter[i] = 0

                            # Jeśli alert został ostatecznie potwierdzony:
                            if is_triggered:
                                trigger_alert_sound(i)

                                action = APP_CONFIG.get("alert_actions", {}).get(str(i), "Brak")
                                if action == "Zatrzymaj Bota (Pauza)":
                                    bot_pause()
                                elif action == "Auto Skille":
                                    threading.Thread(target=execute_auto_skills, daemon=True).start()

                                # Nakładamy cooldown
                                interval = float(APP_CONFIG.get("alert_intervals", {}).get(str(i), 5.0))
                                cooldowns[i] = time.time() + interval

                        except Exception:
                            pass

        # Pętla wykonuje się raz na sekundę (co dyktuje tempo zliczania 3 mignięć = ok. 3 sekundy)
        time.sleep(1.0)


def execute_auto_skills():
    bot_pause()
    time.sleep(0.5)
    try:
        press_combo_ctypes(0x11, 0x47)  # CTRL + G (Zejście z konia)
        time.sleep(0.5)
        press_single_ctypes(0x70)       # F1 (Pierwszy skill)
        time.sleep(0.5)
        press_combo_ctypes(0x11, 0x47)  # CTRL + G (Wejście)
        time.sleep(0.5)
        press_combo_ctypes(0x11, 0x47)  # CTRL + G (Zejście)
        time.sleep(0.5)
        press_single_ctypes(0x71)       # F2 (Drugi skill)
        time.sleep(0.5)
        press_combo_ctypes(0x11, 0x47)  # CTRL + G (Wejście)
        time.sleep(1.3)
    except Exception as e:
        print(f"Błąd Auto Skille: {e}")
    finally:
        bot_resume()


def get_current_channel():
    if not state_templates: return None, 0.0
    with mss.mss() as sct:
        monitor = APP_CONFIG.get("state_monitor")
        if not monitor: return None, 0.0
        try:
            img_array = np.array(sct.grab(monitor))
            screen_gray = cv2.cvtColor(img_array, cv2.COLOR_BGRA2GRAY)
            from config import THRESHOLD_STATE
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

        SHARED_STATE["latest_frame"] = debug_img
        if highest_confidence > 0.70: return best_match_ch, highest_confidence
        return None, highest_confidence


def execute_post_change_automation():
    def automation_routine():
        if APP_CONFIG.get("hold_key_enabled"):
            vk_hold = APP_CONFIG.get("hotkeys", {}).get("auto_hold_key")
            if vk_hold: pdi_down(vk_hold)

        if APP_CONFIG.get("post_hold_space"):
            SHARED_STATE["is_horse_attacking"] = True

        # === INTELIGENTNA PELERYNKA (MYSZKA LUB KLAWISZ) ===
        if APP_CONFIG.get("post_cape_enabled", False):
            # Czekamy ułamek sekundy po załadowaniu mapy
            time.sleep(float(APP_CONFIG.get("post_cape_delay_1", 0.3)))

            mode = APP_CONFIG.get("smart_cape_mode", "mouse")

            # Wewnętrzna funkcja realizująca jeden "strzał" pelerynką
            def execute_single_cape_action():
                if mode == "mouse":
                    coords = APP_CONFIG.get("smart_cape_coords", [0, 0])
                    if coords != [0, 0]:
                        orig_x, orig_y = pyautogui.position()
                        pydirectinput.moveTo(coords[0], coords[1])
                        time.sleep(0.02)
                        pydirectinput.mouseDown(button='right')
                        time.sleep(0.05)
                        pydirectinput.mouseUp(button='right')
                        time.sleep(0.05)
                        pydirectinput.moveTo(orig_x, orig_y)
                elif mode == "key":
                    vk_cape = APP_CONFIG.get("hotkeys", {}).get("post_cape_key")
                    if vk_cape:
                        # Wstrzymanie ataku na czas wciśnięcia klawisza (aby spacja nie zagłuszyła pelerynki)
                        was_attacking = SHARED_STATE["is_horse_attacking"]
                        SHARED_STATE["is_horse_attacking"] = False
                        time.sleep(0.05)

                        press_single_ctypes(vk_cape)

                        # Wznowienie ataku
                        if was_attacking and APP_CONFIG.get("is_running", True):
                            SHARED_STATE["is_horse_attacking"] = True

            # PIERWSZY KLIK
            execute_single_cape_action()

            # Oczekiwanie na drugi klik
            time.sleep(float(APP_CONFIG.get("post_cape_delay_2", 2.0)))

            # DRUGI KLIK
            if APP_CONFIG.get("is_running", True):
                execute_single_cape_action()
        # ====================================================

        if APP_CONFIG.get("post_press_enabled"):
            vk_post = APP_CONFIG.get("hotkeys", {}).get("auto_post_key")
            if vk_post:
                delay = APP_CONFIG.get("post_press_delay", 0.0)
                if delay > 0: time.sleep(delay)
                count = APP_CONFIG.get("post_press_count", 1)
                for _ in range(count):
                    pdi_press(vk_post)
                    time.sleep(0.05)

    threading.Thread(target=automation_routine, daemon=True).start()


def change_channel_routine(target_ch):
    if SHARED_STATE["is_changing_ch"]: return
    SHARED_STATE["is_changing_ch"] = True
    SHARED_STATE["is_horse_attacking"] = False

    try:
        mode = APP_CONFIG.get("click_mode", "image")
        delay = APP_CONFIG.get("change_delay", 1.0)
        success = False

        if mode == "coords":
            coords = APP_CONFIG.get("ch_coords", {}).get(str(target_ch), [0, 0])
            if coords == [0, 0]:
                SHARED_STATE["ch_message"] = f"Błąd: Brak przypisanych współrzędnych dla CH{target_ch}!"
                return
            SHARED_STATE["ch_message"] = f"Klikam we współrzędne CH{target_ch}..."
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
                SHARED_STATE["ch_message"] = f"Brak pliku obrazka ch{target_ch}_btn.png!"
                return
            with mss.mss() as sct:
                monitor = APP_CONFIG.get("btn_monitor")
                if not monitor: return
                img_array = np.array(sct.grab(monitor))
                screen_gray = cv2.cvtColor(img_array, cv2.COLOR_BGRA2GRAY)
                from config import THRESHOLD_BTN
                _, screen_thresh = cv2.threshold(screen_gray, THRESHOLD_BTN, 255, cv2.THRESH_BINARY)
                _, template_thresh = cv2.threshold(template, THRESHOLD_BTN, 255, cv2.THRESH_BINARY)

                if template_thresh.shape[0] > screen_thresh.shape[0] or template_thresh.shape[1] > screen_thresh.shape[
                    1]:
                    SHARED_STATE["ch_message"] = "Błąd: Przycisk CH jest większy niż badany obszar!"
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
                    SHARED_STATE["ch_message"] = f"Nie widzę przycisku CH{target_ch}!"

        if success:
            SHARED_STATE["ch_message"] = f"Zmieniono na -> CH{target_ch}! (Czekam {delay}s na ekran ładowania)"
            time.sleep(delay)
            SHARED_STATE["ch_message"] = "Odpalam automatyzację po zmianie CH..."
            execute_post_change_automation()
            SHARED_STATE["ch_message"] = "Gotowy do pracy"
    finally:
        SHARED_STATE["is_changing_ch"] = False


def next_channel_routine():
    if SHARED_STATE["is_changing_ch"]: return
    allowed = APP_CONFIG.get("allowed_channels", [1, 2, 3, 4, 5, 6])
    if not allowed: return
    allowed.sort()
    current_ch, _ = get_current_channel()
    if current_ch:
        larger_channels = [c for c in allowed if c > current_ch]
        target_ch = larger_channels[0] if larger_channels else allowed[0]
        threading.Thread(target=change_channel_routine, args=(target_ch,), daemon=True).start()


def prev_channel_routine():
    if SHARED_STATE["is_changing_ch"]: return
    allowed = APP_CONFIG.get("allowed_channels", [1, 2, 3, 4, 5, 6])
    if not allowed: return
    allowed.sort()
    current_ch, _ = get_current_channel()
    if current_ch:
        smaller_channels = [c for c in allowed if c < current_ch]
        target_ch = smaller_channels[-1] if smaller_channels else allowed[-1]
        threading.Thread(target=change_channel_routine, args=(target_ch,), daemon=True).start()
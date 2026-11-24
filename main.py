import json
import random
import threading
import time
from typing import Any, Dict
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import serial
import serial.tools.list_ports
from ohbot import ohbot

from scenarios import ScenarioEngine


# --- 1. Chargement de la configuration moteurs ---
def load_config() -> Dict[str, Any]:
    try:
        with open("config.json", "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        print("ATTENTION : Fichier config.json introuvable. Limites par défaut appliquées.")
        return {
            "motors": {
                "head_nod": {"id": 0, "name": "Inclinaison Tête", "min": 0, "max": 10},
                "head_turn": {"id": 1, "name": "Rotation Tête", "min": 0, "max": 10},
                "eye_turn": {"id": 2, "name": "Rotation Yeux", "min": 0, "max": 10},
                "lid_blink": {"id": 3, "name": "Paupières", "min": 0, "max": 10},
                "top_lip": {"id": 4, "name": "Lèvre Supérieure", "min": 0, "max": 10},
                "bottom_lip": {"id": 5, "name": "Lèvre Inférieure", "min": 0, "max": 10},
                "eye_tilt": {"id": 6, "name": "Inclinaison Yeux", "min": 0, "max": 10},
            }
        }


CONFIG = load_config()

# --- 2. Initialisation de l'API & Sécurisation Série ---
app = FastAPI(title="Ohbot 2.2 Control Center API")

_serial_lock = threading.Lock()
SERVO_MOTOR_IDS = (0, 1, 4, 5)  # head_nod, head_turn, top_lip, bottom_lip
_detach_timers: Dict[int, threading.Timer] = {}
_detach_timers_lock = threading.Lock()


def ensure_ohbot_connected() -> bool:
    """Vérifie que le port série Ohbot est ouvert, et reconnecte automatiquement le Pico si besoin."""
    ser_obj = getattr(ohbot, "ser", None)
    if getattr(ohbot, "connected", False) and ser_obj is not None and getattr(ser_obj, "is_open", False):
        return True

    with _serial_lock:
        ser_obj = getattr(ohbot, "ser", None)
        if getattr(ohbot, "connected", False) and ser_obj is not None and getattr(ser_obj, "is_open", False):
            return True

        # 1. Essai standard ohbot.init()
        try:
            if ser_obj is not None:
                try:
                    ser_obj.close()
                except Exception:
                    pass
            ohbot.init()
            ser_obj = getattr(ohbot, "ser", None)
            if getattr(ohbot, "connected", False) and ser_obj is not None and getattr(ser_obj, "is_open", False):
                ser_obj.write_timeout = 1.0
                return True
        except Exception as e:
            print(f"[USB] ohbot.init() a échoué : {e}")

        # 2. Fallback direct sur Raspberry Pi Pico (VID 0x2E8A) ou /dev/ttyACM*
        candidate_port = None
        for p in serial.tools.list_ports.comports():
            if getattr(p, "vid", None) == 0x2E8A or "ttyACM" in p.device or "usbmodem" in p.device:
                candidate_port = p.device
                break

        if candidate_port:
            try:
                s = serial.Serial(candidate_port, 19200, timeout=1.0, write_timeout=1.0)
                s.dtr = True
                s.rts = True
                time.sleep(0.25)
                s.reset_input_buffer()
                ohbot.ser = s
                ohbot.port = candidate_port
                ohbot.connected = True
                ohbot.writing = False
                for i in range(len(ohbot.isAttached)):
                    ohbot.isAttached[i] = False
                print(f"[USB] Reconnexion directe réussie sur {candidate_port}")
                return True
            except Exception as e:
                print(f"[USB] Échec d'ouverture directe sur {candidate_port} : {e}")

        return False


def _custom_serwrite(s: str) -> None:
    """Écriture série thread-safe avec reconnexion auto et délai après attach."""
    if not ensure_ohbot_connected():
        return
    with _serial_lock:
        try:
            ohbot.writing = True
            ohbot.ser.write(s.encode("latin-1"))
            ohbot.ser.flush()
            # Laisse 25 ms au firmware Pico après un attach (a0X) avant la commande de position (m0X)
            if s.startswith("a0"):
                time.sleep(0.025)
        except Exception as e:
            print(f"[USB] Erreur d'écriture série ({e}), marquage déconnecté.")
            ohbot.connected = False
            try:
                ohbot.ser.close()
            except Exception:
                pass
        finally:
            ohbot.writing = False


ohbot._serwrite = _custom_serwrite


def _relax_servos(motor_ids=SERVO_MOTOR_IDS) -> None:
    """Détache les servomoteurs physiques pour éviter qu'ils ne forcent/tremblent à l'arrêt."""
    for m_id in motor_ids:
        if m_id in SERVO_MOTOR_IDS:
            try:
                ohbot.detach(m_id)
            except Exception:
                pass


def _schedule_relax(motor_id: int, delay: float = 0.55) -> None:
    if motor_id not in SERVO_MOTOR_IDS:
        return
    with _detach_timers_lock:
        prev = _detach_timers.get(motor_id)
        if prev is not None:
            prev.cancel()
        timer = threading.Timer(delay, _relax_servos, args=([motor_id],))
        timer.daemon = True
        _detach_timers[motor_id] = timer
        timer.start()


def _safe_move(motor_key: str, target_pos: int, speed: int = 4) -> None:
    """Déplace un moteur en respectant les limites min/max de CONFIG."""
    motor_info = CONFIG.get("motors", {}).get(motor_key)
    if not motor_info:
        return
    pos = max(motor_info["min"], min(motor_info["max"], int(target_pos)))
    try:
        ensure_ohbot_connected()
        ohbot.move(motor_info["id"], pos, spd=speed)
        _schedule_relax(motor_info["id"], delay=0.6)
    except Exception as e:
        print(f"Erreur mouvement '{motor_key}' : {e}")


def _neutral_pos(motor_key: str) -> int:
    motor_info = CONFIG.get("motors", {}).get(motor_key)
    if not motor_info:
        return 5
    return max(motor_info["min"], min(motor_info["max"], 5))


def _set_eyes_scaled(r: int, g: int, b: int, brightness: int = 8, swap_rg: bool = True) -> None:
    scale = max(0, min(10, brightness)) / 10.0
    ensure_ohbot_connected()
    ohbot.setEyeColour(round(r * scale), round(g * scale), round(b * scale), swapRandG=swap_rg)
    ohbot.setEyeBrightness(max(0, min(10, brightness)))


def _animate_face_while_speaking(stop_event: threading.Event) -> None:
    """Anime doucement la tête (un servo à la fois) et les yeux LED pendant la parole."""
    if stop_event.wait(0.35):
        return

    turn_center = _neutral_pos("head_turn")
    nod_center = _neutral_pos("head_nod")
    step = 0
    while not stop_event.is_set():
        if step % 2 == 0:
            _safe_move("head_turn", turn_center + random.choice([-2, -1, 1, 2]), speed=4)
        else:
            _safe_move("head_nod", nod_center + random.choice([-1, 1]), speed=4)

        _safe_move("eye_turn", random.choice([3, 5, 7]), speed=5)
        _safe_move("eye_tilt", random.choice([4, 5, 6]), speed=5)

        if step % 3 == 1:
            _safe_move("lid_blink", 1, speed=7)
            if stop_event.wait(0.15):
                break
            _safe_move("lid_blink", 9, speed=7)

        step += 1
        if stop_event.wait(0.75):
            break

    for key in ("head_turn", "head_nod", "eye_turn", "eye_tilt"):
        _safe_move(key, _neutral_pos(key), speed=4)
        if key in ("head_turn", "head_nod"):
            time.sleep(0.12)
    _safe_move("lid_blink", 9, speed=6)


def _say_with_animation(text: str) -> None:
    ensure_ohbot_connected()
    ohbot.setSynthesizer("gTTS")
    ohbot.setLanguage("fr")
    stop_event = threading.Event()
    anim_thread = threading.Thread(
        target=_animate_face_while_speaking,
        args=(stop_event,),
        daemon=True,
    )
    try:
        anim_thread.start()
        ohbot.say(text)
    finally:
        stop_event.set()
        anim_thread.join(timeout=2.0)
        _relax_servos()


# --- 3. Moteur de scénarios / attitudes / planning ---
scenario_engine = ScenarioEngine(
    move_fn=_safe_move,
    say_fn=_say_with_animation,
    eyes_fn=lambda r, g, b, br: _set_eyes_scaled(r, g, b, brightness=br),
    relax_fn=_relax_servos,
    get_motors_fn=lambda: CONFIG.get("motors", {}),
)


@app.on_event("startup")
def startup_event():
    try:
        ensure_ohbot_connected()
        ohbot.setSynthesizer("gTTS")
        ohbot.setLanguage("fr")
        ohbot.reset()
        ohbot.wait(0.3)
        _set_eyes_scaled(0, 5, 10, brightness=8)
        ohbot.say("Système en ligne et moteurs sécurisés.")
        _relax_servos()
    except Exception as e:
        print(f"Erreur d'initialisation Ohbot : {e}")
    scenario_engine.start()


@app.on_event("shutdown")
def shutdown_event():
    scenario_engine.stop()
    try:
        ohbot.close()
        if getattr(ohbot, "ser", None) is not None:
            ohbot.ser.close()
    except Exception:
        pass


# --- 4. Modèles Pydantic ---
class SpeechRequest(BaseModel):
    text: str


class EyeColorRequest(BaseModel):
    r: int
    g: int
    b: int
    brightness: int = 10
    swap_rg: bool = True


class PomodoroRequest(BaseModel):
    work_min: int = 25
    break_min: int = 5


# --- 5. Routes principales ---
@app.get("/")
def read_root():
    return FileResponse("index.html")


@app.get("/status")
def get_robot_status():
    ser_obj = getattr(ohbot, "ser", None)
    usb_ok = bool(getattr(ohbot, "connected", False) and ser_obj and getattr(ser_obj, "is_open", False))
    return {
        "usb_connected": usb_ok,
        "port": getattr(ohbot, "port", ""),
        "scenario": scenario_engine.get_status(),
    }


@app.post("/reconnect")
def reconnect_usb():
    ohbot.connected = False
    ok = ensure_ohbot_connected()
    if not ok:
        raise HTTPException(status_code=503, detail="Impossible de détecter la carte Ohbot sur USB.")
    return {"status": "success", "port": getattr(ohbot, "port", "")}


@app.post("/say")
def parler(request: SpeechRequest):
    try:
        _say_with_animation(request.text)
        return {"status": "success", "message": f"Ohbot dit : {request.text}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/eyes/color")
def set_eye_color(request: EyeColorRequest):
    for channel, val in (
        ("r", request.r),
        ("g", request.g),
        ("b", request.b),
        ("brightness", request.brightness),
    ):
        if val < 0 or val > 10:
            raise HTTPException(
                status_code=400,
                detail=f"Valeur '{channel}' invalide ({val}). Doit être entre 0 et 10.",
            )
    try:
        _set_eyes_scaled(request.r, request.g, request.b, request.brightness, request.swap_rg)
        scale = request.brightness / 10.0
        return {
            "status": "success",
            "r": round(request.r * scale),
            "g": round(request.g * scale),
            "b": round(request.b * scale),
            "brightness": request.brightness,
            "swap_rg": request.swap_rg,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/move/{motor_key}/{position}")
def bouger_moteur(motor_key: str, position: int):
    if motor_key not in CONFIG["motors"]:
        raise HTTPException(status_code=404, detail=f"Moteur '{motor_key}' inconnu.")

    motor_info = CONFIG["motors"][motor_key]
    motor_id = motor_info["id"]
    min_pos = motor_info["min"]
    max_pos = motor_info["max"]
    motor_name = motor_info["name"]

    if position < min_pos or position > max_pos:
        raise HTTPException(
            status_code=400,
            detail=(
                f"DANGER: Position {position} refusée. "
                f"Les limites pour '{motor_name}' sont [{min_pos} - {max_pos}]."
            ),
        )

    try:
        ensure_ohbot_connected()
        ohbot.move(motor_id, position)
        ohbot.wait(0.1)
        _schedule_relax(motor_id, delay=0.55)
        return {
            "status": "success",
            "motor": motor_name,
            "position": position,
            "message": "Mouvement effectué avec succès",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur avec Ohbot : {e}")


@app.get("/config")
def get_config():
    return CONFIG


@app.post("/config")
def update_config(new_config: Dict[str, Any]):
    global CONFIG
    try:
        with open("config.json", "w", encoding="utf-8") as file:
            json.dump(new_config, file, indent=2, ensure_ascii=False)
        CONFIG = new_config
        return {"status": "success", "message": "Configuration mise à jour et sauvegardée"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset")
def reset_robot():
    try:
        ensure_ohbot_connected()
        ohbot.reset()
        ohbot.wait(0.35)
        _relax_servos()
        return {"status": "success", "message": "Robot en position neutre"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shutdown")
def eteindre():
    try:
        ohbot.reset()
        ohbot.close()
        return {"status": "success", "message": "Connexion fermée"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 6. Routes Scénarios, Attitudes, Planning & Flux ---
@app.get("/scenarios")
def get_scenarios():
    return {
        "config": scenario_engine.config,
        "status": scenario_engine.get_status(),
    }


@app.post("/scenarios")
def update_scenarios(new_scenarios: Dict[str, Any]):
    try:
        saved = scenario_engine.update_config(new_scenarios)
        return {"status": "success", "config": saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scenarios/attitude/{attitude}")
def trigger_attitude(attitude: str):
    msg = scenario_engine.trigger_attitude(attitude)
    return {"status": "success", "message": msg}


@app.post("/scenarios/trigger/{kind}")
def trigger_content(kind: str):
    if kind not in ("jokes", "haikus", "news", "reddit", "weather", "phrases"):
        raise HTTPException(status_code=400, detail=f"Type inconnu : {kind}")
    msg = scenario_engine.trigger_content(kind)
    return {"status": "success", "message": msg}


@app.post("/scenarios/nap/{minutes}")
def start_nap(minutes: int):
    scenario_engine.start_nap(minutes)
    return {"status": "success", "message": f"Sieste de {minutes} min lancée"}


@app.post("/scenarios/wake")
def wake_robot():
    scenario_engine.wake_up()
    return {"status": "success", "message": "Réveil du robot lancé"}


@app.post("/scenarios/pomodoro")
def start_pomodoro(req: PomodoroRequest):
    scenario_engine.start_pomodoro(req.work_min, req.break_min)
    return {
        "status": "success",
        "message": f"Pomodoro lancé ({req.work_min} min focus / {req.break_min} min pause)",
    }

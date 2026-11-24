"""Script de vie autonome pour Ohbot 2.2.

Fait vivre le robot en continu via son API HTTP (mouvements de tête,
regards, clignements, couleurs des yeux et phrases aléatoires).
"""

import argparse
import random
import signal
import sys
import time
from typing import Dict, Any

import requests

PHRASES = [
    "Bonjour ! Tout va bien par ici.",
    "Je garde un œil sur l'atelier.",
    "N'hésitez pas à venir discuter avec moi.",
    "Mes moteurs sont bien chauffés et prêts.",
    "Il fait calme aujourd'hui, non ?",
    "J'observe tout ce qui bouge autour de moi.",
    "Quelqu'un a pensé à prendre un café ?",
    "Mes yeux brillent de mille feux.",
    "Je suis toujours prêt pour une nouvelle mission.",
    "Tout est sous contrôle.",
]

EYE_PALETTES = [
    (0, 4, 10),   # Bleu doux
    (0, 10, 6),   # Turquoise
    (4, 0, 10),   # Violet
    (0, 8, 2),    # Vert émeraude
    (2, 6, 10),   # Bleu ciel
]


def clamp(val: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(max_val, val))


def fetch_motors_config(base_url: str) -> Dict[str, Any]:
    res = requests.get(f"{base_url}/config", timeout=5)
    res.raise_for_status()
    return res.json().get("motors", {})


def move_motor(base_url: str, motor_key: str, position: int):
    requests.post(f"{base_url}/move/{motor_key}/{position}", timeout=5)


def set_eyes(base_url: str, r: int, g: int, b: int, brightness: int = 8):
    requests.post(
        f"{base_url}/eyes/color",
        json={"r": r, "g": g, "b": b, "brightness": brightness, "swap_rg": False},
        timeout=5,
    )


def say(base_url: str, text: str):
    requests.post(f"{base_url}/say", json={"text": text}, timeout=30)


def reset_robot(base_url: str):
    try:
        requests.post(f"{base_url}/reset", timeout=5)
        set_eyes(base_url, 0, 4, 10, brightness=8)
    except requests.RequestException:
        pass


def neutral_pos(motors: Dict[str, Any], key: str) -> int:
    info = motors.get(key)
    if not info:
        return 5
    return clamp(5, info["min"], info["max"])


def bounded_random(motors: Dict[str, Any], key: str, delta: int) -> int:
    info = motors.get(key)
    if not info:
        return 5
    center = neutral_pos(motors, key)
    return clamp(center + random.randint(-delta, delta), info["min"], info["max"])


def run_life_loop(base_url: str, speak_interval: float, quiet: bool):
    running = True

    def handle_stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"Connexion à Ohbot sur {base_url}...")
    motors = fetch_motors_config(base_url)
    print("Robot connecté. Vie autonome démarrée (Ctrl+C pour arrêter).")

    set_eyes(base_url, *random.choice(EYE_PALETTES), brightness=8)
    next_speak_at = time.monotonic() + random.uniform(speak_interval * 0.5, speak_interval)
    last_phrase = None
    step = 0

    try:
        while running:
            # Alterner head_turn et head_nod pour des mouvements fluides
            if step % 2 == 0 and "head_turn" in motors:
                move_motor(base_url, "head_turn", bounded_random(motors, "head_turn", 2))
            elif "head_nod" in motors:
                move_motor(base_url, "head_nod", bounded_random(motors, "head_nod", 2))

            # Regard & clignement des yeux
            if "eye_turn" in motors:
                move_motor(base_url, "eye_turn", bounded_random(motors, "eye_turn", 3))
            if step % 3 == 0 and "eye_tilt" in motors:
                move_motor(base_url, "eye_tilt", bounded_random(motors, "eye_tilt", 2))

            if step % 4 == 2 and "lid_blink" in motors:
                blink_min = motors["lid_blink"]["min"]
                blink_max = motors["lid_blink"]["max"]
                move_motor(base_url, "lid_blink", blink_min)
                time.sleep(0.15)
                move_motor(base_url, "lid_blink", blink_max)

            # Changer doucement la couleur des yeux de temps en temps
            if step % 10 == 0:
                set_eyes(base_url, *random.choice(EYE_PALETTES), brightness=8)

            # Parler périodiquement
            now = time.monotonic()
            if not quiet and now >= next_speak_at:
                candidates = [p for p in PHRASES if p != last_phrase]
                phrase = random.choice(candidates)
                last_phrase = phrase
                print(f"[Ohbot] {phrase}")
                set_eyes(base_url, 0, 10, 10, brightness=10)
                say(base_url, phrase)
                set_eyes(base_url, *random.choice(EYE_PALETTES), brightness=8)
                next_speak_at = time.monotonic() + random.uniform(
                    speak_interval * 0.75, speak_interval * 1.35
                )

            step += 1
            time.sleep(random.uniform(3.0, 5.5))
    except requests.RequestException as e:
        print(f"Erreur de communication avec l'API Ohbot : {e}", file=sys.stderr)
    finally:
        print("\nArrêt de la vie autonome, retour au neutre...")
        reset_robot(base_url)


def main():
    parser = argparse.ArgumentParser(description="Fait vivre Ohbot en continu via son API.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="URL de l'API Ohbot (défaut: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--speak-interval",
        type=float,
        default=45.0,
        help="Intervalle moyen en secondes entre deux phrases (défaut: 45)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Désactive la parole (mouvements et yeux uniquement)",
    )
    args = parser.parse_args()
    run_life_loop(args.url.rstrip("/"), args.speak_interval, args.quiet)


if __name__ == "__main__":
    main()

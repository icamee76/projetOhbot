"""Moteur de scénarios, attitudes, planning et flux externes (News, Reddit, Météo) pour Ohbot."""

from datetime import datetime
import html
import json
import random
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

SCENARIOS_FILE = "scenarios.json"

DEFAULT_SCENARIOS: Dict[str, Any] = {
    "autonomous": {
        "enabled": True,
        "speak_interval_sec": 60,
        "quiet_mode": False,
        "hourly_chime": True,
        "enabled_sources": ["phrases", "jokes", "haikus", "news", "reddit", "weather"],
    },
    "schedule": {
        "enabled": False,
        "wake_time": "08:30",
        "sleep_time": "19:00",
        "days": [0, 1, 2, 3, 4],
    },
    "weather": {
        "city": "Paris",
        "latitude": 48.8566,
        "longitude": 2.3522,
    },
    "news_feeds": [
        {"name": "Franceinfo - À la une", "url": "https://www.francetvinfo.fr/titres.rss"},
        {"name": "Le Monde - Une", "url": "https://www.lemonde.fr/rss/une.xml"},
    ],
    "subreddits": ["PasDeQuestionIdiote", "france", "science"],
    "phrases": [
        "Bonjour ! Tout va bien par ici.",
        "Je garde un œil attentif sur la pièce.",
        "Quelqu'un a pensé à faire une petite pause café ?",
    ],
    "jokes": [
        "Pourquoi les plongeurs plongent-ils toujours en arrière ? Parce que sinon ils tombent dans le bateau.",
        "Que dit une imprimante dans l'eau ? J'ai papier !",
    ],
    "haikus": [
        "Un éclair d'azur, mes moteurs s'ouvrent doucement, le matin sourit.",
    ],
}

WMO_WEATHER_FR: Dict[int, Tuple[str, Tuple[int, int, int]]] = {
    0: ("un grand ciel dégagé", (10, 8, 0)),
    1: ("un ciel globalement dégagé", (10, 8, 2)),
    2: ("quelques nuages", (6, 8, 10)),
    3: ("un ciel couvert", (5, 5, 7)),
    45: ("du brouillard", (6, 6, 6)),
    48: ("du brouillard givrant", (6, 7, 9)),
    51: ("une bruine légère", (2, 5, 10)),
    53: ("de la bruine", (2, 5, 10)),
    55: ("une bruine dense", (1, 4, 10)),
    61: ("une pluie faible", (0, 4, 10)),
    63: ("de la pluie", (0, 3, 10)),
    65: ("une forte pluie", (0, 2, 10)),
    71: ("quelques flocons de neige", (8, 9, 10)),
    73: ("de la neige", (9, 9, 10)),
    75: ("de fortes chutes de neige", (10, 10, 10)),
    80: ("des averses", (0, 4, 10)),
    81: ("des averses soutenues", (0, 3, 10)),
    95: ("de l'orage", (8, 0, 10)),
}


def load_scenarios() -> Dict[str, Any]:
    try:
        with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_SCENARIOS))
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged
    except FileNotFoundError:
        save_scenarios(DEFAULT_SCENARIOS)
        return json.loads(json.dumps(DEFAULT_SCENARIOS))


def save_scenarios(data: Dict[str, Any]) -> None:
    with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _http_get(url: str, timeout: float = 6.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (RaspberryPi; OhbotControlCenter/2.0)",
            "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _clean_title(raw: str) -> str:
    text = html.unescape(raw or "").strip()
    text = " ".join(text.split())
    return text


def fetch_news_headline(feeds: List[Dict[str, str]]) -> Optional[str]:
    if not feeds:
        return None
    shuffled = list(feeds)
    random.shuffle(shuffled)
    for feed in shuffled:
        url = feed.get("url", "").strip()
        source_name = feed.get("name", "Actualités").split("-")[0].strip()
        if not url:
            continue
        try:
            raw_xml = _http_get(url)
            root = ET.fromstring(raw_xml)
            titles: List[str] = []
            # RSS 2.0
            for item in root.findall(".//item/title"):
                if item.text:
                    titles.append(_clean_title(item.text))
            # Atom
            if not titles:
                for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title"):
                    if entry.text:
                        titles.append(_clean_title(entry.text))
            titles = [t for t in titles[:10] if len(t) > 10]
            if titles:
                chosen = random.choice(titles)
                return f"À la une sur {source_name} : {chosen}"
        except Exception as e:
            print(f"[News] Erreur lecture RSS {url} : {e}")
    return None


def fetch_reddit_post(subreddits: List[str]) -> Optional[str]:
    clean_subs = [s.strip().lstrip("r/").strip("/") for s in subreddits if s.strip()]
    if not clean_subs:
        return None
    random.shuffle(clean_subs)
    for sub in clean_subs:
        # 1. Essai JSON public Reddit
        try:
            url = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/hot.json?limit=12"
            data = json.loads(_http_get(url).decode("utf-8"))
            posts = [
                p["data"]["title"]
                for p in data.get("data", {}).get("children", [])
                if not p.get("data", {}).get("stickied")
                and not p.get("data", {}).get("over_18")
                and p.get("data", {}).get("title")
            ]
            if posts:
                title = _clean_title(random.choice(posts[:10]))
                return f"Vu sur Reddit {sub} : {title}"
        except Exception:
            pass
        # 2. Fallback RSS Reddit
        try:
            rss_url = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/hot/.rss"
            root = ET.fromstring(_http_get(rss_url))
            titles = [
                _clean_title(el.text)
                for el in root.findall(".//{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title")
                if el.text
            ]
            if titles:
                return f"Vu sur Reddit {sub} : {random.choice(titles[:10])}"
        except Exception as e:
            print(f"[Reddit] Erreur r/{sub} : {e}")
    return None


def fetch_weather_report(weather_cfg: Dict[str, Any]) -> Optional[Tuple[str, Tuple[int, int, int]]]:
    lat = weather_cfg.get("latitude", 48.8566)
    lon = weather_cfg.get("longitude", 2.3522)
    city = weather_cfg.get("city", "ici")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
    )
    try:
        data = json.loads(_http_get(url).decode("utf-8"))
        current = data.get("current", {})
        temp = round(float(current.get("temperature_2m", 0)))
        code = int(current.get("weather_code", 0))
        desc, color = WMO_WEATHER_FR.get(code, ("un temps variable", (2, 6, 10)))
        return (f"Point météo à {city} : il fait {temp} degrés avec {desc}.", color)
    except Exception as e:
        print(f"[Météo] Erreur Open-Meteo : {e}")
        return None


class ScenarioEngine:
    """Pilote la vie autonome, les attitudes, les siestes, le Pomodoro et le planning."""

    def __init__(
        self,
        move_fn: Callable[[str, int, int], None],
        say_fn: Callable[[str], None],
        eyes_fn: Callable[[int, int, int, int], None],
        relax_fn: Callable[[], None],
        get_motors_fn: Callable[[], Dict[str, Any]],
    ):
        self._move = move_fn
        self._say = say_fn
        self._eyes = eyes_fn
        self._relax = relax_fn
        self._get_motors = get_motors_fn

        self.config: Dict[str, Any] = load_scenarios()
        self._lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.state: str = "awake"  # "awake" | "napping" | "scheduled_sleep" | "pomodoro_focus"
        self.current_activity: str = "En veille active"
        self.last_spoken: str = ""
        self.nap_until: float = 0.0
        self.pomodoro_until: float = 0.0
        self.pomodoro_break_min: int = 5
        self._next_speak_ts: float = time.time() + 35.0
        self._last_chime_hour: Optional[Tuple[int, int]] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def update_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.config = new_cfg
            save_scenarios(self.config)
            interval = max(15, int(self.config.get("autonomous", {}).get("speak_interval_sec", 60)))
            self._next_speak_ts = min(self._next_speak_ts, time.time() + interval)
        return self.config

    def get_status(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "state": self.state,
            "current_activity": self.current_activity,
            "last_spoken": self.last_spoken,
            "autonomous_enabled": bool(self.config.get("autonomous", {}).get("enabled", False)),
            "nap_remaining_sec": max(0, int(self.nap_until - now)) if self.state == "napping" else 0,
            "pomodoro_remaining_sec": max(0, int(self.pomodoro_until - now)) if self.state == "pomodoro_focus" else 0,
            "next_speak_in_sec": max(0, int(self._next_speak_ts - now)),
        }

    def _neutral(self, key: str) -> int:
        info = self._get_motors().get(key)
        if not info:
            return 5
        return max(info["min"], min(info["max"], 5))

    def _bounded(self, key: str, target: int) -> int:
        info = self._get_motors().get(key)
        if not info:
            return max(0, min(10, target))
        return max(info["min"], min(info["max"], target))

    def _enter_sleep_pose(self, label: str) -> None:
        self.current_activity = label
        self._eyes(0, 0, 3, 1)
        self._move("lid_blink", 0, 4)
        self._move("head_nod", self._bounded("head_nod", 3), 3)
        time.sleep(0.5)
        self._relax()

    def _wake_up_pose(self, speak_greeting: bool = False) -> None:
        self.state = "awake"
        self.nap_until = 0.0
        self.pomodoro_until = 0.0
        self.current_activity = "Réveillé"
        self._eyes(0, 6, 10, 8)
        self._move("lid_blink", 9, 6)
        self._move("head_nod", self._neutral("head_nod"), 4)
        time.sleep(0.35)
        self._move("head_turn", self._neutral("head_turn"), 4)
        time.sleep(0.45)
        self._relax()
        if speak_greeting:
            self._speak_now("Aaah, ça fait du bien ! Je suis de nouveau opérationnel.")

    def _speak_now(self, text: str) -> None:
        self.last_spoken = text
        self._say(text)

    # --- Actions déclenchables depuis l'API / Web UI ---

    def start_nap(self, minutes: int = 15) -> None:
        def _job():
            with self._action_lock:
                self.state = "napping"
                self.nap_until = time.time() + max(1, minutes) * 60
                self._speak_now(f"Je fais une petite sieste de {minutes} minutes. À tout à l'heure.")
                self._enter_sleep_pose(f"En sieste ({minutes} min)")

        threading.Thread(target=_job, daemon=True).start()

    def wake_up(self) -> None:
        threading.Thread(
            target=lambda: self._wake_up_pose(speak_greeting=True),
            daemon=True,
        ).start()

    def start_pomodoro(self, work_min: int = 25, break_min: int = 5) -> None:
        def _job():
            with self._action_lock:
                self.state = "pomodoro_focus"
                self.pomodoro_until = time.time() + max(1, work_min) * 60
                self.pomodoro_break_min = max(1, break_min)
                self.current_activity = f"Pomodoro Focus ({work_min} min)"
                self._eyes(0, 3, 10, 7)
                self._move("lid_blink", 9, 6)
                self._speak_now(
                    f"C'est parti pour {work_min} minutes de concentration. Je reste silencieux !"
                )
                self._relax()

        threading.Thread(target=_job, daemon=True).start()

    def trigger_attitude(self, attitude: str) -> str:
        def _job():
            with self._action_lock:
                self.state = "awake"
                self.nap_until = 0.0
                if attitude == "happy":
                    self.current_activity = "Attitude : Joyeux"
                    self._eyes(0, 10, 4, 10)
                    self._move("lid_blink", 10, 8)
                    for pos in (7, 4, 6):
                        self._move("head_nod", self._bounded("head_nod", pos), 6)
                        time.sleep(0.35)
                    self._speak_now("Je suis super content aujourd'hui !")
                elif attitude == "curious":
                    self.current_activity = "Attitude : Curieux"
                    self._eyes(6, 0, 10, 9)
                    self._move("head_turn", self._bounded("head_turn", 2), 4)
                    self._move("eye_turn", 2, 6)
                    time.sleep(0.6)
                    self._move("head_turn", self._bounded("head_turn", 8), 4)
                    self._move("eye_turn", 8, 6)
                    time.sleep(0.6)
                    self._move("head_turn", self._neutral("head_turn"), 4)
                    self._speak_now("Tiens tiens... qu'est-ce qui se passe par là ?")
                elif attitude == "grumpy":
                    self.current_activity = "Attitude : Grognon"
                    self._eyes(10, 1, 0, 9)
                    self._move("lid_blink", 4, 6)
                    for pos in (3, 7, 3, 5):
                        self._move("head_turn", self._bounded("head_turn", pos), 6)
                        time.sleep(0.3)
                    self._speak_now("Franchement, on pourrait me laisser tranquille deux minutes.")
                    self._move("lid_blink", 9, 6)
                elif attitude == "zen":
                    self.current_activity = "Attitude : Zen"
                    self._eyes(0, 8, 7, 5)
                    self._move("lid_blink", 3, 3)
                    time.sleep(0.4)
                    haikus = self.config.get("haikus") or DEFAULT_SCENARIOS["haikus"]
                    self._speak_now(random.choice(haikus))
                    self._move("lid_blink", 9, 4)
                elif attitude == "party":
                    self.current_activity = "Attitude : Fête"
                    colors = [(10, 0, 4), (0, 10, 4), (0, 4, 10), (10, 8, 0), (8, 0, 10)]
                    for idx, (r, g, b) in enumerate(colors):
                        self._eyes(r, g, b, 10)
                        self._move("head_turn", self._bounded("head_turn", 3 if idx % 2 == 0 else 7), 7)
                        time.sleep(0.35)
                    self._move("head_turn", self._neutral("head_turn"), 5)
                    self._speak_now("Ambiance discothèque activée !")
                else:
                    self._wake_up_pose(speak_greeting=True)
                time.sleep(0.4)
                self._relax()
                self.current_activity = "En veille active"

        threading.Thread(target=_job, daemon=True).start()
        return f"Attitude '{attitude}' lancée"

    def trigger_content(self, kind: str) -> str:
        def _job():
            with self._action_lock:
                self._run_single_source(kind)

        threading.Thread(target=_job, daemon=True).start()
        return f"Action '{kind}' lancée"

    def _run_single_source(self, kind: str) -> bool:
        if kind == "jokes":
            jokes = self.config.get("jokes") or []
            if not jokes:
                return False
            joke = random.choice(jokes)
            self.current_activity = "Raconte une blague"
            self._eyes(10, 7, 0, 9)
            self._speak_now(joke)
            # Petit clin d'œil final
            self._move("lid_blink", 1, 8)
            time.sleep(0.18)
            self._move("lid_blink", 9, 8)
            self._relax()
            return True

        if kind == "haikus":
            haikus = self.config.get("haikus") or []
            if not haikus:
                return False
            self.current_activity = "Récite un haïku"
            self._eyes(0, 9, 7, 6)
            self._speak_now(random.choice(haikus))
            self._relax()
            return True

        if kind == "news":
            self.current_activity = "Lit les actualités"
            headline = fetch_news_headline(self.config.get("news_feeds") or [])
            if not headline:
                return False
            self._eyes(0, 5, 10, 9)
            self._speak_now(headline)
            self._relax()
            return True

        if kind == "reddit":
            self.current_activity = "Lit Reddit"
            post = fetch_reddit_post(self.config.get("subreddits") or [])
            if not post:
                return False
            self._eyes(10, 3, 0, 9)
            self._speak_now(post)
            self._relax()
            return True

        if kind == "weather":
            self.current_activity = "Annonce la météo"
            res = fetch_weather_report(self.config.get("weather") or {})
            if not res:
                return False
            text, (r, g, b) = res
            self._eyes(r, g, b, 9)
            self._speak_now(text)
            self._relax()
            return True

        # Default: phrases
        phrases = self.config.get("phrases") or []
        if not phrases:
            return False
        self.current_activity = "Parle spontanément"
        self._eyes(0, 8, 10, 9)
        self._speak_now(random.choice(phrases))
        self._relax()
        return True

    # --- Planificateur & Boucle autonome ---

    def _is_within_schedule(self, now_dt: datetime) -> bool:
        sched = self.config.get("schedule", {})
        if not sched.get("enabled", False):
            return True
        days = sched.get("days", [0, 1, 2, 3, 4, 5, 6])
        if now_dt.weekday() not in days:
            return False
        try:
            wh, wm = (int(x) for x in sched.get("wake_time", "08:30").split(":"))
            sh, sm = (int(x) for x in sched.get("sleep_time", "19:00").split(":"))
            cur = now_dt.hour * 60 + now_dt.minute
            wake_m = wh * 60 + wm
            sleep_m = sh * 60 + sm
            if wake_m <= sleep_m:
                return wake_m <= cur < sleep_m
            return cur >= wake_m or cur < sleep_m
        except Exception:
            return True

    def _loop(self) -> None:
        step = 0
        next_idle_move_ts = time.time() + 4.0

        while not self._stop_event.wait(1.0):
            now = time.time()
            now_dt = datetime.now()

            # 1. Vérification du planning Allumé / Éteint
            if not self._is_within_schedule(now_dt):
                if self.state != "scheduled_sleep":
                    with self._action_lock:
                        self.state = "scheduled_sleep"
                        self._enter_sleep_pose("Éteint (selon planning)")
                continue
            elif self.state == "scheduled_sleep":
                with self._action_lock:
                    self._wake_up_pose(speak_greeting=False)

            # 2. Fin de sieste éventuelle
            if self.state == "napping":
                if now >= self.nap_until:
                    with self._action_lock:
                        self._wake_up_pose(speak_greeting=True)
                else:
                    continue

            # 3. Fin de Pomodoro éventuelle
            if self.state == "pomodoro_focus":
                if now >= self.pomodoro_until:
                    with self._action_lock:
                        self.state = "awake"
                        self.current_activity = "Pause Pomodoro !"
                        self._eyes(0, 10, 4, 10)
                        self._speak_now(
                            f"Bravo ! Session terminée, prends {self.pomodoro_break_min} minutes de pause."
                        )
                        self._relax()
                else:
                    continue

            auto_cfg = self.config.get("autonomous", {})
            if not auto_cfg.get("enabled", False):
                self.current_activity = "Mode autonome en pause"
                continue

            # 4. Carillon des heures pleines
            if auto_cfg.get("hourly_chime", False) and not auto_cfg.get("quiet_mode", False):
                chime_key = (now_dt.day, now_dt.hour)
                if now_dt.minute == 0 and self._last_chime_hour != chime_key:
                    self._last_chime_hour = chime_key
                    with self._action_lock:
                        if now_dt.hour == 12:
                            self._speak_now("Il est midi ! Bon appétit à tous.")
                        else:
                            self._speak_now(f"Il est {now_dt.hour} heures.")

            # 5. Petits mouvements de vie autonome
            if now >= next_idle_move_ts and not self._action_lock.locked():
                with self._action_lock:
                    self.current_activity = "Vie autonome"
                    if step % 2 == 0:
                        target = self._neutral("head_turn") + random.choice([-2, -1, 0, 1, 2])
                        self._move("head_turn", self._bounded("head_turn", target), 4)
                    else:
                        target = self._neutral("head_nod") + random.choice([-1, 0, 1, 2])
                        self._move("head_nod", self._bounded("head_nod", target), 4)

                    self._move("eye_turn", random.randint(2, 8), 5)
                    if step % 3 == 0:
                        self._move("eye_tilt", random.randint(3, 7), 5)
                    if step % 4 == 2:
                        self._move("lid_blink", 0, 8)
                        time.sleep(0.15)
                        self._move("lid_blink", 9, 8)
                    if step % 8 == 0:
                        palette = random.choice([(0, 4, 10), (0, 9, 6), (4, 0, 10), (2, 6, 10)])
                        self._eyes(palette[0], palette[1], palette[2], 7)

                    time.sleep(0.45)
                    self._relax()
                step += 1
                next_idle_move_ts = now + random.uniform(4.0, 7.5)

            # 6. Intervention vocale périodique (blague, haïku, actu, reddit, météo, phrase)
            if not auto_cfg.get("quiet_mode", False) and now >= self._next_speak_ts:
                sources = list(auto_cfg.get("enabled_sources") or ["phrases"])
                random.shuffle(sources)
                with self._action_lock:
                    for src in sources:
                        if self._run_single_source(src):
                            break
                    self.current_activity = "Vie autonome"
                interval = max(15, int(auto_cfg.get("speak_interval_sec", 60)))
                self._next_speak_ts = time.time() + random.uniform(interval * 0.8, interval * 1.3)

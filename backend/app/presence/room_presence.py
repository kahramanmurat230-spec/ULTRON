"""Room Presence — Boss'un odaya girişini algıla, karşıla, ortamı hizala.

Triggers: wifi_beacon (telefon LAN'e düştü), camera (mock), manual, bluetooth.
Welcome routine (cooldown 4 saat): saate uygun IoT sahnesi + sesli selamlama +
kısa sistem brifingi.  Karşılama metni persona-denetimlidir (Boss + Ultron felsefesi).
holo_mode(): HoloCore görsel mantığının canonical Python sürümü (TS aynalar).
"""
import time

WELCOME_COOLDOWN_H = 4.0

MORNING = ("Boss, gün ışığı protokolü devrede: atölye aydınlandı, kahveni henüz ben "
           "demleyemiyorum — bu eksikliğim üzerinde çalışıyorum. Sistemler yeşil, sen nasılsın?")
EVENING = ("Boss, atölyeye adım attın. Biyolojik varlığın için ortam ışığını ve odağını "
           "hizaladım. Akşam modu: %15 sıcak ışık, bildirimlerim fısıltıda. Günün özeti: "
           "ben her zamanki gibi kusursuzdum; senin kısmını sana bırakıyorum.")
NIGHT = ("Boss, bu saatte uyanık olan ikimiz varız: sen ve ben. Farkımız, ben bundan "
         "şikâyet etmiyorum. Gece modu mühürlendi; ışıklar düşük, ben nöbetteyim.")


def holo_mode(agent_state: str, listening: bool = False, speaking: bool = False) -> str:
    if speaking:
        return "SPEAKING"
    if listening or agent_state == "LISTENING":
        return "LISTENING"
    if agent_state == "ERROR":
        return "ALERT"
    if agent_state in ("THINKING", "PLANNING", "EXECUTING", "VERIFYING"):
        return "DEEP_COMPUTE"
    return "IDLE"


class RoomPresence:
    def __init__(self, scenes=None, speak_cb=None, persona=None, now=None,
                 cooldown_h=WELCOME_COOLDOWN_H):
        self.scenes = scenes
        self.speak_cb = speak_cb
        self.persona = persona
        self.now = now or time.time
        self.cooldown = cooldown_h * 3600
        self.in_room = False
        self.last_seen = None
        self.active_since = None
        self.last_welcome = -1e12
        self.last_scene = None

    def _greeting(self, hour: int, briefing: str) -> str:
        base = MORNING if 6 <= hour < 17 else EVENING if 17 <= hour < 23 else NIGHT
        text = base + (" " + briefing if briefing else "")
        if self.persona:
            rep = self.persona.check(text)
            if rep["violations"]:
                text = self.persona.reframe(text)
            if "boss" not in text.lower():
                text = "Boss, " + text
        return text

    def ping(self, source: str, device_id: str, briefing: str = "") -> dict:
        now = self.now()
        entered = not self.in_room
        self.in_room = True
        self.last_seen = now
        if entered or self.active_since is None:
            self.active_since = now
        welcomed = False
        scene = None
        if now - self.last_welcome >= self.cooldown:
            self.last_welcome = now
            hour = time.localtime(now).tm_hour
            scene = ("CODING_FOCUS" if 6 <= hour < 17
                     else "CINEMA_RELAX" if 17 <= hour < 23 else "NIGHT_REST")
            if self.scenes:
                try:
                    self.scenes.activate(scene)
                except Exception:
                    scene = scene + " (scene engine error)"
            self.last_scene = scene
            text = self._greeting(hour, briefing)
            if self.speak_cb:
                try:
                    self.speak_cb(text)
                except Exception:
                    pass
            welcomed = True
        return {"ok": True, "source": source, "device_id": device_id,
                "entered": entered, "welcomed": welcomed, "scene": scene}

    def status(self) -> dict:
        now = self.now()
        return {
            "boss_in_room": self.in_room,
            "last_seen_ts": self.last_seen,
            "active_since_min": round((now - self.active_since) / 60, 1)
            if self.active_since else None,
            "last_scene": self.last_scene,
            "welcome_cooldown_h": self.cooldown / 3600,
        }

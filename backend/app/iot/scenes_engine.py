"""Scenes Engine — Boss'un özel senaryoları.

CODING_FOCUS : masa ışığı %100, gereksiz priz OFF, Sentinel CODING (manual)
NIGHT_REST   : tüm ışık+ekran OFF, Ultron fısıltı/kısa-cevap modu
CINEMA_RELAX : ışıklar %15 sıcak
ALL_OFF      : her şey OFF
"""
SCENES = ("CODING_FOCUS", "NIGHT_REST", "CINEMA_RELAX", "ALL_OFF")


class ScenesEngine:
    def __init__(self, nexus, sentinel=None, persona=None):
        self.nexus = nexus
        self.sentinel = sentinel
        self.persona = persona
        self.last_scene = None

    def activate(self, name: str) -> dict:
        n = self.nexus
        results = []
        if name == "CODING_FOCUS":
            results.append(n.control("light_masa", "set_value", 100))
            results.append(n.control("switch_priz", "turn_off"))
            if self.sentinel:
                self.sentinel.manual_mode = "CODING_MODE"
            note = "CODING_FOCUS aktif: ışık %100, priz mühürlü, nöbet kod modunda."
        elif name == "NIGHT_REST":
            for d in n.list():
                if d["domain"] in ("light", "media"):
                    results.append(n.control(d["device_id"], "turn_off"))
            if self.sentinel:
                self.sentinel.manual_mode = "IDLE_MODE"
            if self.persona:
                self.persona.set_emotion_hint(
                    "NIGHT_REST: fısıltı modu — çok kısa, çok yumuşak cevap ver.", "TIRED")
            note = "NIGHT_REST: ışıklar söndü. Ben de fısıltı modundayım; iyi uykular, Boss."
        elif name == "CINEMA_RELAX":
            results.append(n.control("light_masa", "set_value", 15))
            results.append(n.control("light_amb", "set_value", 15))
            note = "CINEMA_RELAX: %15 sıcak ışık. Sahne hazır, Boss."
        elif name == "ALL_OFF":
            for d in n.list():
                if d["domain"] in ("light", "switch", "media", "climate"):
                    results.append(n.control(d["device_id"], "turn_off"))
            note = "ALL_OFF: her şey söndü. Sessizliğin de bir mimarisi var."
        else:
            return {"ok": False, "error": f"unknown scene: {name}"}
        self.last_scene = name
        ok = all(r.get("ok") for r in results if isinstance(r, dict))
        return {"ok": ok, "scene": name, "results": results, "note": note}

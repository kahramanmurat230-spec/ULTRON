"""IoT Nexus — dual-sided device control (PC brain or independent mobile node).

Drivers: mock (virtual demo devices), homeassistant (REST + LL token),
http (Tasmota/Shelly-style local endpoints).  Registry in SQLite.
Natural-language fuzzy matcher maps "ışığı aç / masayı yak / klimayı 24 yap"
to (device_id, intent, value).  Unreachable drivers NEVER fake success.
"""
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

ON_WORDS = ("aç", "ac", "yak", "başlat", "açık")
OFF_WORDS = ("kapat", "söndür", "sondur", "kapat")
TOGGLE_WORDS = ("değiştir", "toggle")


class IoTNexus:
    def __init__(self, db_path="data/iot/iot_devices.db",
                 ha_url=None, ha_token=None, vault=None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ha_url = ha_url
        self.ha_token = ha_token
        # PHASE: secret ARGÜMANDAN DEĞİL vault'tan çözülür (plaintext DB yok)
        self.vault = vault
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS iot_devices(
                device_id TEXT PRIMARY KEY, name TEXT, domain TEXT,
                state TEXT, value REAL, room TEXT, driver TEXT, address TEXT)""")
        if not self.list():
            self._seed_demo()

    def _seed_demo(self):
        demo = [
            ("light_masa", "Masa Lambası", "light", "on", 80, "Çalışma", "mock", None),
            ("light_amb", "Ambiyans Işığı", "light", "off", 0, "Salon", "mock", None),
            ("climate_ac", "Klima", "climate", "off", 22, "Salon", "mock", None),
            ("switch_priz", "Gereksiz Priz", "switch", "on", None, "Çalışma", "mock", None),
            ("media_tv", "TV", "media", "off", None, "Salon", "mock", None),
        ]
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT OR IGNORE INTO iot_devices VALUES(?,?,?,?,?,?,?,?)", demo)

    # ------------------------------------------------------------ registry
    def register(self, device_id, name, domain, room="?", driver="mock", address=None):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO iot_devices VALUES(?,?,?,?,?,?,?,?)",
                       (device_id, name, domain, "off", None, room, driver, address))
        return self.get(device_id)

    def list(self, include_simulated: bool = True):
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT device_id,name,domain,state,value,room,driver,address"
                              " FROM iot_devices").fetchall()
        out = []
        for r in rows:
            d = {"device_id": r[0], "name": r[1], "domain": r[2], "state": r[3],
                 "value": r[4], "room": r[5], "driver": r[6], "address": r[7],
                 # PHASE: mock sürücü = simülasyon, gerçek cihaz ASLA mock
                 # etiketlenmez (sahte başarı YASAK)
                 "is_simulated": r[6] == "mock"}
            if include_simulated or not d["is_simulated"]:
                out.append(d)
        return out

    def _ha_token(self) -> str | None:
        """Token önceliği: vault (şifreli) → ctor arg'ı → None."""
        if self.vault is not None:
            t = self.vault.get("homeassistant_token")
            if t:
                return t
        return self.ha_token or None

    def get(self, device_id):
        for d in self.list():
            if d["device_id"] == device_id:
                return d
        return None

    # ------------------------------------------------------------ control
    def _dispatch(self, dev, action, value):
        if dev["driver"] == "mock":
            return {"ok": True, "driver": "mock"}
        if dev["driver"] == "homeassistant":
            if not self.ha_url:
                return {"ok": False, "error": "HA base URL yok", "driver": "homeassistant"}
            svc = {"turn_on": "turn_on", "turn_off": "turn_off",
                   "toggle": "toggle"}.get(action)
            url = f"{self.ha_url.rstrip('/')}/api/services/{dev['domain']}/{svc or 'turn_on'}"
            body = {"entity_id": dev["address"] or dev["device_id"]}
            if action == "set_value" and value is not None:
                body = {"entity_id": body["entity_id"],
                        "temperature" if dev["domain"] == "climate"
                        else "brightness_pct": value}
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {self.ha_token or ''}",
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=3) as r:
                    r.read()
                return {"ok": True, "driver": "homeassistant"}
            except Exception as e:
                return {"ok": False, "error": str(e)[:120], "driver": "homeassistant"}
        if dev["driver"] == "http" and dev["address"]:
            url = dev["address"].format(action=action, value=value if value is not None else "")
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    r.read()
                return {"ok": True, "driver": "http"}
            except Exception as e:
                return {"ok": False, "error": str(e)[:120], "driver": "http"}
        return {"ok": False, "error": "unknown driver"}

    def control(self, device_id, action, value=None) -> dict:
        dev = self.get(device_id)
        if not dev:
            return {"ok": False, "error": f"unknown device: {device_id}"}
        res = self._dispatch(dev, action, value)
        res["is_simulated"] = dev["driver"] == "mock"  # dürüst etiket
        if res.get("ok"):
            state = "on" if action in ("turn_on",) else "off" if action == "turn_off" \
                else ("off" if dev["state"] == "on" else "on") if action == "toggle" else dev["state"]
            val = value if (action == "set_value" and value is not None) else dev["value"]
            if action == "set_value":
                state = "on"
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE iot_devices SET state=?, value=? WHERE device_id=?",
                           (state, val, device_id))
            res["device"] = self.get(device_id)
        return res

    # ------------------------------------------------------------ fuzzy NL
    def fuzzy_match(self, text: str):
        t = (text or "").lower()
        intent = None
        if any(w in t for w in OFF_WORDS):
            intent = "turn_off"
        elif any(w in t for w in TOGGLE_WORDS):
            intent = "toggle"
        elif any(w in t for w in ON_WORDS):
            intent = "turn_on"
        m = re.search(r"(\d{1,3})\s*(?:derece|°|yap|ayarla)", t)
        value = float(m.group(1)) if m else None
        if value is not None:
            intent = "set_value"
        if intent is None:
            return None
        best, best_score = None, 0
        for d in self.list():
            score = 0
            for tok in re.findall(r"[\wçğıöşü]+", d["name"].lower() + " " + d["room"].lower()):
                if len(tok) >= 3 and tok in t:
                    score += 2
                elif len(tok) >= 4 and tok[:4] in t:
                    score += 1
            if d["domain"] == "climate" and ("klima" in t or "serin" in t or "ısın" in t):
                score += 3
            if d["domain"] == "light" and ("ışık" in t or "lamba" in t or "aydın" in t):
                score += 1
            if score > best_score:
                best, best_score = d, score
        if best is None or best_score == 0:
            return None
        return {"device_id": best["device_id"], "intent": intent, "value": value,
                "score": best_score}

    # ------------------------------------------------------------ discovery
    def discover(self) -> dict:
        found = []
        if self.ha_url:
            try:
                req = urllib.request.Request(self.ha_url.rstrip("/") + "/api/states",
                                             headers={"Authorization": f"Bearer {self.ha_token or ''}"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    states = json.loads(r.read())
                for s in states[:50]:
                    eid = s.get("entity_id", "")
                    dom = eid.split(".")[0]
                    if dom in ("light", "switch", "climate", "media_player"):
                        did = eid.replace(".", "_")
                        if not self.get(did):
                            self.register(did, s.get("attributes", {}).get("friendly_name", eid),
                                          dom if dom != "media_player" else "media",
                                          "?", "homeassistant", eid)
                            found.append(did)
            except Exception as e:
                return {"ok": False, "error": f"HA keşfi başarısız: {str(e)[:100]}",
                        "found": found, "driver": "homeassistant"}
        return {"ok": True, "found": found,
                "note": "mock envanter hazır; HA yoksa sanal cihazlarla tam test"}

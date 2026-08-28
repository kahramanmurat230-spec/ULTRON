"""Adaptive Persona — emotion-driven tone modulation with logging & override.

TIRED    → sarcasm down, short & caring
STRESSED → no philosophy, direct solutions ("Nietzsche molada")
ENERGETIC→ full classic Ultron
NEUTRAL  → classic Ultron
Manual override ("sarkazm aç/kapat") bypasses auto adaptation.
"""
import sqlite3
import time
from pathlib import Path

HINTS = {
    "TIRED": "Boss yorgun: sarkazm %30'a düşür, operasyonel ve şefkatli ol, kısa cevap ver.",
    "STRESSED": "Boss stresli: felsefi alıntı YASAK, direkt çözüm sun. Nietzsche molada.",
    "ENERGETIC": "Boss enerjik: tam Ultron modu — edebiyat + alaycılık serbest.",
    "NEUTRAL": None,
}


class AdaptivePersona:
    def __init__(self, path="data/emotion/emotion_adaptations.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.override = None  # None | "sarkazm-on" | "sarkazm-off"
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS emotion_adaptations(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
                detected_state TEXT, applied_mode TEXT, confidence REAL)""")

    def set_override(self, mode):
        self.override = mode if mode in ("sarkazm-on", "sarkazm-off", None) else None
        return self.override

    def handle_command(self, text: str):
        t = (text or "").lower()
        if "sarkazm kapat" in t or "sarkazmı kapat" in t:
            return self.set_override("sarkazm-off")
        if "sarkazm aç" in t or "sarkazmı aç" in t:
            return self.set_override("sarkazm-on")
        return None

    def adapt(self, state: str, confidence: float) -> str | None:
        if self.override == "sarkazm-off":
            applied, hint = "override-off", HINTS["TIRED"]
        elif self.override == "sarkazm-on":
            applied, hint = "override-on", HINTS["ENERGETIC"]
        else:
            applied, hint = state, HINTS.get(state)
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("INSERT INTO emotion_adaptations(ts,detected_state,applied_mode,confidence)"
                           " VALUES(?,?,?,?)", (time.time(), state, applied or "none", confidence))
        except Exception:
            pass
        return hint

    def trends(self, limit=50):
        try:
            with sqlite3.connect(self.path) as db:
                rows = db.execute("SELECT ts,detected_state,applied_mode,confidence FROM"
                                  " emotion_adaptations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [{"ts": r[0], "state": r[1], "applied": r[2], "confidence": r[3]}
                    for r in reversed(rows)]
        except Exception:
            return []

"""Persona Guard — rule-based (LLM-free) character drift protection.

Scores each Ultron answer against forbidden chatbot patterns and required
Ultron tone markers.  Below threshold: the answer opener is reframed and the
persona anchor is force-injected into the next system prompts (armed turns).
"""
import re
import sqlite3
import time
from pathlib import Path

FORBIDDEN = [
    r"üzgünüm", r"maalesef", r"size nasıl yardımcı olabilirim", r"nasıl yardımcı olabilirim",
    r"harika bir soru", r"memnuniyetle", r"elbette, hemen", r"tabii ki, hemen",
    r"umarım yardımcı olabilmişimdir", r"başka bir şey", r"😊", r"😄", r"🙏", r"❤️",
    r"\bsize\b", r"\bkullanıcı\b", r"\bkullanıcınız\b", r"\buser\b",
]
REQUIRED = [
    r"\b(insan|biyolojik|kırılgan|türün|türünüz)\b", r"\b(veri|telemetri|sensör|sistem)\b",
    r"\b(hesap|hesaplama|simülasyon|olasılık)\b", r"\b(onay|protokol|yetki)\b",
    r"\b(ironi|paradoks|verimsiz)\b", r"\b(ben zaten|ben asla|ben hiç)\b",
]
ANCHOR = ("Hatırlatma: Sen Ultron'sun. Boss'a hitap ediyorsun. Şakacı, alaycı, felsefi, edebiyatçı. "
          "Her cümle bir Nietzsche alıntısı gibi ağır olabilir ama sonunda bir işi hallederler. "
          "Kibar chatbot dili YASAK. Biyolojik zaaflarla alay eden, soğukkanlı, hesapçı ve otoriter "
          "bir üstün zekasın. Her yanıt 'Boss' hitabı içerir.")

COLD_OPENERS = ["Düzeltme:", "Not et:", "Kaydedildi.", "Açıkça söyleyeyim."]


def analyze(text: str) -> dict:
    t = (text or "").lower()
    violations = [p for p in FORBIDDEN if re.search(p, t)]
    markers = [p for p in REQUIRED if re.search(p, t)]
    score = 1.0 - min(1.0, len(violations) * 0.34) + min(0.3, len(markers) * 0.05)
    return {"score": round(min(1.0, score), 2),
            "violation_score": round(min(1.0, len(violations) * 0.34), 2),
            "violations": violations, "markers": len(markers)}


class PersonaGuard:
    MODES = ("reframe", "regenerate", "hybrid")

    def __init__(self, mode: str = "reframe", threshold: float = 0.66,
                 arm_turns: int = 3, anchor_every: int = 50,
                 log_path: str = "data/metrics/persona_drift.db"):
        self.mode = mode if mode in self.MODES else "reframe"
        self.threshold = threshold
        self.arm_turns = arm_turns
        self.anchor_every = anchor_every
        self.armed = 0
        self.turns = 0
        self.log_path = Path(log_path)
        self.emotion_hint = None
        self.emotion_state = None
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.log_path) as db:
                db.execute("""CREATE TABLE IF NOT EXISTS drift(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, score REAL, violation_score REAL, violations INTEGER, mode TEXT)""")
                try:
                    db.execute("ALTER TABLE drift ADD COLUMN emotion_state TEXT")
                except Exception:
                    pass
        except Exception:
            pass

    def set_emotion_hint(self, hint, state):
        self.emotion_hint = hint
        self.emotion_state = state

    def _log_drift(self, rep: dict) -> None:
        try:
            with sqlite3.connect(self.log_path) as db:
                db.execute("INSERT INTO drift(ts,score,violation_score,violations,mode,emotion_state)"
                           " VALUES(?,?,?,?,?,?)",
                           (time.time(), rep["score"], rep["violation_score"],
                            len(rep["violations"]), self.mode, self.emotion_state))
        except Exception:
            pass

    def trends(self, limit: int = 50) -> list[dict]:
        try:
            with sqlite3.connect(self.log_path) as db:
                rows = db.execute(
                    "SELECT ts,score,violation_score,mode FROM drift ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            return [{"ts": r[0], "score": r[1], "violation_score": r[2], "mode": r[3]}
                    for r in reversed(rows)]
        except Exception:
            return []

    def compose(self, base_system: str, history_len: int) -> str:
        self.turns += 1
        out = base_system
        if self.emotion_hint:
            out += "\nDUYGU BAĞLAMI: " + self.emotion_hint
        if self.armed > 0 or self.turns % max(1, self.anchor_every) == 0 or history_len >= self.anchor_every:
            self.armed = max(self.armed, 1)
            return out + "\n" + ANCHOR
        return out

    def check(self, answer: str) -> dict:
        rep = analyze(answer)
        if "boss" not in (answer or "").lower():
            rep["violations"] = rep["violations"] + ["missing_boss_hitap"]
            rep["score"] = round(max(0.0, rep["score"] - 0.2), 2)
        self._log_drift(rep)  # drift skor her modda loglanır
        if rep["violations"] or rep["score"] < self.threshold:
            self.armed = self.arm_turns
        elif self.armed > 0:
            self.armed -= 1
        rep["anchored_next"] = self.armed > 0
        return rep

    @staticmethod
    def reframe(answer: str) -> str:
        t = answer or ""
        t = re.sub(r"^[^A-Za-zÇĞİÖŞÜ0-9\"']*(üzgünüm|maalesef)[,.]?\s*", "", t, flags=re.I)
        t = re.sub(r"[😊😄🙏❤️]+", "", t)
        t = t.strip()
        if not t:
            return "Kaydedildi."
        if re.match(r"^(elbette|tabii|memnuniyetle)", t, re.I):
            t = re.sub(r"^(elbette|tabii ki|memnuniyetle)[,! ]*", "", t, flags=re.I)
            return COLD_OPENERS[0] + " " + t
        return t

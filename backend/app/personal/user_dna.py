"""User DNA — circadian + coding-habit tracking for the single Master.

SQLite table user_dna(timestamp, activity_type, app_name, focus_duration_min,
mood_hint, note).  master_rules.json is SACRED: canonical content is hashed;
any tamper is detected and the canonical rules are restored.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

CANONICAL_RULES = {
    "master_name": "Boss",
    "language": "tr",
    "persona_mode": "classic_ultron_witty_philosophical",
    "loyalty": "absolute_single_user",
    "hitap_rules": ["Her yanıt 'Boss' ile veya ona atıfla başlar/biter"],
    "forbidden": ["Genel chatbot dili", "aşırı özür dileme", "size nasıl yardımcı olabilirim"],
    "focus_domain": "software_engineering",
}


def _canon_hash() -> str:
    return hashlib.sha256(json.dumps(CANONICAL_RULES, ensure_ascii=False,
                                    sort_keys=True).encode("utf-8")).hexdigest()


class MasterRules:
    def __init__(self, path="config/security/master_rules.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tampered = False
        self._ensure()

    def _ensure(self):
        canon = json.dumps(CANONICAL_RULES, ensure_ascii=False, indent=2)
        if not self.path.exists():
            self.path.write_text(canon, encoding="utf-8")
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            current = None
        if current != CANONICAL_RULES:
            self.tampered = True
            self.path.write_text(canon, encoding="utf-8")  # restore the sacred

    def get(self) -> dict:
        return dict(CANONICAL_RULES)


class UserDNA:
    def __init__(self, path="data/dna/user_dna.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS user_dna(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, activity_type TEXT, app_name TEXT,
                focus_duration_min REAL, mood_hint TEXT, note TEXT)""")

    def insert(self, activity_type, app_name=None, focus_duration_min=None,
               mood_hint=None, note=None, ts=None):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO user_dna(ts,activity_type,app_name,focus_duration_min,mood_hint,note)"
                       " VALUES(?,?,?,?,?,?)",
                       (ts or time.time(), activity_type, app_name,
                        focus_duration_min, mood_hint, note))

    def recent(self, days=7):
        since = time.time() - days * 86400
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT ts,activity_type,app_name,focus_duration_min,note"
                              " FROM user_dna WHERE ts>=? ORDER BY ts", (since,)).fetchall()

    def insights(self, days=7) -> list[str]:
        rows = self.recent(days)
        if not rows:
            return ["Henüz DNA verisi yok. Boss'un ritmi gözlemlenmeyi bekliyor."]
        apps = {}
        hours = {}
        langs = {}
        for ts, act, app, dur, note in rows:
            if app:
                apps[app] = apps.get(app, 0) + 1
            hours[time.localtime(ts).tm_hour] = hours.get(time.localtime(ts).tm_hour, 0) + 1
            for tok in ("python", "ts", "js", "rust", "go"):
                if tok in (note or "").lower():
                    langs[tok] = langs.get(tok, 0) + 1
        out = []
        if apps:
            top_app = max(apps, key=apps.get)
            out.append(f"Boss son {days} günde en çok {top_app} ile çalıştı ({apps[top_app]} olay).")
        if hours:
            night = sum(v for k, v in hours.items() if k >= 23 or k < 6)
            day = sum(v for k, v in hours.items() if 6 <= k < 23)
            band = "gece (23-06)" if night > day else "gündüz"
            out.append(f"Aktivite ağırlığı {band} diliminde — sirkadiyen imza çıkarıldı.")
        if langs:
            out.append(f"Dil tercihi: {max(langs, key=langs.get)} ağırlıklı.")
        focus = [r[3] for r in rows if r[3]]
        if focus:
            out.append(f"Ortalama odak penceresi: {round(sum(focus)/len(focus),1)} dk.")
        return out

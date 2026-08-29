"""Predictive Intelligence — Wave 5 §5 (+§10 planner entegrasyonu).

ULTRON yalnızca tepki vermez — ölçülmüş geçmişten tahmin üretir:
- failure prediction: görev/geçmiş hata oranından
- task duration estimation: median + MAD (sağlam istatistik; aykırıya dayanıklı)
- resource prediction: CPU/RAM eğilimi (lineer üstünkümü — dürüst R² ile)
- deadline risk: kalan iş / kalan zaman
- dependency risk: bağımlılık zincirinde hata olasılığı çarpımı
- anomaly prediction: son ölçümlerin robust z-skoru
- maintenance prediction: periyodik bakım aralığı modeli
- user-intent prediction: son intent geçiş matrisi (Markov-1)

DÜRÜSTLÜK KURALI: tahmin ile gerçek sonuç ASLA karıştırılmaz — her tahmin
prediction kaydına yazılır; outcome ile kapanır (calibration izlenebilir).
Yetersiz veri → confidence düşük + 'insufficient-data' reason; uydurma
medyan YOK.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections import Counter, deque
from pathlib import Path


def _now() -> float:
    return time.time()


def median(xs: list[float]) -> float:
    if not xs:
        raise ValueError("median of empty")
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def mad(xs: list[float]) -> float:
    m = median(xs)
    return median([abs(x - m) for x in xs]) if xs else 0.0


class PredictiveEngine:
    def __init__(self, db_path: str = "data/cognitive/predictions.db",
                 min_samples: int = 3):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS predictions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
            subject TEXT, predicted REAL, actual REAL, confidence REAL,
            reason TEXT, meta TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS task_stats(
            task_type TEXT PRIMARY KEY, samples INTEGER,
            durations_json TEXT, failures INTEGER, runs INTEGER)""")
        self.db.commit()
        self.min_samples = int(min_samples)
        self._recent_metrics: deque = deque(maxlen=64)
        self._intent_history: deque = deque(maxlen=64)

    # ---------------------------------------------------- kayıt (gerçek)
    def record_task(self, task_type: str, duration_s: float,
                    failed: bool = False) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT samples,durations_json,failures,runs FROM task_stats"
                " WHERE task_type=?", (task_type,)).fetchone()
            if row is None:
                samples, durs, fails, runs = 0, [], 0, 0
            else:
                samples, durs, fails, runs = row[0], json.loads(row[1]), row[2], row[3]
            durs = (durs + [round(float(duration_s), 3)])[-200:]
            self.db.execute(
                """INSERT INTO task_stats(task_type,samples,durations_json,
                   failures,runs) VALUES(?,?,?,?,?)
                   ON CONFLICT(task_type) DO UPDATE SET samples=excluded.samples,
                   durations_json=excluded.durations_json,
                   failures=excluded.failures, runs=excluded.runs""",
                (task_type, len(durs), json.dumps(durs),
                 fails + (1 if failed else 0), runs + 1))
            self.db.commit()
            return {"ok": True, "samples": len(durs)}

    def record_metric(self, value: float) -> None:
        with self.lock:
            self._recent_metrics.append(float(value))

    def record_intent(self, intent: str) -> None:
        with self.lock:
            self._intent_history.append(str(intent))

    # ---------------------------------------------------- tahminler
    def _pred(self, kind, subject, predicted, confidence, reason, meta):
        with self.lock:
            self.db.execute(
                """INSERT INTO predictions(ts,kind,subject,predicted,actual,
                   confidence,reason,meta) VALUES(?,?,?,?,NULL,?,?,?)""",
                (_now(), kind, str(subject)[:120],
                 None if predicted is None else round(float(predicted), 3),
                 round(float(confidence), 3), reason,
                 json.dumps(meta or {}, ensure_ascii=False)))
            self.db.commit()
            return self.db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def duration_estimate(self, task_type: str) -> dict:
        """Görev süresi tahmini: median + MAD bandı. Veri yetersizse dürüst."""
        with self.lock:
            row = self.db.execute(
                "SELECT samples,durations_json FROM task_stats WHERE task_type=?",
                (task_type,)).fetchone()
        durs = json.loads(row[1]) if row else []
        n = len(durs)
        if n < self.min_samples:
            return {"ok": False, "reason": "insufficient-data", "samples": n,
                    "confidence": 0.0}
        med = median(durs)
        m = mad(durs) or max(0.001, med * 0.1)
        conf = min(0.9, 0.4 + 0.1 * n) * (1.0 / (1.0 + m / max(med, 1e-6)))
        pid = self._pred("duration", task_type, med, conf,
                         f"median n={n}", {"mad": round(m, 3), "n": n})
        return {"ok": True, "prediction_id": pid, "estimate_s": round(med, 2),
                "band": [round(max(0.0, med - 2 * m), 2), round(med + 2 * m, 2)],
                "confidence": round(conf, 3), "samples": n}

    def failure_probability(self, task_type: str) -> dict:
        with self.lock:
            row = self.db.execute(
                "SELECT failures,runs FROM task_stats WHERE task_type=?",
                (task_type,)).fetchone()
        if not row or row[1] < self.min_samples:
            return {"ok": False, "reason": "insufficient-data",
                    "runs": row[1] if row else 0, "confidence": 0.0}
        fails, runs = row
        # Laplace düzeltmesi: 0 hata → 0 değil 1/(runs+2) (aşırı iyimserlik yok)
        p = (fails + 1) / (runs + 2)
        pid = self._pred("failure_prob", task_type, p, min(0.9, 0.5 + 0.05 * runs),
                         f"laplace {fails}/{runs}", {"fails": fails, "runs": runs})
        return {"ok": True, "prediction_id": pid,
                "probability": round(p, 3), "confidence":
                    round(min(0.9, 0.5 + 0.05 * runs), 3)}

    def dependency_risk(self, chain: list[dict]) -> dict:
        """Bağımlılık zinciri: her adımın p_fail biliniyorsa çarpım;
        bilinmiyorsa 'insufficient-data' adımı dürüstçe raporlanır."""
        prob = 1.0
        unknown = []
        for step in chain:
            fp = step.get("failure_probability")
            if fp is None:
                unknown.append(step.get("name", "?"))
            else:
                prob *= (1.0 - float(fp))
        if unknown:
            return {"ok": False, "reason": "insufficient-data",
                    "unknown_steps": unknown}
        succ = prob
        pid = self._pred("dependency_chain", "→".join(
            s.get("name", "?") for s in chain), succ, 0.6,
            "chain-product", {"steps": len(chain)})
        return {"ok": True, "prediction_id": pid,
                "success_probability": round(succ, 3),
                "failure_probability": round(1.0 - succ, 3)}

    def resource_trend(self, horizon_s: float = 60.0) -> dict:
        """Son ölçümlerden lineer eğilim (üstünkümü + dürüst R²)."""
        with self.lock:
            pts = list(self._recent_metrics)
        n = len(pts)
        if n < self.min_samples:
            return {"ok": False, "reason": "insufficient-data", "samples": n}
        xs = list(range(n))
        mx, my = sum(xs) / n, sum(pts) / n
        sxx = sum((x - mx) ** 2 for x in xs) or 1e-9
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, pts))
        slope = sxy / sxx
        ss_tot = sum((y - my) ** 2 for y in pts) or 1e-9
        ss_res = sum((y - (my + slope * (x - mx))) ** 2
                     for x, y in zip(xs, pts))
        r2 = max(0.0, 1.0 - ss_res / ss_tot)
        predicted = pts[-1] + slope * horizon_s
        pid = self._pred("resource", "trend", predicted, r2,
                         f"linear r2={r2:.2f}", {"slope_per_sample":
                                                 round(slope, 4)})
        return {"ok": True, "prediction_id": pid,
                "predicted": round(predicted, 2), "slope": round(slope, 4),
                "r2": round(r2, 3),
                "confidence": round(r2, 3)}   # R² düşükse güven düşük

    def anomaly_risk(self, value: float) -> dict:
        """Robust z-skoru (medyan+MAD): aykırı değer alarmı.
        Ölçek çökmesi (MAD=0) durumunda dürüst fallback: std → nötr 1.0."""
        with self.lock:
            pts = list(self._recent_metrics)
        if len(pts) < self.min_samples:
            return {"ok": False, "reason": "insufficient-data"}
        med = median(pts)
        m = mad(pts)
        scale = 1.4826 * m
        if scale < 1e-9:
            mu = sum(pts) / len(pts)
            scale = max(1e-9, math.sqrt(sum((x - mu) ** 2 for x in pts)
                                         / len(pts)))
        if scale < 1e-9:
            scale = 1.0   # tüm örnekler eşit: ölçek bilinmiyor (nötr)
        z = abs(value - med) / scale
        return {"ok": True, "value": value, "robust_z": round(z, 2),
                "anomaly": z > 3.5,
                "confidence": round(min(0.9, 0.5 + 0.02 * len(pts)), 3)}

    def maintenance_due(self, last_at: float, interval_s: float,
                        now: float | None = None) -> dict:
        now = _now() if now is None else now
        elapsed = now - last_at
        return {"ok": True, "due": elapsed >= interval_s,
                "elapsed_s": round(elapsed, 1),
                "next_in_s": round(max(0.0, interval_s - elapsed), 1)}

    def intent_prediction(self) -> dict:
        """Markov-1: son intent'ten en olası sıradaki (gerçek geçişler)."""
        with self.lock:
            seq = list(self._intent_history)
        if len(seq) < 4:
            return {"ok": False, "reason": "insufficient-data"}
        counts: Counter = Counter()
        for a, b in zip(seq, seq[1:]):
            if a == seq[-1]:
                counts[b] += 1
        total = sum(counts.values())
        if not total:
            return {"ok": False, "reason": "insufficient-data"}
        nxt, c = counts.most_common(1)[0]
        p = c / total
        return {"ok": True, "next_intent": nxt,
                "probability": round(p, 3),
                "confidence": round(min(0.9, p), 3), "evidence": total}

    # ---------------------------------------------------- kapanış
    def outcome(self, prediction_id: int, actual: float) -> dict:
        """Tahmin ↔ gerçek ayrı tutulur; kalibrasyon ölçülebilir olur."""
        with self.lock:
            cur = self.db.execute(
                "UPDATE predictions SET actual=? WHERE id=? AND actual IS NULL",
                (round(float(actual), 3), prediction_id))
            self.db.commit()
            if cur.rowcount == 0:
                return {"ok": False, "error": "unknown or closed prediction"}
            return {"ok": True}

    def calibration(self, kind: str | None = None) -> dict:
        """Kapanmış tahminlerin gerçek sapması (dürüst başarı ölçüsü)."""
        q = ("SELECT predicted, actual FROM predictions"
             " WHERE actual IS NOT NULL")
        args: list = []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        if not rows:
            return {"closed": 0}
        errs = [abs(p - a) for p, a in rows]
        return {"closed": len(rows),
                "mean_abs_error": round(sum(errs) / len(errs), 3),
                "max_abs_error": round(max(errs), 3)}

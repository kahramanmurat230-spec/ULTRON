#!/usr/bin/env python3
"""WAVE 1 benchmarks — journal latency, WAL recovery, runtime+observability
overhead, trace chain verification. Local, honest, reproducible:
    python scripts/bench_wave1.py [ --out results.json ]
Numbers are wall-clock on THIS machine; no cloud, no simulation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.tasks.engine import TaskEngine  # noqa: E402


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1)))))
    return round(vals[k], 3)


def stats(vals):
    return {"n": len(vals), "mean_ms": round(statistics.fmean(vals), 3),
            "p50_ms": pct(vals, 50), "p95_ms": pct(vals, 95),
            "p99_ms": pct(vals, 99), "max_ms": pct(vals, 100)}


def bench_journal(tmp, n=300):
    e = TaskEngine(db_path=os.path.join(tmp, "j.db"),
                   tracer=Tracer(path=os.path.join(tmp, "j.jsonl")))
    t = e.create("bench", steps=[{"label": "s", "worker": "w"}])
    step = {"index": 0, "label": "s", "worker": "w"}
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        e.journal(t["id"], "STEP_SUCCESS", step=step, input_hash="a" * 16,
                  output_summary=f"iter {i}", duration_ms=1.2,
                  risk={"worker": "w", "level": "LOW"})
        lat.append((time.perf_counter() - t0) * 1000)
    return {"journal_write_ms": stats(lat)}


def bench_recovery(tmp, steps=200):
    e = TaskEngine(db_path=os.path.join(tmp, "r.db"),
                   tracer=Tracer(path=os.path.join(tmp, "r.jsonl")))
    plan = [{"label": f"s{i}", "worker": "w"} for i in range(steps)]
    t = e.create("bench", steps=plan)
    task = e.get(t["id"])
    for i in range(steps):
        e.journal(t["id"], "STEP_SUCCESS", step=task["steps"][i],
                  input_hash="b" * 16, output_summary="ok", duration_ms=2.0)
    t0 = time.perf_counter()
    replay = e.wal_replay(t["id"])
    t1 = time.perf_counter()
    rec = e.reconcile_from_journal(t["id"])
    t2 = time.perf_counter()
    assert len(replay["successful_steps"]) == steps and rec["changed"]
    return {"wal_replay_ms": round((t1 - t0) * 1000, 3),
            "reconcile_ms": round((t2 - t1) * 1000, 3),
            "steps": steps}


def bench_overhead(tmp, runs=30):
    """Aynı görev koşusu: journal+trace AÇIK vs KAPALI (gerçek fark)."""

    async def runner(task, step, ctx):
        return {"ok": True, "output": "x"}

    def one(journal_on, tracer_on):
        eng = TaskEngine(db_path=os.path.join(tmp, f"o{int(journal_on)}.db"),
                         tracer=Tracer(path=os.path.join(
                             tmp, f"o{int(journal_on)}.jsonl")),
                         journal_enabled=journal_on) if tracer_on else \
            TaskEngine(db_path=os.path.join(tmp, f"o{int(journal_on)}.db"),
                       journal_enabled=journal_on,
                       tracer=Tracer(path=os.path.join(
                           tmp, f"o{int(journal_on)}.jsonl")))
        # tracer her iki modda da bağlı; kapanınca journal yok + span yok:
        eng2 = eng
        if not tracer_on:
            eng2._tracer = _NullTracer()
        t = eng2.create("bench", steps=[{"label": "a", "worker": "w"},
                                        {"label": "b", "worker": "w"}])
        t0 = time.perf_counter()
        out = asyncio.run(eng2.execute(t["id"], runner))
        dt = (time.perf_counter() - t0) * 1000
        assert out["status"] == "COMPLETED"
        return dt

    on = [one(True, True) for _ in range(runs)]
    off = [one(False, False) for _ in range(runs)]
    mean_on, mean_off = statistics.fmean(on), statistics.fmean(off)
    return {"with_observability_ms": stats(on),
            "without_observability_ms": stats(off),
            "overhead_ms": round(mean_on - mean_off, 3),
            "overhead_pct": round((mean_on - mean_off) / mean_off * 100, 2)
            if mean_off else None}


class _NullTracer:
    """Gerçek Tracer'ın arayüzü; span üretmez (aşırı yük ölçümü için)."""

    class _NullSpan:
        trace_id = "0" * 32
        span_id = "0" * 16

        def end(self, *a, **k):
            return {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    redact_fn = staticmethod(lambda t: t)

    def start_trace(self, *a, **k):
        return self._NullSpan()

    def start_span(self, *a, **k):
        return self._NullSpan()


def bench_trace_chain(tmp, n=2000):
    tr = Tracer(path=os.path.join(tmp, "chain.jsonl"))
    t0 = time.perf_counter()
    for i in range(n):
        tr.start_span(f"op{i}", kind="tool").end()
    export_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    v = tr.verify_chain()
    verify_ms = (time.perf_counter() - t1) * 1000
    assert v["ok"] and v["spans"] == n
    return {"span_export_ms_per_1k": round(export_ms / n * 1000, 3),
            "chain_verify_ms_per_1k": round(verify_ms / n * 1000, 3),
            "spans": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        res = {"ts": time.time(), "python": sys.version.split()[0]}
        res.update(bench_journal(tmp))
        res.update(bench_recovery(tmp))
        res.update(bench_overhead(tmp))
        res.update(bench_trace_chain(tmp))
    txt = json.dumps(res, indent=2, ensure_ascii=False)
    print(txt)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()

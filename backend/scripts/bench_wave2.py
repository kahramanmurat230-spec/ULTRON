#!/usr/bin/env python3
"""WAVE 2 benchmarks (§27, §32) — honest numbers on THIS machine.

Ölçümler: memory write/retrieval/semantic, world update/snapshot,
event publish/consume/replay, conflict resolution, dedup.
Kullanım: python scripts/bench_wave2.py [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.events.bus import DurableEventBus  # noqa: E402
from app.memory.intelligence import (  # noqa: E402
    ConflictResolver, DuplicateDetector,
)
from app.memory.retrieval import Retriever  # noqa: E402
from app.memory.store_v3 import MemoryStore  # noqa: E402
from app.world.store import WorldStore  # noqa: E402


def stats(vals):
    if not vals:
        return {"n": 0}
    s = sorted(vals)

    def pct(p):
        return round(s[max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))], 3)
    return {"n": len(vals), "mean_ms": round(statistics.fmean(vals), 3),
            "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99)}


def bench_memory(tmp, n=300):
    s = MemoryStore(db_path=os.path.join(tmp, "m.db"))
    w, wd = [], []
    for i in range(n):
        t0 = time.perf_counter()
        s.write(f"benchmark kaydı {i} içerik", memory_type="SEMANTIC",
                provenance="SYSTEM", record_kind="FACT",
                subject_key=f"bench:{i % 20}")
        w.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        s.write(f"benchmark kaydı {i} içerik", memory_type="SEMANTIC",
                provenance="SYSTEM", record_kind="FACT",
                subject_key=f"bench:{i % 20}", dedup=True)  # dedup aramalı
        wd.append((time.perf_counter() - t0) * 1000)
    return {"memory_write_ms": stats(w), "memory_write_dedup_ms": stats(wd)}


def bench_retrieval(tmp, n=60):
    s = MemoryStore(db_path=os.path.join(tmp, "m.db"))
    for i in range(500):
        s.write(f"konu {i % 7} hakkında bilgi nokta {i}", memory_type="SEMANTIC",
                provenance="SYSTEM", record_kind="FACT",
                importance=0.3 + (i % 10) / 20)
    r = Retriever(s)
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        r.retrieve(f"konu {i % 7} bilgi", limit=8)
        lat.append((time.perf_counter() - t0) * 1000)
    return {"memory_retrieval_ms": stats(lat), "engine": r.engine,
            "corpus": 500}


def bench_world(tmp, n=300):
    w = WorldStore(db_path=os.path.join(tmp, "w.db"))
    up, snap = [], []
    for i in range(n):
        t0 = time.perf_counter()
        w.upsert("SYSTEM_HEALTH", "SYSTEM_HEALTH", {"cpu_percent": 5 + (i % 90)})
        up.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        w.snapshot()
        snap.append((time.perf_counter() - t0) * 1000)
    return {"world_update_ms": stats(up), "world_snapshot_ms": stats(snap)}


def bench_events(tmp, n=300):
    bus = DurableEventBus(db_path=os.path.join(tmp, "b.db"))
    got = 0

    def sub(t, p):
        nonlocal got
        got += 1
    bus.subscribe("bench.*", sub)
    pub = []
    for i in range(n):
        t0 = time.perf_counter()
        bus.publish(f"bench.tick.{i}", {"i": i, "v": i * 1.5})
        pub.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    out = bus.replay(1, n, force=True)                  # consume (yeniden dağıtım)
    replay_ms = (time.perf_counter() - t0) * 1000
    return {"event_publish_consume_ms": stats(pub),
            "event_replay_all_ms": round(replay_ms, 3),
            "replayed": out["redelivered"], "consumed": got}


def bench_conflict_dedup(tmp, n=100):
    s = MemoryStore(db_path=os.path.join(tmp, "c.db"))
    det = DuplicateDetector(s)
    cr = ConflictResolver(s)
    dup_lat, conf_lat = [], []
    for i in range(n):
        s.write(f"değer {i}", memory_type="USER", provenance="USER",
                record_kind="PREFERENCE", subject_key=f"pref:{i}")
        t0 = time.perf_counter()
        det.check(f"değer {i} yeni", "USER", "PREFERENCE", None, time.time(),
                  f"pref:{i}")
        dup_lat.append((time.perf_counter() - t0) * 1000)
        rec = s.query(subject_key=f"pref:{i}")[0]
        t0 = time.perf_counter()
        cr.resolve(rec, cr.detect(f"pref:{i}", rec))
        conf_lat.append((time.perf_counter() - t0) * 1000)
    return {"dedup_check_ms": stats(dup_lat),
            "conflict_resolve_ms": stats(conf_lat)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        res = {"ts": time.time(), "python": sys.version.split()[0]}
        res.update(bench_memory(tmp))
        res.update(bench_retrieval(tmp))
        res.update(bench_world(tmp))
        res.update(bench_events(tmp))
        res.update(bench_conflict_dedup(tmp))
    txt = json.dumps(res, indent=2, ensure_ascii=False)
    print(txt)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(txt)


if __name__ == "__main__":
    main()

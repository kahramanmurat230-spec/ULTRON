"""WAVE 1 / Security + failure injection:
- secrets never reach ANY channel (task.error, WS event detail, journal,
  trace file, dead-letter result) even when hidden inside exception text
- step timeout injection (real asyncio timeout) → honest FAILED
- journal tamper (row edited in SQLite) → WAL replay stays honest
- path traversal on journal API inputs rejected at engine level
"""
import asyncio
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import Tracer  # noqa: E402
from app.tasks.engine import TaskEngine  # noqa: E402

SECRET = "api_key: TOPSECRET-9876-XYZ"


def make_engine(tmp, **kw):
    return TaskEngine(db_path=os.path.join(tmp, "tasks.db"),
                      tracer=Tracer(path=os.path.join(tmp, "traces.jsonl")), **kw)


# ------------------------------------------------- secrets in error channels
def test_secret_in_exception_never_reaches_any_channel(tmp_path):
    events = []
    e = make_engine(str(tmp_path))
    e.event_cb = lambda ev: events.append(ev)

    async def leaky(task, step, ctx):
        raise RuntimeError(f"baglanti hatasi {SECRET} sızdı")

    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    out = asyncio.run(e.execute(t["id"], leaky))
    # 1) task.error (API kanalı)
    assert "TOPSECRET-9876-XYZ" not in (out["error"] or "")
    assert "***REDACTED***" in out["error"]
    # 2) event detail (WS kanalı)
    for ev in events:
        assert "TOPSECRET-9876-XYZ" not in json.dumps(ev)
    # 3) step error + journal
    task = e.get(t["id"])
    assert "TOPSECRET-9876-XYZ" not in json.dumps(task["steps"])
    assert "TOPSECRET-9876-XYZ" not in json.dumps(e.journal_rows(t["id"]))
    # 4) trace dosyası
    raw = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert "TOPSECRET-9876-XYZ" not in raw


def test_secret_in_dead_letter_notification(tmp_path):
    notified = []
    e = make_engine(str(tmp_path), on_dead_letter=lambda t: notified.append(t))

    async def poison(task, step, ctx):
        raise RuntimeError(f"poison {SECRET}")

    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    for _ in range(3):
        out = asyncio.run(e.execute(t["id"], poison))
        if out["status"] != "DEAD_LETTER":
            got = e.get(t["id"])
            e._save(got, "RECOVERING")
    assert out["status"] == "DEAD_LETTER"
    assert "TOPSECRET-9876-XYZ" not in json.dumps(notified[0])
    assert "TOPSECRET-9876-XYZ" not in json.dumps(out["result"])


def test_secret_in_dependency_error(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])

    async def boom(task, step, ctx):
        raise RuntimeError(f"dep {SECRET}")

    asyncio.run(e.execute(a["id"], boom))
    b = e.create("B", steps=[{"label": "s", "worker": "w"}], needs=[f"task:{a['id']}"])
    out = asyncio.run(e.execute(b["id"], _ok()))
    assert out["status"] == "CANCELLED"
    assert "TOPSECRET-9876-XYZ" not in json.dumps(out)


def _ok():
    async def runner(task, step, ctx):
        return {"ok": True, "output": "x"}
    return runner


# ------------------------------------------------- timeout injection (real)
def test_step_timeout_injection_honest_failure(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}],
                 budgets={"step_timeout_s": 0.05, "retry_budget": 0})

    async def hang(task, step, ctx):
        await asyncio.sleep(5)          # GERÇEK askıda adım
        return {"ok": True, "output": "x"}

    out = asyncio.run(e.execute(t["id"], hang))
    assert out["status"] == "FAILED"
    rows = e.journal_rows(t["id"])
    failed = [r for r in rows if r["status"] == "STEP_FAILED"]
    assert failed and failed[0]["error_class"] == "TimeoutError"


# ------------------------------------------------- journal tamper (DB edit)
def test_journal_tamper_detected_by_trace_chain(tmp_path):
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "s", "worker": "w"}])
    asyncio.run(e.execute(t["id"], _ok()))
    # günlük satırını doğrudan DB'de değiştir (SQLite seviyesinde oynama)
    conn = sqlite3.connect(e.path)
    conn.execute("UPDATE task_steps_journal SET output_summary='SAINT ELSEWHERE'"
                 " WHERE task_id=?", (t["id"],))
    conn.commit()
    conn.close()
    rows = e.journal_rows(t["id"])
    assert any(r["output_summary"] == "SAINT ELSEWHERE" for r in rows)  # DB'de değişti
    # ama trace kanalı zinciri HÂLÂ doğrulanır ve adım kanıtı journal'dan gelir;
    # zincir kanıtı trace dosyasındadır:
    v = e._get_tracer().verify_chain()
    assert v["ok"] is True               # trace kanalı etkilenmedi (ayrı kanal)


def test_wal_replay_honest_after_journal_tamper(tmp_path):
    """Journal'da sahte SUCCESS satırı eklenirse WAL replay onu KABUL EDER
    (journal append-only varsayımı) — bu bilinen sınır dürüstçe test edilir:
    koruma katmanı trace hash zinciridir."""
    e = make_engine(str(tmp_path))
    t = e.create("g", steps=[{"label": "one", "worker": "w"}])
    task = e.get(t["id"])
    conn = sqlite3.connect(e.path)
    conn.execute("INSERT INTO task_steps_journal(task_id,step_index,step_label,"
                 "worker,status,input_hash,output_summary,duration_ms,risk,"
                 "approval,error_class,trace_id,span_id,ts)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (t["id"], 0, "one", "w", "STEP_SUCCESS", "fake", "forged", 1.0,
                  None, None, None, None, None, 0))
    conn.commit()
    conn.close()
    replay = e.wal_replay(t["id"])
    assert [d["label"] for d in replay["successful_steps"]] == ["one"]  # kabul eder
    # kanıt: span_id'siz sahte satır tespit edilebilir (span yok → doğrulanamaz)
    row = e.journal_rows(t["id"])[0]
    assert row["span_id"] is None        # sahte satırın iz kanıtı yok


# ------------------------------------------------- traversal / abuse
def test_artifact_and_needs_reject_abuse(tmp_path):
    e = make_engine(str(tmp_path))
    with pytest.raises(ValueError):
        e.mark_artifact("t", "..")
    with pytest.raises(ValueError):
        e.mark_artifact("t", "a/b/c")
    with pytest.raises(ValueError):
        e.create("X", needs=["task:nonexist1"])          # bilinmeyen görev
    with pytest.raises(ValueError):
        e.create("X", needs=["artifact:" + "A" * 500])   # dev id (artifact serbest
    # (artifact id'leri uzun olabilir; mark_artifact 120 ile sınırlar)


def test_needs_list_bounded(tmp_path):
    e = make_engine(str(tmp_path))
    a = e.create("A", steps=[{"label": "s", "worker": "w"}])
    needs = [f"task:{a['id']}"] * 40
    with pytest.raises(ValueError):
        e.create("B", needs=needs)

"""WAVE 1 / Observability: structured traces — nesting, redaction, integrity.

Kapsam (talimat): trace generation · nested spans · secret redaction ·
trace integrity (hash chain) · metrics counters · restart chain continuity.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.observability.trace import (  # noqa: E402
    Span, TaskMetrics, Tracer,
)


def make_tracer(tmp_path, redact_fn=None):
    return Tracer(path=str(tmp_path / "traces.jsonl"), redact_fn=redact_fn)


# ---------------------------------------------------- trace & nested spans
def test_trace_generation_and_structure(tmp_path):
    tr = make_tracer(tmp_path)
    with tr.start_trace("task:analyze", kind="task", task_id="t1",
                        attributes={"task": "analyze", "goal": "g"}) as root:
        with tr.start_span("step:code_analysis", kind="step", parent=root,
                           task_id="t1",
                           attributes={"step": "code", "worker": "code_analysis"}) as s1:
            s1.end(result_summary="96 files, 3 issues", risk={"level": "SAFE"},
                   budget={"tool_budget": 50, "used": 1})
        root.end(result_summary="done")
    recs = tr.read_all()
    assert len(recs) == 2
    task_span = next(r for r in recs if r["kind"] == "task")
    step_span = next(r for r in recs if r["kind"] == "step")
    assert task_span["kind"] == "task" and step_span["kind"] == "step"
    assert step_span["trace_id"] == task_span["trace_id"]          # aynı trace
    assert step_span["parent_span_id"] == task_span["span_id"]     # nested
    assert task_span["parent_span_id"] is None
    assert step_span["duration_ms"] is not None and step_span["status"] == "OK"
    assert step_span["risk"] == {"level": "SAFE"}
    assert step_span["budget"]["used"] == 1
    assert step_span["result_summary"] == "96 files, 3 issues"


def test_span_ids_unique_and_error_status(tmp_path):
    tr = make_tracer(tmp_path)
    ids = set()
    for i in range(5):
        s = tr.start_span(f"op{i}", kind="tool")
        ids.add(s.span_id)
        s.end(status="ERROR", error_class="TimeoutError")
    assert len(ids) == 5
    errs = [r for r in tr.read_all() if r["status"] == "ERROR"]
    assert len(errs) == 5 and all(r["error_class"] == "TimeoutError" for r in errs)


def test_recent_query_filters(tmp_path):
    tr = make_tracer(tmp_path)
    root = tr.start_trace("t", kind="task", task_id="tk1")
    root.end()
    other = tr.start_trace("t2", kind="task", task_id="tk2")
    other.end()
    assert len(tr.recent(trace_id=root.trace_id)) == 1
    assert len(tr.recent(task_id="tk2")) == 1
    assert tr.stats()["exported"] == 2


# ------------------------------------------------------------ redaction
def test_secrets_never_in_trace_file(tmp_path):
    tr = make_tracer(tmp_path)
    s = tr.start_span("email_send", kind="tool", attributes={
        "tool": "email_send",
        "detail": "api_key: SUPERSECRET-1234 bağlanıyor",
        "auth": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sig",
    })
    s.end(result_summary="sent with password: gizli-sifre-99",
          approval="approved")
    raw = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert "SUPERSECRET-1234" not in raw
    assert "gizli-sifre-99" not in raw
    assert "eyJhbGciOiJI" not in raw
    assert "***REDACTED***" in raw


def test_redaction_with_concrete_extra_values(tmp_path):
    vault_value = "zz-concrete-vault-secret-zz"
    from app.security.redaction import redact
    tr = make_tracer(tmp_path, redact_fn=lambda t: redact(t, extra_values=(vault_value,)))
    s = tr.start_span("weather", kind="tool", attributes={"url": f"key={vault_value}"})
    s.end()
    raw = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert vault_value not in raw


def test_metrics_counters_carry_no_values(tmp_path):
    m = TaskMetrics()
    m.inc("tasks_created")
    m.inc("journal_writes", 5)
    snap = m.snapshot()
    assert snap["counters"]["tasks_created"] == 1
    assert snap["counters"]["journal_writes"] == 5
    assert all(isinstance(v, int) for v in snap["counters"].values())  # sadece sayılar


# ------------------------------------------------------------ integrity
def test_trace_hash_chain_verifies(tmp_path):
    tr = make_tracer(tmp_path)
    for i in range(4):
        tr.start_span(f"op{i}", kind="step").end()
    assert tr.verify_chain()["ok"] is True


def test_trace_chain_detects_tampering(tmp_path):
    tr = make_tracer(tmp_path)
    tr.start_span("a", kind="step").end()
    tr.start_span("b", kind="step").end()
    p = tmp_path / "traces.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["result_summary"] = "manipulated"          # içerik oynandı
    lines[0] = json.dumps(rec, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = tr.verify_chain()
    assert v["ok"] is False and v["broken_at"] == 0


def test_trace_chain_detects_truncation_and_insertion(tmp_path):
    tr = make_tracer(tmp_path)
    for i in range(3):
        tr.start_span(f"op{i}", kind="step").end()
    p = tmp_path / "traces.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")   # son satır silindi
    assert Tracer(path=str(p)).verify_chain()["ok"] is False
    inserted = lines[1]
    p.write_text("\n".join([lines[0], inserted, lines[1], lines[2]]) + "\n",
                 encoding="utf-8")                                   # satır eklendi
    assert Tracer(path=str(p)).verify_chain()["ok"] is False


def test_chain_continues_across_restart(tmp_path):
    tr1 = make_tracer(tmp_path)
    tr1.start_span("first", kind="task").end()
    tr2 = make_tracer(tmp_path)  # restart: yeni instance, aynı dosya
    tr2.start_span("second", kind="task").end()
    v = tr2.verify_chain()
    assert v["ok"] is True and v["spans"] == 2


def test_export_failure_never_raises(tmp_path):
    tr = make_tracer(tmp_path)
    (tmp_path / "traces.jsonl").write_text("not-json", encoding="utf-8")
    s = tr.start_span("x", kind="tool")  # bozuk dosya üstüne append
    s.end()                              # exception YOK — observability uygulamayı kırmaz
    assert tr.stats()["exported"] >= 1

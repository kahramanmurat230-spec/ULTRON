"""WAVE 3 / Artifacts + result pipeline: immutable artifacts, erişim
kontrolü, iyimser çakışma, bütünlük; sonuç doğrulama, çelişki (UNCERTAIN
dahil), reviewer, judge (self-approval yasağı, RETRY/REJECT/ACCEPT),
merge."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.orchestr.artifacts import ArtifactError, ArtifactManager  # noqa: E402
from app.orchestr.results import (  # noqa: E402
    ResultValidationError, detect_conflicts, judge, make_result, merge,
    resolve_conflict, review, validate_result,
)
from app.security.redaction import redact  # noqa: E402


def am(tmp):
    return ArtifactManager(db_path=os.path.join(tmp, "a.db"),
                           redact_fn=redact)


# ============================================================ artifacts
def test_artifact_schema_and_immutability(tmp_path):
    m = am(str(tmp_path))
    a = m.create("task-1", "w1", "report", "içerik burada")
    for k in ("artifact_id", "owner_task", "producer_worker", "type", "hash",
              "size", "created_at", "status"):
        assert k in a
    assert a["size"] == len("içerik burada".encode()) and a["status"] == "FINAL"
    # immutable: aynı id ile yeniden yazım RED
    with pytest.raises(ArtifactError, match="conflict"):
        m.create("task-1", "w2", "report", "değiştirilmiş", artifact_id=a["artifact_id"])
    assert m.verify_integrity(a["artifact_id"])["ok"] is True


def test_artifact_access_control(tmp_path):
    m = am(str(tmp_path))
    a = m.create("task-1", "w1", "data", "x")
    got = m.get(a["artifact_id"], requester_worker="w2",
                requester_task="task-1")       # aynı görev → OK
    assert got["content"] == "x"
    with pytest.raises(ArtifactError, match="unauthorized"):
        m.get(a["artifact_id"], requester_worker="w9",
              requester_task="task-2")         # başka görev → RED


def test_artifact_secret_redaction_and_limits(tmp_path):
    m = am(str(tmp_path))
    a = m.create("task-1", "w1", "note", "şifre: cok-gizli-9")
    got = m.get(a["artifact_id"], requester_worker="w1",
                requester_task="task-1")
    assert "cok-gizli-9" not in got["content"]     # secret artifact'a gömülmez
    with pytest.raises(ArtifactError):
        m.create("t", "w", "bilinmeyen-tip", "x")
    with pytest.raises(ArtifactError):
        m.create("t", "w", "data", "A" * (3 * 1024 * 1024))


def test_artifact_concurrent_create_single_winner(tmp_path):
    """Lost update YOK: aynı id'ye eşzamanlı yazım — yalnız biri kazanır."""
    m = am(str(tmp_path))
    errors = []

    def writer(i):
        try:
            m.create("t", f"w{i}", "data", f"içerik {i}",
                     artifact_id="sabit-id")
        except ArtifactError:
            errors.append(i)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(errors) == 7                          # 7 çakışma RED
    assert len(m.for_task("t")) == 1


# ============================================================ validation
def _res(**kw):
    base = dict(worker_id="w1", role="RESEARCH", payload={"cpu": 20},
                confidence=0.9, task_id="t1", evidence=["ölçüm-1"],
                subject="cpu_temp")
    base.update(kw)
    return make_result(**base)


def test_result_schema(tmp_path):
    r = _res()
    for k in ("source-worker", ):                     # yer tutucu
        pass
    assert r["worker_id"] == "w1" and 0 <= r["confidence"] <= 1
    assert r["evidence"] == ["ölçüm-1"] and r["status"] == "OK"


def test_validate_result_schema_enforced(tmp_path):
    r = _res()
    assert validate_result(r)["ok"] is True
    broken = dict(r)
    broken.pop("confidence")
    with pytest.raises(ResultValidationError):
        validate_result(broken)
    bad = dict(r)
    bad["confidence"] = 1.7
    with pytest.raises(ResultValidationError):
        validate_result(bad)


def test_validate_result_artifact_integrity(tmp_path):
    m = am(str(tmp_path))
    a = m.create("t1", "w1", "evidence", "kanıt metni")
    r = _res(artifact_refs=[a["artifact_id"]])
    assert validate_result(r, artifacts=m)["ok"] is True
    # artifact'ı DB'de boz → hash tutmaz → dürüst RED
    import sqlite3
    db = sqlite3.connect(m.path)
    db.execute("UPDATE artifacts SET content='bozulmuş' WHERE artifact_id=?",
               (a["artifact_id"],))
    db.commit()
    db.close()
    with pytest.raises(ResultValidationError, match="integrity"):
        validate_result(r, artifacts=m)


# ============================================================ conflicts
def test_conflict_detected_and_resolved_by_evidence(tmp_path):
    a = _res(worker_id="wa", payload={"cpu": 55}, confidence=0.9,
             evidence=["s1"])
    b = _res(worker_id="wb", payload={"cpu": 72}, confidence=0.9,
             evidence=["s1", "s2", "s3"])          # daha çok kanıt
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1 and conflicts[0]["subject"] == "cpu_temp"
    res = resolve_conflict(conflicts[0])
    assert res["resolution"] == "winner" and res["winner"] == "wb"


def test_conflict_tie_is_uncertain_not_guess(tmp_path):
    a = _res(worker_id="wa", payload={"cpu": 55}, confidence=0.9,
             evidence=["s1"])
    b = _res(worker_id="wb", payload={"cpu": 72}, confidence=0.9,
             evidence=["s1"])
    res = resolve_conflict(detect_conflicts([a, b])[0])
    assert res["resolution"] == "UNCERTAIN"          # TAHMİN YOK


def test_conflict_independent_verification_wins(tmp_path):
    a = _res(worker_id="wa", payload={"cpu": 55}, confidence=0.9,
             evidence=["s1", "s2"])
    b = _res(worker_id="wb", payload={"cpu": 72}, confidence=0.9,
             evidence=["s1", "s2"])
    # bağımsız doğrulayıcı yalnız b'yi onaylıyor
    res = resolve_conflict(detect_conflicts([a, b])[0],
                           verifier=lambda r: r["worker_id"] == "wb")
    assert res["winner"] == "wb"
    assert res["method"] == "independent_verification"


def test_different_times_not_conflict(tmp_path):
    a = _res(worker_id="wa", payload={"cpu": 55}, subject=None)
    b = _res(worker_id="wb", payload={"cpu": 72}, subject=None)
    assert detect_conflicts([a, b]) == []            # subject yok → çelişki yok


# ============================================================ reviewer/judge
def test_reviewer_flags_missing_evidence(tmp_path):
    r = _res(evidence=[])                            # kanıtsız
    note = review([r], reviewer_worker_id="rev-1")
    assert note["all_clean"] is False
    assert note["findings"][0]["issues"] == ["no evidence attached"]


def test_reviewer_flags_inflated_inference_confidence(tmp_path):
    r = _res(provenance="MODEL_INFERENCE", confidence=0.95)
    note = review([r], reviewer_worker_id="rev-1")
    assert any("inference" in i for f in note["findings"]
               for i in f["issues"])


def test_judge_accept_clean(tmp_path):
    r = _res()
    note = review([r], reviewer_worker_id="rev-1")
    v = judge([r], note, judge_worker_id="j-1")
    assert v["verdict"] == "ACCEPT"


def test_judge_retry_when_no_evidence(tmp_path):
    r = _res(evidence=[])
    note = review([r], reviewer_worker_id="rev-1")
    v = judge([r], note, judge_worker_id="j-1")
    assert v["verdict"] == "RETRY" and "evidence" in v["reason"]


def test_judge_reject_on_findings(tmp_path):
    r = _res(provenance="MODEL_INFERENCE", confidence=0.99, evidence=["e1"])
    note = review([r], reviewer_worker_id="rev-1")
    v = judge([r], note, judge_worker_id="j-1")
    assert v["verdict"] == "REJECT"


def test_judge_cannot_approve_own_result(tmp_path):
    r = _res(worker_id="j-1")                        # judge üretti!
    note = review([r], reviewer_worker_id="rev-1")
    v = judge([r], note, judge_worker_id="j-1")
    assert v["verdict"] == "ESCALATE"
    assert "self-approval" in v["reason"]


def test_judge_escalates_uncertain_conflict(tmp_path):
    a = _res(worker_id="wa", payload={"cpu": 55}, evidence=["s"])
    b = _res(worker_id="wb", payload={"cpu": 72}, evidence=["s"])
    conf = detect_conflicts([a, b])
    resolution = resolve_conflict(conf[0])
    note = review([a, b], reviewer_worker_id="rev-1")
    v = judge([a, b], note, judge_worker_id="j-1",
              conflicts_resolved=[resolution])
    assert v["verdict"] == "ESCALATE" and "UNCERTAIN" not in v["reason"]


def test_merge_outputs_producers_and_evidence(tmp_path):
    a = _res(worker_id="wa", payload={"x": 1}, evidence=["e1", "e2"])
    b = _res(worker_id="wb", role="VISION", payload={"y": 2}, subject=None,
             evidence=["e3"])
    m = merge([a, b])
    assert m["merged"] == {"wa": {"x": 1}, "wb": {"y": 2}}
    assert m["total_evidence"] == 3
    assert set(m["producers"]) == {"wa", "wb"}

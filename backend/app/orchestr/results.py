"""WAVE 3 — Result pipeline: validation → provenance/confidence →
conflict detection → reviewer → judge → merge.

Worker sonucu DOĞRUDAN final answer OLAMAZ. Her sonuç: source, confidence,
status, artifact_refs, evidence, trace_id taşır. Reviewer bağımsız inceler;
Judge ACCEPT/REJECT/RETRY/REPLAN/ESCALATE kararır. Worker kendi sonucunu
kendisi final onaylayamaz. Çelişki TAHMİNLE çözülmez: kanıt > provenance >
güven > bağımsız doğrulama; çözülmezse UNCERTAIN.
"""
from __future__ import annotations

import time

from app.orchestr.worker import WORKER_ROLES

VERDICTS = ("ACCEPT", "REJECT", "RETRY", "REPLAN", "ESCALATE")


def make_result(worker_id: str, role: str, *, payload, confidence: float,
                task_id: str, trace_id=None, artifact_refs=(),
                evidence=(), status="OK", subject=None, provenance="TOOL",
                now=None) -> dict:
    """Standart sonuç kaydı (worker çıktısı bu şemaya oturur)."""
    if role not in WORKER_ROLES:
        raise ValueError(f"unknown role {role!r}")
    return {"result_id": f"res-{worker_id}-{int((now or time.time()) * 1000)}",
            "worker_id": worker_id, "role": role, "task_id": task_id,
            "trace_id": trace_id, "payload": payload,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "status": status, "artifact_refs": list(artifact_refs or []),
            "evidence": list(evidence or []), "subject": subject,
            "provenance": provenance, "ts": now or time.time()}


class ResultValidationError(Exception):
    pass


def validate_result(result: dict, artifacts=None) -> dict:
    """Şema + artifact bütünlük doğrulaması (sahte başarı YOK)."""
    for k in ("result_id", "worker_id", "task_id", "payload", "confidence",
              "status", "evidence"):
        if k not in result:
            raise ResultValidationError(f"missing field {k!r}")
    if not 0.0 <= float(result["confidence"]) <= 1.0:
        raise ResultValidationError("confidence out of range")
    if artifacts is not None:
        for ref in result.get("artifact_refs") or []:
            v = artifacts.verify_integrity(ref)
            if not v.get("ok"):
                detail = v.get("error") or "hash mismatch"
                raise ResultValidationError(
                    f"artifact integrity failed: {ref} ({detail})")
    return {"ok": True, "result_id": result["result_id"]}


# ------------------------------------------------------------ conflicts
def detect_conflicts(results: list[dict]) -> list[dict]:
    """Aynı subject + farklı payload → çelişki adayları."""
    by_subject: dict[str, list] = {}
    for r in results:
        if r.get("subject") and r.get("status") == "OK":
            by_subject.setdefault(r["subject"], []).append(r)
    conflicts = []
    for subject, rs in by_subject.items():
        payloads = {repr(r.get("payload")) for r in rs}
        if len(payloads) > 1:
            conflicts.append({"subject": subject, "results": rs})
    return conflicts


def resolve_conflict(conflict: dict, verifier=None) -> dict:
    """TAHMİN YOK: kanıt sayısı > provenance güveni > confidence;
    beraberlikte bağımsız doğrulama (verifier) yoksa UNCERTAIN."""
    rs = conflict["results"]
    if verifier is not None:
        verified = [r for r in rs if verifier(r)]
        if len(verified) == 1:
            return {"resolution": "winner", "winner": verified[0]["worker_id"],
                    "method": "independent_verification"}
        if len(verified) > 1:
            rs = verified                      # doğrulananlar arasına in
    prov_rank = {"TOOL": 3, "SYSTEM": 3, "DOCUMENT": 2, "BROWSER": 2,
                 "VISION": 1, "MODEL_INFERENCE": 0}
    def score(r):
        return (len(r.get("evidence") or []),
                prov_rank.get(r.get("provenance"), 1),
                float(r.get("confidence") or 0))
    ranked = sorted(rs, key=score, reverse=True)
    if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]):
        return {"resolution": "UNCERTAIN",
                "reason": "evidence/provenance/confidence tie — refusing "
                          "to guess", "subject": conflict["subject"]}
    return {"resolution": "winner", "winner": ranked[0]["worker_id"],
            "method": "evidence>provenance>confidence"}


# ------------------------------------------------------------ reviewer/judge
def review(results: list[dict], reviewer_worker_id: str) -> dict:
    """Bağımsız inceleme: kanıt kapsamı, artifact bağlantısı, güven."""
    findings = []
    for r in results:
        issues = []
        if not r.get("evidence"):
            issues.append("no evidence attached")
        if r.get("confidence", 0) < 0.3:
            issues.append("very low confidence")
        if r.get("provenance") == "MODEL_INFERENCE" \
                and r.get("confidence", 0) > 0.6:
            issues.append("inference claims high confidence")
        findings.append({"result_id": r["result_id"],
                         "worker_id": r["worker_id"], "issues": issues,
                         "clean": not issues})
    return {"reviewer": reviewer_worker_id, "findings": findings,
            "all_clean": all(f["clean"] for f in findings)}


def judge(results: list[dict], review_note: dict, judge_worker_id: str,
          conflicts_resolved: list[dict] | None = None) -> dict:
    """Nihai karar. KURAL: judge, kendi ürettiği sonucu onaylayamaz."""
    for r in results:
        if r["worker_id"] == judge_worker_id:
            return {"verdict": "ESCALATE",
                    "reason": f"judge {judge_worker_id} produced a result "
                              "under judgment — self-approval forbidden",
                    "judge": judge_worker_id}
    unresolved = [c for c in (conflicts_resolved or [])
                  if c.get("resolution") == "UNCERTAIN"]
    if unresolved:
        return {"verdict": "ESCALATE",
                "reason": f"unresolved conflicts: "
                          f"{[c.get('subject') for c in unresolved]}",
                "judge": judge_worker_id}
    dirty = [f for f in review_note.get("findings", [])
             if not f.get("clean")]
    if dirty and any("no evidence" in " ".join(f["issues"]) for f in dirty):
        return {"verdict": "RETRY",
                "reason": "results lack evidence — retry with evidence "
                          "collection",
                "judge": judge_worker_id}
    if dirty:
        return {"verdict": "REJECT",
                "reason": f"review findings: {[f['issues'] for f in dirty]}",
                "judge": judge_worker_id}
    return {"verdict": "ACCEPT", "reason": "clean review, no conflicts",
            "judge": judge_worker_id}


def merge(results: list[dict]) -> dict:
    """Kabul edilen sonuçların birleşimi (payload sözlüğü + kanıtlar)."""
    return {"merged": {r["worker_id"]: r.get("payload") for r in results},
            "confidences": {r["worker_id"]: r.get("confidence")
                            for r in results},
            "total_evidence": sum(len(r.get("evidence") or [])
                                  for r in results),
            "producers": [r["worker_id"] for r in results]}

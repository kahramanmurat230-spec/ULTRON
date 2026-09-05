"""WAVE 3 — Supervisor Orchestrator: nihai orchestration otoritesi.

goal → DAG → paralel worker'lar → sonuç toplama → cross-validation →
reviewer → judge → merge → doğrulama → RESULT.

- Approval: HIGH/CRITICAL capability → authority.approve (yalnız burada);
  worker ctx.grant(cap) token doğrular — bypass imkânsız.
- Observability: Wave 1 Tracer ile task→worker→judge span zinciri;
  redaction aynen.
- Memory policy: worker sonuçları OTOMATİK yazılmaz — classification →
  provenance → confidence → importance → privacy kararı sonra.
- Recovery: supervisor restart → SUCCEEDED worker'lar yeniden koşmaz;
  takılanlar SINIRLI retry, sonra poison.
- Dünya güncellemeleri Event Bus üzerinden (Wave 2 DurableEventBus).
"""
from __future__ import annotations

import asyncio

from app.security.redaction import redact as _redact

from app.orchestr.artifacts import ArtifactManager
from app.orchestr.budgets import BudgetPool
from app.orchestr.messages import AgentMessageBus
from app.orchestr.results import (
    detect_conflicts, judge, make_result, merge, resolve_conflict, review,
    validate_result,
)
from app.orchestr.scheduler import DAGScheduler, SchedulerPolicy
from app.orchestr.tokens import CapabilityTokenAuthority, TokenError
from app.orchestr.worker import MAX_ATTEMPTS, Worker, WorkerRegistry


class ResultMemoryPolicy:
    """Worker sonucu → bellek kararı (varsayılan: YAZMA)."""

    def __init__(self, memory_store=None):
        self.memory = memory_store
        self.decisions = {"written": 0, "denied": 0}

    def decide(self, result: dict, *, approved: bool = False) -> dict:
        if self.memory is None:
            return {"write": False, "reason": "no memory store"}
        if not approved:
            self.decisions["denied"] += 1
            return {"write": False,
                    "reason": "memory writes require explicit policy "
                              "approval (never automatic)"}
        if result.get("provenance") == "MODEL_INFERENCE" \
                and float(result.get("confidence", 0)) < 0.5:
            self.decisions["denied"] += 1
            return {"write": False,
                    "reason": "low-confidence inference is not memorized"}
        importance = min(0.9, 0.3 + 0.1 * len(result.get("evidence") or []))
        res = self.memory.write(
            str(result.get("payload"))[:400],
            memory_type="PROJECT" if result.get("task_id") else "EPISODIC",
            provenance="TOOL", record_kind="OBSERVATION",
            source_id=f"worker:{result.get('worker_id')}",
            subject_key=f"task:{result.get('task_id')}",
            importance=importance,
            confidence=float(result.get("confidence", 0.5)) * 0.8,
            tags=["wave3", "worker_result"])
        if res.get("ok"):
            self.decisions["written"] += 1
        return {"write": bool(res.get("ok")), "id": res.get("id")}


class SupervisorOrchestrator:
    """Supervisor: DAG koşuturur, sonuç hattını işletir, karara bağlar."""

    def __init__(self, registry: WorkerRegistry, scheduler: DAGScheduler,
                 authority: CapabilityTokenAuthority,
                 artifacts: ArtifactManager, bus: AgentMessageBus,
                 tracer=None, memory_policy: ResultMemoryPolicy | None = None,
                 event_publisher=None, now=None):
        self.registry = registry
        self.scheduler = scheduler
        self.authority = authority
        self.artifacts = artifacts
        self.bus = bus
        self.tracer = tracer
        self.memory_policy = memory_policy or ResultMemoryPolicy()
        self.publish = event_publisher or (lambda *a, **k: None)
        self.now = now or (lambda: __import__("time").time())
        self._tokens: dict[str, list[str]] = {}    # worker_id → token list

    # ------------------------------------------------------------ council
    async def convene(self, topic: str, members: dict, *,
                      limits: "CouncilLimits | None" = None,
                      final_authority: str = "SUPERVISOR",
                      decision=None):
        """Çözülemeyen çelişkide konsey: müzakere + nihai otorite onayı.

        members: {isim: async fn(round, session) -> {kind, payload}}.
        Uzlaşma olsa bile final karar Supervisor/Judge imzası ister.
        """
        from app.orchestr.council import CouncilSession
        session = CouncilSession(topic, members, limits=limits,
                                 final_authority=final_authority,
                                 redact_fn=_redact,
                                 event_publisher=self.publish, now=self.now)
        out = await session.negotiate()
        if out["outcome"] == "CONSENSUS" and decision is not None:
            out["ratified"] = session.ratify(final_authority, decision)
            self.publish("council.resolved",
                         {"topic": topic, "outcome": out["outcome"],
                          "reason": out["reason"],
                          "consensus": out["consensus"]})
        else:
            out["ratified"] = None
            self.publish("council.resolved",
                         {"topic": topic, "outcome": out["outcome"],
                          "reason": out["reason"]})
        out["transcript"] = session.transcript
        out["violations"] = session.violations
        return out

    # ------------------------------------------------------------ plan
    def plan(self, task_id: str, spec: list[dict]) -> list[Worker]:
        """spec: [{role, depends_on?, budget?, priority?}] → Worker DAG."""
        workers = []
        by_name: dict[str, Worker] = {}
        for item in spec:
            w = Worker(task_id, item.get("role"),
                       depends_on=[by_name[d].worker_id
                                   for d in (item.get("depends_on") or [])
                                   if d in by_name],
                       budget=item.get("budget"),
                       priority=int(item.get("priority", 5)))
            by_name[item.get("name") or w.worker_id] = w
            workers.append(w)
        return workers

    def issue_worker_tokens(self, worker: Worker) -> None:
        for cap in worker.capabilities:
            self._tokens.setdefault(worker.worker_id, []).append(
                self.authority.issue(worker.worker_id, cap,
                                     task_id=worker.task_id))

    # ------------------------------------------------------------ execute
    async def execute(self, task_id: str, workers: list[Worker],
                      executors: dict, policy: SchedulerPolicy | None = None,
                      parent_budget: dict | None = None,
                      judge_worker_id: str = "judge-1",
                      reviewer_worker_id: str = "reviewer-1") -> dict:
        pool = BudgetPool(parent_budget) if parent_budget else None
        tracer = self._get_tracer()
        root = tracer.start_trace(f"supervisor:{task_id}", kind="task",
                                  task_id=task_id,
                                  attributes={"task": task_id,
                                              "workers": len(workers)})
        # executor sarmalayıcı: worker → fn(worker, ctx) — ctx güvenli kanallar
        def wrap(role, fn):
            async def wrapped(worker: Worker):
                ctx = self._ctx_for(worker)
                span = tracer.start_span(f"worker:{worker.role}:{worker.worker_id}",
                                         kind="step", parent=root,
                                         task_id=task_id,
                                         attributes={"role": worker.role,
                                                     "worker": worker.worker_id})
                t0 = self.now()
                try:
                    out = fn(worker, ctx)
                    if hasattr(out, "__await__"):
                        out = await out
                except Exception as exc:  # noqa: BLE001
                    span.end(status="ERROR", error_class=type(exc).__name__,
                             result_summary=str(exc)[:200])
                    raise
                res = out if isinstance(out, dict) else {"ok": True,
                                                          "output": out}
                span.end(result_summary=str(res.get("output"))[:200],
                         budget={"elapsed_ms": round((self.now() - t0) * 1000, 1)})
                return res
            return wrapped

        wrapped_exec = {role: wrap(role, fn) for role, fn in executors.items()}
        for w in workers:
            self.issue_worker_tokens(w)
            self.publish("task.worker.created", {"task_id": task_id,
                                                 "worker_id": w.worker_id,
                                                 "role": w.role})
        report = await self.scheduler.run(workers, wrapped_exec,
                                          policy=policy, task_id=task_id,
                                          budget_pool=pool)
        report["pipeline"] = self._pipeline(task_id, report, workers,
                                            judge_worker_id,
                                            reviewer_worker_id)
        root.end(status="OK" if report.get("ok") else "ERROR",
                 result_summary=str(report.get("pipeline", {}).get("verdict")),
                 budget={"workers": len(workers),
                         "succeeded": sum(1 for s in report["states"].values()
                                          if s == "SUCCEEDED")})
        return report

    def _ctx_for(self, worker: Worker) -> dict:
        """Worker'a açılan GÜVENLİ kanallar (capability token kapılı)."""
        authority = self.authority

        def grant(capability: str) -> dict:
            tokens = self._tokens.get(worker.worker_id) or []
            last_err = None
            for tok in tokens:
                try:
                    return authority.verify(tok, worker_id=worker.worker_id,
                                            capability=capability,
                                            task_id=worker.task_id)
                except TokenError as exc:
                    last_err = exc
            raise TokenError(str(last_err or "no token for capability"))

        def send(receiver: str, msg_type: str, payload: dict, **kw):
            return self.bus.send(worker.worker_id, receiver, msg_type,
                                 payload, task_id=worker.task_id,
                                 trace_id=worker.trace_id, **kw)

        def artifact(type_: str, content: str, **kw):
            return self.artifacts.create(worker.task_id, worker.worker_id,
                                         type_, content, **kw)

        return {"grant": grant, "send": send, "artifact": artifact,
                "worker": worker.to_dict()}

    # ------------------------------------------------------------ pipeline
    def _pipeline(self, task_id: str, report: dict, workers: list[Worker],
                  judge_worker_id: str, reviewer_worker_id: str) -> dict:
        results = []
        for w in workers:
            if report["states"].get(w.worker_id) != "SUCCEEDED":
                continue
            raw = report.get("results", {}).get(w.worker_id) or {}
            try:
                res = make_result(w.worker_id, w.role,
                                  payload=raw.get("output"),
                                  confidence=float(raw.get("confidence", 0.8)),
                                  task_id=task_id, trace_id=w.trace_id,
                                  artifact_refs=raw.get("artifact_refs") or [],
                                  evidence=raw.get("evidence") or [],
                                  subject=raw.get("subject"))
                validate_result(res, artifacts=self.artifacts
                                if res["artifact_refs"] else None)
                results.append(res)
            except Exception:  # noqa: BLE001 — doğrulama hatası sonucu düşürür
                continue
        conflicts = detect_conflicts(results)
        resolutions = [resolve_conflict(c) for c in conflicts]
        note = review(results, reviewer_worker_id=reviewer_worker_id)
        verdict = judge(results, note, judge_worker_id=judge_worker_id,
                        conflicts_resolved=resolutions)
        merged = merge(results) if verdict["verdict"] == "ACCEPT" else None
        return {"results": len(results), "conflicts": len(conflicts),
                "resolutions": resolutions, "review_all_clean": note["all_clean"],
                "verdict": verdict, "merged": merged}

    # ------------------------------------------------------------ authority
    def approve(self, worker_id: str, capability: str) -> dict:
        """İnsan onayı → HIGH/CRITICAL capability açılır (yalnız bu yol)."""
        res = self.authority.approve(worker_id, capability)
        self.publish("task.worker.approved",
                     {"worker_id": worker_id, "capability": capability})
        return res

    # ------------------------------------------------------------ recovery
    def recover(self, task_id: str) -> dict:
        """Supervisor restart: kayıtlardan kurtarma planı.
        SUCCEEDED → yeniden koşmaz; takılanlar sınırın altındaysa retry."""
        workers = self.registry.for_task(task_id)
        plan = {"keep": [], "retry": [], "poison": []}
        for w in workers:
            if w.state == "SUCCEEDED":
                plan["keep"].append(w.worker_id)
            elif w.state in ("RUNNING", "WAITING", "RETRYING", "FAILED"):
                # FAILED: restart'ta attempts hakkı varsa tekrar denenir
                # (DETECT→CLASSIFY→RETRY); sınır doluysa poison raporlanır
                if w.attempts < MAX_ATTEMPTS:
                    plan["retry"].append(w.worker_id)
                elif w.state != "FAILED":
                    w.transition("FAILED",
                                 reason="poison: attempts exhausted at "
                                        "recovery")
                    self.registry.save(w)
                    plan["poison"].append(w.worker_id)
                else:
                    plan["poison"].append(w.worker_id)
            elif w.state in ("READY", "BLOCKED", "PENDING"):
                plan["retry"].append(w.worker_id)
        return plan

    def _get_tracer(self):
        if self.tracer is None:
            from app.observability.trace import Tracer
            self.tracer = Tracer()
        return self.tracer

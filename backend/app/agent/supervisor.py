"""Supervisor Agent — plan, delegate, verify, replan, and locally evaluate reports."""
import asyncio
import subprocess
import sys
from pathlib import Path

from app.tasks.engine import BrainUnavailable, NeedsApproval


class Worker:
    id = "worker"; label = "worker"; alt = None
    def run(self, goal, args, ctx): raise NotImplementedError


class CodeAnalysisWorker(Worker):
    id = "code_analysis"; label = "Static code analysis"
    def __init__(self, code_intel): self.code_intel = code_intel
    def run(self, goal, args, ctx):
        target = args.get("target") or ("frontend" if "frontend" in goal.lower() else "backend" if "backend" in goal.lower() else "all")
        rep = self.code_intel.analyze(target); issues = rep.get("issues", [])
        return {"ok": rep.get("summary", {}).get("files", 0) > 0, "output": {
            "target": target, "files": rep.get("summary", {}).get("files", 0), "issues": len(issues),
            "high": sum(1 for i in issues if i.get("severity") in ("high", "error")),
            "duplicates": len(rep.get("duplicates", [])), "todo": len(rep.get("todo", [])),
            "top": [{"file": i["file"], "line": i.get("line"), "sev": i["severity"], "msg": i["message"][:120]} for i in issues[:5]]}}


class TestWorker(Worker):
    __test__ = False; id = "tests"; label = "Project test suite"
    def __init__(self, project_root: Path): self.root = Path(project_root)
    def run(self, goal, args, ctx):
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(self.root), capture_output=True, text=True, timeout=int(args.get("timeout", 240)))
            tail = (r.stdout or r.stderr or "").strip().splitlines()
            return {"ok": r.returncode == 0, "output": {"returncode": r.returncode, "summary": tail[-1] if tail else "", "tail": tail[-5:]}}
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": {"returncode": None, "summary": "timeout"}}


class DiagnosticWorker(Worker):
    id = "diagnostic"; label = "Self diagnostic"
    def __init__(self, runtime): self.runtime = runtime
    def run(self, goal, args, ctx):
        res = self.runtime.self_diagnostic()
        return {"ok": bool(res.get("ok")), "output": {"overall": res.get("overall"), "report": (res.get("report") or "")[:400], "components": {k: v.get("status") for k, v in res.get("components", {}).items() if isinstance(v, dict)}}}


class VerificationWorker(Worker):
    id = "verification"; label = "Output verification"
    def run(self, goal, args, ctx):
        outputs = ctx.get("outputs", {}); problems = []
        if outputs.get("code_analysis") is not None and (not isinstance(outputs["code_analysis"], dict) or outputs["code_analysis"].get("files", 0) < 1): problems.append("code_analysis output invalid")
        if outputs.get("tests") is not None and (not isinstance(outputs["tests"], dict) or "returncode" not in outputs["tests"]): problems.append("tests output invalid")
        if outputs.get("diagnostic") is not None and (not isinstance(outputs["diagnostic"], dict) or "overall" not in outputs["diagnostic"]): problems.append("diagnostic output invalid")
        if not outputs: problems.append("nothing to verify")
        return {"ok": not problems, "output": {"verified": sorted(outputs.keys()), "problems": problems}}


class PlannerWorker(Worker):
    """Step 0 of a general plan task: ask the (local) LLM planner for a
    validated, capability-aware plan and stage it as the task's remaining
    steps. Raises BrainUnavailable (→ engine WAITING_BRAIN, honest) when the
    local brain is down — never fabricates a plan."""
    id = "planner"; label = "General LLM planner"

    def __init__(self, planner):
        self.planner = planner

    def run(self, goal, args, ctx):
        try:
            plan = self.planner.make_plan(goal)
        except ValueError:
            # the brain ANSWERED but produced an invalid plan — that is a
            # step failure (replan/retry), not "brain unavailable"
            raise
        except BrainUnavailable:
            raise
        except Exception as exc:  # connection/transport failure
            # honest WAITING_BRAIN — never fabricate a plan
            raise BrainUnavailable(f"planner brain unavailable: {exc}") from exc
        steps = []
        for s in plan.get("steps", []):
            steps.append({
                "label": f"{s['tool']}",
                "worker": "tool_step",
                "args": {"tool": s["tool"], "arguments": dict(s.get("arguments") or {}),
                         "reason": str(s.get("reason", ""))[:300],
                         "depends_on": list(s.get("depends_on") or [])},
                "critical": True, "attempts": 0, "status": "PENDING",
                "result": None, "input_hash": None})
        # every tool step is independently verified; close with the shared
        # verification + report workers so the final report is evidence-based
        steps.append({"label": "verify outputs", "worker": "verification", "args": {},
                      "critical": True, "attempts": 0, "status": "PENDING",
                      "result": None, "input_hash": None})
        steps.append({"label": "final evaluated report", "worker": "report", "args": {},
                      "critical": False, "attempts": 0, "status": "PENDING",
                      "result": None, "input_hash": None})
        ctx["planned_steps"] = steps
        return {"ok": True, "output": {
            "planner": plan.get("planner"), "bounded": plan.get("bounded"),
            "goal": str(plan.get("goal", ""))[:200],
            "steps": [{"tool": s["tool"], "depends_on": s.get("depends_on", [])}
                      for s in plan.get("steps", [])]}}


class ToolStepWorker(Worker):
    """Execute ONE validated plan step through the production Executor with
    the full risk/approval gate, then verify the effect through an
    INDEPENDENT observation channel (never the tool's own claimed success).

    Output contract: {"executed", "succeeded", "verified", "result"} —
    verified=False fails the step (recovery/replan); verified=None means
    honestly unobservable and is reported, not faked."""
    id = "tool_step"; label = "Plan tool step"

    def __init__(self, executor, registry, verifier=None):
        self.executor = executor
        self.registry = registry
        self.verifier = verifier

    def _resolve_refs(self, arguments, ctx):
        from app.agent.hybrid_executor import HybridPlanExecutor
        results = ctx.get("step_results") or []
        try:
            return HybridPlanExecutor._resolve_args(arguments, results)
        except Exception:
            return arguments

    def run(self, goal, args, ctx):
        from app.security import risk as risk_mod
        tool = str(args.get("tool") or "")
        arguments = self._resolve_refs(dict(args.get("arguments") or {}), ctx)
        expect = arguments.pop("expect", None)  # verifier-only, never reaches tools
        item = self.registry.get(tool)
        if item is None or not callable(item.get("fn")):
            return {"ok": False, "output": {
                "tool": tool, "executed": False, "succeeded": False,
                "verified": {"mode": "none", "verified": None,
                             "detail": "tool not registered or not callable"},
                "error": f"Tool not registered: {tool}"}}
        dangerous = bool(item.get("dangerous"))
        decision = risk_mod.evaluate(tool, arguments, dangerous)
        if decision.get("blocked"):
            # HARD security block — approval can NEVER override this
            return {"ok": False, "output": {
                "tool": tool, "executed": False, "succeeded": False,
                "verified": {"mode": "policy", "verified": False,
                             "detail": decision["blocked"]},
                "error": decision["blocked"]}}
        task = ctx.get("task") or {}
        approved = bool(task.get("user_approved"))
        if decision.get("requires_approval") and not approved:
            raise NeedsApproval(
                f"Plan adımı onay gerektiriyor: {tool} (risk={decision['level']})",
                risks=[tool])
        try:
            result = self.executor.execute([(tool, arguments)], approved=approved)[0]
        finally:
            if approved and task is not None:
                # one-shot approval consumed by this execution attempt
                task["user_approved"] = False
                engine = ctx.get("engine")
                if engine is not None:
                    try:
                        engine._save(task)
                    except Exception:
                        pass
        verification = (self.verifier.verify(tool, arguments, result, expect=expect)
                        if self.verifier is not None
                        else {"mode": "none", "verified": None, "detail": "no verifier"})
        ok = verification.get("verified") is not False
        return {"ok": ok, "output": {
            "tool": tool, "executed": True, "succeeded": True,
            "verified": verification, "result": result,
            "risk": decision.get("level")}}


class ReportWorker(Worker):
    id = "report"; label = "Final evaluated report"
    def __init__(self, router=None, llm_available=None): self.router = router; self.llm_available = llm_available or (lambda: False)

    @staticmethod
    def _base_report(goal, outputs):
        lines = ["ULTRON FINAL REPORT", f"GOAL: {goal}"]
        ca = outputs.get("code_analysis")
        if ca:
            lines.append(f"CODE: {ca.get('files')} files · {ca.get('issues')} issues ({ca.get('high')} high) · {ca.get('duplicates')} dup · {ca.get('todo')} todo")
            lines += [f"  - {t.get('sev')}: {t.get('msg')} @ {t.get('file')}:{t.get('line')}" for t in ca.get("top", [])[:3]]
        tw = outputs.get("tests")
        if tw: lines.append(f"TESTS: rc={tw.get('returncode')} · {tw.get('summary')}")
        dg = outputs.get("diagnostic")
        if dg: lines.append(f"DIAGNOSTIC: {dg.get('overall')}")
        vf = outputs.get("verification")
        if vf: lines.append(f"VERIFICATION: {'PASS' if not vf.get('problems') else 'FAIL — ' + '; '.join(vf['problems'])}")
        lines.append("EVIDENCE: " + ("verified supervisor outputs are listed above." if vf and not vf.get("problems") else "verification did not fully pass; inspect the listed problems."))
        lines.append("NEXT: " + ("no mandatory corrective action from this run." if vf and not vf.get("problems") else "fix verification failures before treating the run as complete."))
        return "\n".join(lines)

    def run(self, goal, args, ctx):
        outputs = ctx.get("outputs", {})
        report = self._base_report(goal, outputs); ctx["final_report"] = report
        if self.router is not None and self.llm_available() and self.router.local_multi_enabled():
            try:
                chosen, results = self.router.race_local_and_judge(
                    messages=[
                        {"role": "system", "content": "Türkçe, kısa, net, kanıta dayalı ULTRON final raporu yaz. Boss diye hitap et. Gerçekleşmeyen işi yapılmış gösterme. GOAL, CODE, TESTS, DIAGNOSTIC, VERIFICATION, EVIDENCE ve NEXT bilgilerini koru."},
                        {"role": "user", "content": report[:6000]},
                    ],
                    system="Aday raporlarını doğruluk, açıklık, eksiksizlik ve kanıt kullanımı açısından değerlendir. En iyi raporu doğrudan döndür; yeni olgu uydurma.",
                    task="report",
                )
                if chosen and chosen.content: report = chosen.content.strip(); ctx["final_report"] = report
                ctx["local_evaluation"] = {"winner": chosen.model if chosen else None, "winner_score": chosen.score if chosen else None, "candidates": [r.to_dict() for r in results]}
            except Exception as exc:
                ctx["local_evaluation"] = {"winner": None, "winner_score": None, "error": str(exc)[:200], "candidates": []}
        return {"ok": True, "output": report}


class SupervisorAgent:
    # goals matching these keywords run the fixed audit pipeline (kept
    # byte-compatible with the previous behaviour); everything else goes to
    # the general LLM planner → persistent TaskEngine path when a planner
    # worker is registered.
    AUDIT_KEYWORDS = ("teşhis", "diagnostik", "diagnostic", "kendini kontrol",
                      "sağlık", "analiz", "analyze", "incele", "kod", "code",
                      "kalite", "bug", "test", "regresyon", "regression")

    def __init__(self, task_engine, workers, event_cb=None): self.engine = task_engine; self.workers = workers; self.event_cb = event_cb

    def plan(self, goal):
        g = goal.lower(); steps = []
        if any(k in g for k in ("teşhis", "diagnostik", "diagnostic", "kendini kontrol", "sağlık")): steps.append({"label":"self diagnostic","worker":"diagnostic","args":{}})
        if any(k in g for k in ("analiz", "analyze", "incele", "kod", "code", "kalite", "bug")): steps.append({"label":"static analysis","worker":"code_analysis","args":{}})
        if any(k in g for k in ("test", "regresyon", "regression")): steps.append({"label":"project tests","worker":"tests","args":{"timeout":300},"critical":False})
        if not steps: steps.append({"label":"static analysis","worker":"code_analysis","args":{}})
        steps += [{"label":"verify outputs","worker":"verification","args":{}}, {"label":"final evaluated report","worker":"report","args":{}}]
        return steps

    def is_audit_goal(self, goal: str) -> bool:
        g = (goal or "").lower()
        return any(k in g for k in self.AUDIT_KEYWORDS)

    async def submit(self, goal, budgets=None, spawn=True, force_pipeline=None):
        """Submit a goal to the persistent TaskEngine.

        Routing: audit-keyword goals keep the fixed 5-worker audit pipeline;
        any other goal becomes a kind="plan" task whose first step asks the
        general LLM planner for a validated plan (brain down → honest
        WAITING_BRAIN, never a fabricated plan). Without a registered
        planner worker the legacy audit pipeline is used (back-compat)."""
        pipeline = force_pipeline or (
            "audit" if (self.is_audit_goal(goal) or "planner" not in self.workers)
            else "plan")
        if pipeline == "plan":
            merged = {"replan_budget": 2}
            merged.update(budgets or {})
            task = self.engine.create(
                goal, kind="plan",
                steps=[{"label": "plan", "worker": "planner", "args": {}}],
                budgets=merged)
        else:
            task = self.engine.create(goal, kind="supervisor", steps=self.plan(goal), budgets=budgets)
        if spawn: self.engine.spawn(task["id"], self._runner)
        return task

    def _evaluate_worker_output(self, task, step, result, ctx):
        """Apply the local quality gate to a successful worker decision."""
        router = getattr(self.workers.get("report"), "router", None)
        if router is None or not router.local_multi_enabled() or not result.get("ok"):
            return result
        if step.get("worker") in ("verification", "report"):
            return result
        evaluation = router.evaluate_local_output(result.get("output"), task="worker:" + step.get("worker", "general"))
        ctx.setdefault("step_evaluations", {})[step.get("worker", "worker")] = evaluation
        result["quality_gate"] = evaluation
        if not evaluation.get("passed", True):
            result = dict(result)
            result["ok"] = False
            result["error"] = f"local quality gate failed: {evaluation.get('score')} < {evaluation.get('threshold')}"
        return result

    async def _runner(self, task, step, ctx):
        worker = self.workers.get(step["worker"])
        if worker is None: return {"ok":False,"output":{"error":f"unknown worker {step['worker']}"}}
        ctx.setdefault("outputs", {})
        # Workers are synchronous (subprocesses, file IO, HTTP probes); run them
        # in a worker thread so the aiohttp event loop stays responsive — the
        # pause/approve endpoints must be served while a step is executing.
        result = await asyncio.to_thread(worker.run, task["goal"], step.get("args", {}), ctx)
        result = self._evaluate_worker_output(task, step, result, ctx)

        # general plan pipeline: the planner step stages the validated plan as
        # the task's remaining steps (persisted by the engine after this step)
        if worker.id == "planner" and result.get("ok") and ctx.get("planned_steps"):
            planned = ctx.pop("planned_steps")
            base = task["current_step"] + 1
            for i, s in enumerate(planned):
                s["index"] = base + i
            task["steps"].extend(planned)
            result = dict(result)
            result["output"] = dict(result.get("output") or {})
            result["output"]["staged_steps"] = len(planned)

        if not result.get("ok") and not step.get("_replan_used"):
            # general plan pipeline: bounded, planner-driven replan on a failed
            # tool step — replace the REMAINING steps with a corrected plan
            replan_result = await self._replan_plan_task(task, step, ctx, result)
            if replan_result is not None:
                # failed step stays in history as FAILED (honest journal), but
                # is superseded by the corrected plan → task continues
                step["critical"] = False
                ctx.setdefault("step_results", []).append({"ok": False, "result": None})
                return replan_result
            step["_replan_used"] = True
            if self.event_cb:
                self.event_cb({"task_id":task["id"],"component":"supervisor","status":"REPLAN","detail":f"{worker.id} failed or quality gate rejected; rebuilding worker step"})
            replanned = next((s for s in self.plan(task["goal"]) if s.get("worker") == worker.id), None)
            retry_args = dict((replanned or step).get("args", {})); retry_args["replan"] = True

            # Prefer an explicitly declared alternate worker for deterministic recovery.
            # The alternate runs through the same worker interface and existing executor
            # safety gates; no approval state is fabricated here.
            alternate_id = getattr(worker, "alt", None)
            alternate = self.workers.get(alternate_id) if alternate_id else None
            if alternate is not None:
                if self.event_cb:
                    self.event_cb({"task_id":task["id"],"component":"supervisor","status":"REPLAN_ALTERNATE","detail":f"{worker.id} failed; trying alternate {alternate.id}"})
                alt_args = dict(step.get("args", {})); alt_args["replan"] = True; alt_args["fallback_from"] = worker.id
                alt_result = await asyncio.to_thread(alternate.run, task["goal"], alt_args, ctx)
                alt_result = self._evaluate_worker_output(task, {**step, "worker": alternate.id, "args": alt_args}, alt_result, ctx)
                if alt_result.get("ok"):
                    ctx["outputs"][alternate.id] = alt_result.get("output")
                    alt_result = dict(alt_result)
                    alt_result["replanned_from"] = worker.id
                    return alt_result
                result = dict(result)
                result["alternate"] = {"worker": alternate.id, "result": alt_result}

            result = await asyncio.to_thread(worker.run, task["goal"], retry_args, ctx)
            result = self._evaluate_worker_output(task, {**step, "args": retry_args}, result, ctx)

        if result.get("ok"):
            ctx["outputs"][worker.id] = result.get("output")
            if worker.id == "tool_step":
                tool = str(step.get("args", {}).get("tool") or "tool")
                ctx["outputs"][f"tool:{tool}"] = result.get("output")
                ctx.setdefault("step_results", []).append(
                    {"ok": True, "result": (result.get("output") or {}).get("result")})
        else:
            if worker.id == "tool_step":
                ctx.setdefault("step_results", []).append({"ok": False, "result": None})
        return result

    async def _replan_plan_task(self, task, step, ctx, failed_result):
        """Bounded replan for kind="plan" tasks: ask the planner for a
        corrected plan and replace the REMAINING steps. Returns the replan
        result dict, or None when replanning is unavailable/not allowed."""
        if step.get("worker") != "tool_step" or step.get("_replan_used"):
            return None
        planner_worker = self.workers.get("planner")
        if planner_worker is None or task.get("kind") != "plan":
            return None
        budgets = task.get("budgets") or {}
        remaining = int(budgets.get("replan_budget", 0))
        if remaining <= 0:
            return None
        step["_replan_used"] = True
        budgets["replan_budget"] = remaining - 1
        out = failed_result.get("output") or {}
        err = str(out.get("error") or out.get("verified") or "step failed")[:300]
        tool = str(step.get("args", {}).get("tool") or "tool")
        replan_goal = (f"{task['goal']}\n\n"
                       f"Not: önceki plandaki '{tool}' adımı başarısız oldu ({err}). "
                       f"Kalan hedefe ulaşmak için düzeltilmiş, uygulanabilir bir plan üret.")
        try:
            new_plan = await asyncio.to_thread(planner_worker.planner.make_plan, replan_goal)
        except Exception:
            # replan failed (e.g. brain down): the original failure stands
            step["_replan_used"] = False
            budgets["replan_budget"] = remaining
            return None
        idx = int(step.get("index", task.get("current_step", 0)))
        new_steps = []
        for i, s in enumerate(new_plan.get("steps", [])):
            new_steps.append({
                "label": f"{s['tool']}",
                "worker": "tool_step",
                "args": {"tool": s["tool"], "arguments": dict(s.get("arguments") or {}),
                         "reason": str(s.get("reason", ""))[:300],
                         "depends_on": list(s.get("depends_on") or [])},
                "critical": True, "attempts": 0, "status": "PENDING",
                "result": None, "input_hash": None})
        if not new_steps:
            step["_replan_used"] = False
            budgets["replan_budget"] = remaining
            return None
        # re-stage the shared closing workers so the corrected plan is also
        # independently verified and ends with an evidence-based report
        new_steps.append({"label": "verify outputs", "worker": "verification",
                          "args": {}, "critical": True, "attempts": 0,
                          "status": "PENDING", "result": None, "input_hash": None})
        new_steps.append({"label": "final evaluated report", "worker": "report",
                          "args": {}, "critical": False, "attempts": 0,
                          "status": "PENDING", "result": None, "input_hash": None})
        # keep the failed step in history (it stays FAILED), replace the rest
        for i, s in enumerate(new_steps):
            s["index"] = idx + 1 + i
        task["steps"] = task["steps"][:idx + 1] + new_steps
        if self.event_cb:
            self.event_cb({"task_id": task["id"], "component": "supervisor",
                           "status": "REPLAN",
                           "detail": f"{tool} failed ({err[:120]}); replanned "
                                     f"{len(new_steps)} steps (replan_budget={remaining - 1})"})
        return {"ok": False, "output": {
            "tool": tool, "executed": True, "succeeded": False,
            "verified": (out.get("verified") or {"mode": "none", "verified": None}),
            "error": err, "replanned": True, "new_steps": len(new_steps)}}

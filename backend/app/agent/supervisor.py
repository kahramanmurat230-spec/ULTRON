"""Supervisor Agent — decompose, delegate to workers, verify, replan.

THINK -> PLAN -> ACT (worker) -> OBSERVE -> VERIFY -> SUCCESS?
  YES -> COMPLETE         NO -> ANALYZE FAILURE -> REPLAN (alternative
worker, bounded) -> ACT. No infinite loops: budgets live in the task engine.

Workers are REAL subsystem adapters (no mocks):
  code_analysis -> CodeIntel static analysis (stdlib AST, offline)
  tests         -> project pytest run (subprocess, timeout-bounded)
  diagnostic    -> runtime.self_diagnostic() (real health scan)
  verification  -> output schema/sanity checks of previous workers
  report        -> deterministic evidence report (local multi-model polish optional,
                   only when healthy local models are available)

Workers execute inside the existing permission system: they only call
safe/read-only tools; anything dangerous keeps requiring the normal
approval gates (this layer never bypasses them).
"""
import subprocess
import sys
from pathlib import Path


class Worker:
    id = "worker"
    label = "worker"
    alt = None

    def run(self, goal: str, args: dict, ctx: dict) -> dict:
        raise NotImplementedError


class CodeAnalysisWorker(Worker):
    id = "code_analysis"
    label = "Static code analysis"
    alt = None

    def __init__(self, code_intel):
        self.code_intel = code_intel

    def run(self, goal, args, ctx):
        target = args.get("target") or ("frontend" if "frontend" in goal.lower()
                                        else "backend" if "backend" in goal.lower() else "all")
        rep = self.code_intel.analyze(target)
        issues = rep.get("issues", [])
        return {"ok": rep.get("summary", {}).get("files", 0) > 0,
                "output": {"target": target,
                           "files": rep.get("summary", {}).get("files", 0),
                           "issues": len(issues),
                           "high": sum(1 for i in issues if i.get("severity") in ("high", "error")),
                           "duplicates": len(rep.get("duplicates", [])),
                           "todo": len(rep.get("todo", [])),
                           "top": [{"file": i["file"], "line": i.get("line"),
                                    "sev": i["severity"], "msg": i["message"][:120]}
                                   for i in issues[:5]]}}


class TestWorker(Worker):
    __test__ = False
    id = "tests"
    label = "Project test suite"
    alt = None

    def __init__(self, project_root: Path):
        self.root = Path(project_root)

    def run(self, goal, args, ctx):
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=str(self.root), capture_output=True, text=True,
                timeout=int(args.get("timeout", 240)))
            tail = (r.stdout or r.stderr or "").strip().splitlines()
            return {"ok": r.returncode == 0,
                    "output": {"returncode": r.returncode,
                               "summary": tail[-1] if tail else "",
                               "tail": tail[-5:]}}
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": {"returncode": None, "summary": "timeout"}}


class DiagnosticWorker(Worker):
    id = "diagnostic"
    label = "Self diagnostic"
    alt = None

    def __init__(self, runtime):
        self.runtime = runtime

    def run(self, goal, args, ctx):
        res = self.runtime.self_diagnostic()
        return {"ok": bool(res.get("ok")), "output": {
            "overall": res.get("overall"),
            "report": (res.get("report") or "")[:400],
            "components": {k: v.get("status") for k, v in res.get("components", {}).items()
                           if isinstance(v, dict)}}}


class VerificationWorker(Worker):
    id = "verification"
    label = "Output verification"

    def run(self, goal, args, ctx):
        outputs = ctx.get("outputs", {})
        problems = []
        ca = outputs.get("code_analysis")
        if ca is not None:
            if not isinstance(ca, dict) or "issues" not in ca or ca.get("files", 0) < 1:
                problems.append("code_analysis output invalid")
        tw = outputs.get("tests")
        if tw is not None:
            if not isinstance(tw, dict) or "returncode" not in tw:
                problems.append("tests output invalid")
        dg = outputs.get("diagnostic")
        if dg is not None:
            if not isinstance(dg, dict) or "overall" not in dg:
                problems.append("diagnostic output invalid")
        if not outputs:
            problems.append("nothing to verify (no worker outputs)")
        return {"ok": not problems, "output": {
            "verified": sorted(outputs.keys()), "problems": problems}}


class ReportWorker(Worker):
    id = "report"
    label = "Final report"

    def __init__(self, router=None, llm_available=None):
        self.router = router
        self.llm_available = llm_available or (lambda: False)

    def run(self, goal, args, ctx):
        outputs = ctx.get("outputs", {})
        lines = [f"GOAL: {goal}"]
        ca = outputs.get("code_analysis")
        if ca:
            lines.append(f"CODE: {ca.get('files')} files · {ca.get('issues')} issues "
                         f"({ca.get('high')} high) · {ca.get('duplicates')} dup · {ca.get('todo')} todo")
            for t in ca.get("top", [])[:3]:
                lines.append(f"  - {t.get('sev')}: {t.get('msg')} @ {t.get('file')}:{t.get('line')}")
        tw = outputs.get("tests")
        if tw:
            lines.append(f"TESTS: rc={tw.get('returncode')} · {tw.get('summary')}")
        dg = outputs.get("diagnostic")
        if dg:
            lines.append(f"DIAGNOSTIC: {dg.get('overall')}")
        vf = outputs.get("verification")
        if vf:
            lines.append(f"VERIFICATION: {'PASS' if not vf.get('problems') else vf['problems']}")
        report = "\n".join(lines)
        if self.router is not None and self.llm_available():
            try:
                from app.core.model_router import TaskType
                if self.router.local_multi_enabled():
                    chosen, _results = self.router.race_local_and_judge(
                        messages=[
                            {"role": "system", "content": "Türkçe, kısa, net ve kanıta dayalı ULTRON raporu yaz. Boss diye hitap et. Gerçekleşmeyen bir işi yapılmış gibi gösterme."},
                            {"role": "user", "content": report[:6000]},
                        ],
                        system="Rapor adaylarını doğruluk, açıklık ve kanıt kullanımı açısından değerlendir. En iyi raporu doğrudan döndür.",
                    )
                    if chosen and chosen.content:
                        report = chosen.content.strip()
                else:
                    polished = self.router.ask(
                        TaskType.GENERAL,
                        "Aşağıdaki görev raporunu Türkçe, ULTRON personasıyla (Boss hitabı) kısa ve net özetle:\n" + report[:3000])
                    if polished:
                        report = polished.strip()
            except Exception:
                pass
        return {"ok": True, "output": report}


class SupervisorAgent:
    """Plans worker pipelines and drives them through the TaskEngine."""

    def __init__(self, task_engine, workers: dict[str, Worker], event_cb=None):
        self.engine = task_engine
        self.workers = workers
        self.event_cb = event_cb

    def plan(self, goal: str) -> list[dict]:
        g = goal.lower()
        steps: list[dict] = []
        if any(k in g for k in ("teşhis", "diagnostik", "diagnostic", "kendini kontrol", "sağlık")):
            steps.append({"label": "self diagnostic", "worker": "diagnostic", "args": {}})
        if any(k in g for k in ("analiz", "analyze", "incele", "kod", "code", "kalite", "bug")):
            steps.append({"label": "static analysis", "worker": "code_analysis", "args": {}})
        if any(k in g for k in ("test", "regresyon", "regression")):
            steps.append({"label": "project tests", "worker": "tests",
                          "args": {"timeout": 300}, "critical": False})
        if not steps:
            steps.append({"label": "static analysis", "worker": "code_analysis", "args": {}})
        steps.append({"label": "verify outputs", "worker": "verification", "args": {}})
        steps.append({"label": "final report", "worker": "report", "args": {}})
        return steps

    async def submit(self, goal: str, budgets: dict | None = None, spawn: bool = True) -> dict:
        task = self.engine.create(goal, kind="supervisor",
                                  steps=self.plan(goal), budgets=budgets)
        if spawn:
            self.engine.spawn(task["id"], self._runner)
        return task

    async def _runner(self, task: dict, step: dict, ctx: dict) -> dict:
        worker = self.workers.get(step["worker"])
        if worker is None:
            return {"ok": False, "output": {"error": f"unknown worker {step['worker']}"}}
        ctx.setdefault("outputs", {})
        result = worker.run(task["goal"], step.get("args", {}), ctx)
        if not result.get("ok") and worker.alt and not step.get("_alt_used"):
            if self.event_cb:
                self.event_cb({"task_id": task["id"], "component": "supervisor",
                               "status": "REPLAN",
                               "detail": f"{worker.id} failed -> alternative {worker.alt}"})
            step["_alt_used"] = True
            alt = self.workers.get(worker.alt)
            if alt is not None:
                result = alt.run(task["goal"], step.get("args", {}), ctx)
        if result.get("ok"):
            ctx["outputs"][worker.id] = result.get("output")
        return result

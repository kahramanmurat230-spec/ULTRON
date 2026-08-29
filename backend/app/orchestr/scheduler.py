"""WAVE 3 — DAG parallel scheduler: bounded concurrency, fair queue,
deadlock/starvation koruması, fail-fast/continue policy, cancellation.

Bağımsız worker'lar GERÇEKTEN paralel koşar (asyncio eşzamanlı yürütme);
bağımlı worker'lar bekletilir. Sınırlar: global + görev + rol başına
concurrency. Kuyruk yaşlandırma (aging) ile starvation önlenir; döngü
reddi + runtime deadlock tespiti vardır. Wave 1/2'ye dokunulmaz.
"""
from __future__ import annotations

import asyncio
import heapq
import time
import uuid

from app.orchestr.budgets import BudgetExceeded, BudgetPool
from app.orchestr.worker import (
    MAX_ATTEMPTS, InvalidWorkerTransition, Worker,
)

DEFAULT_GLOBAL_LIMIT = 4
DEFAULT_TASK_LIMIT = 4
DEFAULT_ROLE_LIMIT = 2
DEFAULT_WORKER_TIMEOUT_S = 120.0
MAX_WORKER_WAIT_S = 60.0          # starvation eşiği
AGE_BOOST_STEP = 10.0             # her 10 sn bekleme → +1 öncelik


class DAGCycleError(Exception):
    pass


class SchedulerPolicy:
    """Supervisor tarafından belirlenir."""
    def __init__(self, *, fail_fast: bool = True, retry: bool = True,
                 worker_timeout_s: float = DEFAULT_WORKER_TIMEOUT_S,
                 max_worker_wait_s: float = MAX_WORKER_WAIT_S):
        self.fail_fast = fail_fast
        self.retry = retry
        self.worker_timeout_s = float(worker_timeout_s)
        self.max_worker_wait_s = float(max_worker_wait_s)


class RunReport:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.run_id = uuid.uuid4().hex[:10]
        self.results: dict[str, dict] = {}        # worker_id → result
        self.states: dict[str, str] = {}          # worker_id → final state
        self.errors: dict[str, str] = {}
        self.started_at = time.time()
        self.finished_at = None
        self.starved: list[str] = []
        self.cancelled = False
        self.deadlocked: list[str] = []

    def finalize(self):
        self.finished_at = time.time()
        ok = all(s == "SUCCEEDED" for s in self.states.values()) and self.states
        return {"run_id": self.run_id, "task_id": self.task_id,
                "ok": bool(ok), "states": dict(self.states),
                "results": {k: v for k, v in self.results.items()},
                "errors": dict(self.errors), "starved": self.starved,
                "deadlocked": self.deadlocked, "cancelled": self.cancelled,
                "elapsed_ms": round((self.finished_at - self.started_at) * 1000, 2)}


def validate_dag(workers: list[Worker]) -> None:
    """Döngü + bilinmeyen bağımlılık reddi (koşumdan ÖNCE)."""
    ids = {w.worker_id for w in workers}
    for w in workers:
        for dep in w.depends_on:
            if dep not in ids:
                raise DAGCycleError(f"unknown dependency {dep} (worker {w.worker_id})")
    edges = {w.worker_id: set(w.depends_on) for w in workers}
    state: dict[str, int] = {}       # 0=unseen 1=visiting 2=done

    def dfs(node: str) -> None:
        mark = state.get(node, 0)
        if mark == 1:
            raise DAGCycleError(f"DAG cycle detected at {node}")
        if mark == 2:
            return
        state[node] = 1
        for nxt in edges.get(node, ()):
            dfs(nxt)
        state[node] = 2

    for node in edges:
        dfs(node)


class DAGScheduler:
    """Supervisor adına DAG koşuturur. Tek otorite: çağıran supervisor."""

    def __init__(self, registry, *, global_limit: int = DEFAULT_GLOBAL_LIMIT,
                 per_task_limit: int = DEFAULT_TASK_LIMIT,
                 per_role_limit: int = DEFAULT_ROLE_LIMIT, now=None):
        self.registry = registry
        self.global_limit = max(1, int(global_limit))
        self.per_task_limit = max(1, int(per_task_limit))
        self.per_role_limit = max(1, int(per_role_limit))
        self.now = now or time.time
        self._active: dict[str, dict] = {}         # task_id → run state
        self._seq = 0

    # ------------------------------------------------------------ helpers
    def _effective_priority(self, priority: int, waited_s: float) -> float:
        boost = min(4.0, waited_s / AGE_BOOST_STEP)     # aging: starvation KORUMASI
        return priority - boost

    def _limits_allow(self, run: dict, worker: Worker) -> bool:
        if len(run["running"]) >= self.global_limit:
            return False
        by_task = sum(1 for w in run["running"].values()
                      if w.task_id == worker.task_id)
        if by_task >= self.per_task_limit:
            return False
        by_role = sum(1 for w in run["running"].values()
                      if w.role == worker.role)
        if by_role >= self.per_role_limit:
            return False
        return True

    # ------------------------------------------------------------ run
    async def run(self, workers: list[Worker], executors: dict,
                  policy: SchedulerPolicy | None = None,
                  task_id: str | None = None,
                  budget_pool: BudgetPool | None = None) -> dict:
        policy = policy or SchedulerPolicy()
        task_id = task_id or (workers[0].task_id if workers else "none")
        validate_dag(workers)
        if budget_pool is not None:
            budget_pool.plan_check(workers)     # parent üst sınırı (rezervasyon)
        run = {
            "task_id": task_id, "workers": {w.worker_id: w for w in workers},
            "executors": executors, "policy": policy,
            "pending": {w.worker_id: w for w in workers},
            "queue": [],                        # heap: (prio, seq, worker_id)
            "running": {},                      # wid → asyncio.Task
            "report": RunReport(task_id),
            "cancelled": False, "done": asyncio.Event(),
            "enqueued_at": {}, "pool": budget_pool,
        }
        self._active[task_id] = run
        # Bağımlılık kapısı: yalnız bağımlılıkları KARŞILANMIŞ worker'lar
        # kuyruğa girer; diğerleri PENDING/BLOCKED bekler (döngü yok: DAG).
        for w in workers:
            if w.is_terminal:
                if w.state == "SUCCEEDED":
                    run["report"].states[w.worker_id] = w.state  # idempotent
                continue
            if self._deps_met(run, w):
                if w.state == "PENDING":
                    w.transition("READY")
                elif w.state == "BLOCKED":
                    w.transition("READY")
                self._enqueue(run, w)
            else:
                if w.state == "PENDING":
                    pass                              # bekle
                elif w.state in ("RUNNING", "WAITING", "RETRYING", "READY"):
                    try:
                        w.transition("BLOCKED",
                                     reason="waiting for dependencies")
                    except InvalidWorkerTransition:
                        pass
            self.registry.save(w)

        pump = asyncio.ensure_future(self._pump(run))
        try:
            await run["done"].wait()
        finally:
            pump.cancel()
            self._active.pop(task_id, None)
        return run["report"].finalize()

    @staticmethod
    def _deps_met(run: dict, worker: Worker) -> bool:
        for dep in worker.depends_on:
            d = run["workers"].get(dep)
            if d is None or d.state != "SUCCEEDED":
                return False
        return True

    def _enqueue(self, run: dict, worker: Worker):
        if worker.worker_id in run["enqueued_at"]:
            return
        run["enqueued_at"][worker.worker_id] = self.now()
        self._seq += 1
        heapq.heappush(run["queue"],
                       (worker.priority, self._seq, worker.worker_id))
        run["pending"][worker.worker_id] = worker

    # ------------------------------------------------------------ pump
    async def _pump(self, run: dict) -> None:
        """Fair scheduler: kuyruğu sınır içinde başlat, starvation ve
        deadlock izle, DAG bittiğinde düğümü kapar."""
        while not run["done"].is_set():
            progressed = False
            # 1) kuyruktan sınır müsadesiyle başlat (fair: heap sırası)
            pool = run.get("pool")
            if pool is not None and run["queue"] and not pool.resource_ok():
                await asyncio.sleep(0.01)   # cpu/mem sınırı: launch DURUR
                continue
            while run["queue"] and len(run["running"]) < self.global_limit:
                popped = []
                launched = False
                while run["queue"]:
                    prio, seq, wid = heapq.heappop(run["queue"])
                    w = run["workers"][wid]
                    if self._limits_allow(run, w):
                        self._launch(run, w)
                        launched = True
                        break
                    popped.append((prio, seq, wid))
                for item in popped:
                    heapq.heappush(run["queue"], item)
                if not launched:
                    break
                progressed = True
            # 2) starvation izle + aging
            now = self.now()
            for prio, seq, wid in list(run["queue"]):
                waited = now - run["enqueued_at"].get(wid, now)
                if waited > run["policy"].max_worker_wait_s \
                        and wid not in run["report"].starved:
                    run["report"].starved.append(wid)
                eff = self._effective_priority(run["workers"][wid].priority,
                                               waited)
                if eff < prio:
                    run["queue"].remove((prio, seq, wid))
                    heapq.heappush(run["queue"], (eff, seq, wid))
            # 3) deadlock: koşan yok + kuyruk boş + bitmemiş var → tıkandı
            if not run["running"] and not run["queue"]:
                leftover = [wid for wid, w in run["workers"].items()
                            if not w.is_terminal
                            and wid not in run["report"].states]
                if leftover:
                    for wid in leftover:
                        w = run["workers"][wid]
                        try:
                            w.transition("BLOCKED",
                                         reason="deadlock: unsatisfiable "
                                                "dependencies")
                        except InvalidWorkerTransition:
                            pass
                        run["report"].deadlocked.append(wid)
                        run["report"].states[wid] = w.state
                        self.registry.save(w)
                run["done"].set()
                return
            if not progressed:
                await asyncio.sleep(0.005)
            else:
                await asyncio.sleep(0)      # kooperatif geçiş

    def _launch(self, run: dict, worker: Worker):
        run["pending"].pop(worker.worker_id, None)
        run["enqueued_at"].pop(worker.worker_id, None)
        if worker.state == "BLOCKED":
            worker.transition("READY")
        if worker.state != "RUNNING":
            worker.transition("RUNNING")
        self.registry.save(worker)
        task = asyncio.ensure_future(self._run_worker(run, worker))
        run["running"][worker.worker_id] = worker
        run["tasks"] = run.get("tasks", {})
        run["tasks"][worker.worker_id] = task

    async def _run_worker(self, run: dict, worker: Worker):
        policy = run["policy"]
        executor = run["executors"].get(worker.role)
        timeout = float(worker.budget.get("wall_clock_s")
                        or policy.worker_timeout_s)
        try:
            if executor is None:
                worker.transition("FAILED", reason=f"no executor for role "
                                                   f"{worker.role}")
                self._finish(run, worker, error=worker.error)
                return
            result = await asyncio.wait_for(executor(worker), timeout=timeout)
            if not isinstance(result, dict) or "ok" not in result:
                worker.transition("FAILED", reason="invalid result shape")
                self._finish(run, worker, error="invalid result shape")
                return
            if result.get("ok"):
                pool = run.get("pool")
                if pool is not None:
                    usage = result.get("usage") or {}
                    try:
                        pool.consume(worker.worker_id,
                                     tokens=usage.get("tokens", 0),
                                     cost=usage.get("cost", 0),
                                     tools=usage.get("tools", 1),
                                     worker_caps=worker.budget)
                    except BudgetExceeded as bexc:
                        # bütçe hatası KALICI: retry yok, dürüst fail
                        worker.transition("FAILED", reason=str(bexc))
                        self._finish(run, worker, error=str(bexc))
                        return
                worker.result_id = result.get("result_id")
                worker.transition("SUCCEEDED")
                run["report"].results[worker.worker_id] = result
                self._finish(run, worker)
            else:
                self._fail(run, worker, str(result.get("error") or "not ok"))
        except asyncio.TimeoutError:
            worker.transition("TIMEOUT", reason="wall clock budget")
            self._finish(run, worker, error="timeout")
        except asyncio.CancelledError:
            if worker.state in ("RUNNING", "WAITING"):
                worker.transition("CANCELLED")
            self._finish(run, worker, cancelled=True)
            raise
        except Exception as exc:  # noqa: BLE001 — worker crash
            self._fail(run, worker, str(exc))

    def _fail(self, run: dict, worker: Worker, error: str):
        worker.transition("FAILED", reason=error)
        if (run["policy"].retry and worker.attempts < MAX_ATTEMPTS
                and not run["cancelled"]):
            worker.transition("RETRYING")      # SINIRLI retry (MAX_ATTEMPTS)
            self.registry.save(worker)
            run["pending"].pop(worker.worker_id, None)
            run["enqueued_at"].pop(worker.worker_id, None)
            self._enqueue(run, worker)
            run["running"].pop(worker.worker_id, None)
            self._check_done(run)
            return
        # deneme hakkı bitti → poison: dead-letter kanalı (Wave 1 semantiği)
        self._finish(run, worker, error=f"dead-letter: {error}")

    def _finish(self, run: dict, worker: Worker, error: str | None = None,
                cancelled: bool = False):
        run["running"].pop(worker.worker_id, None)
        run["report"].states[worker.worker_id] = worker.state
        if error:
            run["report"].errors[worker.worker_id] = error
        self.registry.save(worker)
        if cancelled or (run["policy"].fail_fast
                         and worker.state in ("FAILED", "TIMEOUT")
                         and not run["cancelled"]):
            # fail-fast: kalan her şeyi iptal et / continue: bağımlıları düşür
            self._on_worker_terminal(run, worker)
        else:
            self._on_worker_terminal(run, worker)
        self._check_done(run)

    def _on_worker_terminal(self, run: dict, worker: Worker):
        """Bağımlıların kaderi: dep başarılı → kuyruğa; değilse policy."""
        ok = worker.state == "SUCCEEDED"
        for wid, w in list(run["workers"].items()):
            if wid == worker.worker_id or w.is_terminal:
                continue
            if worker.worker_id in w.depends_on and wid not in run["report"].states:
                if ok and self._deps_met(run, w):
                    if w.state == "PENDING":
                        w.transition("READY")
                    elif w.state == "BLOCKED":
                        w.transition("READY")
                    run["enqueued_at"].pop(wid, None)
                    self._enqueue(run, w)
                else:
                    # bağımlılık başarısız: fail-fast → her şey durur;
                    # continue-on-failure → bu dal iptal, diğerleri sürer
                    if not run["policy"].fail_fast:
                        try:
                            w.transition("CANCELLED")
                        except InvalidWorkerTransition:
                            pass
                        run["report"].states[wid] = w.state
                        run["report"].errors[wid] = (
                            f"dependency {worker.worker_id} "
                            f"{worker.state}")
                        self.registry.save(w)
                        run["pending"].pop(wid, None)
                        for item in list(run["queue"]):
                            if item[2] == wid:
                                run["queue"].remove(item)

    def _check_done(self, run: dict):
        if run["cancelled"]:
            run["done"].set()
            return
        all_done = all(w.is_terminal or wid in run["report"].states
                       for wid, w in run["workers"].items())
        if all_done and not run["running"] and not run["queue"]:
            run["done"].set()

    # ------------------------------------------------------------ cancel
    async def cancel(self, task_id: str) -> dict:
        run = self._active.get(task_id)
        if not run:
            return {"ok": False, "error": "no active run"}
        run["cancelled"] = True
        run["report"].cancelled = True
        for wid, task in list(run.get("tasks", {}).items()):
            if not task.done():
                task.cancel()
        for wid, w in run["workers"].items():
            if not w.is_terminal:
                try:
                    if w.state == "PENDING":
                        w.transition("CANCELLED")
                    elif w.state in ("READY", "RETRYING", "BLOCKED"):
                        w.transition("CANCELLED")
                    elif w.state in ("RUNNING", "WAITING"):
                        w.transition("CANCELLED")
                except InvalidWorkerTransition:
                    pass
                run["report"].states[wid] = w.state
                self.registry.save(w)
        run["done"].set()
        return {"ok": True, "cancelled": len(run["report"].states)}

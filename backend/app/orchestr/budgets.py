"""WAVE 3 — Worker budget isolation (parent üst sınırı ile).

Model (dürüst kapsamıyla):
- Rezervasyon: worker başına cap belirtilmişse, cap TOPLAMI parent cap'ini
  aşamaz (plan zamanında reddedilir) → paralel worker'lar toplamda parent
  bütçesini ASLA aşamaz.
- Tüketim: her worker kullanımını sonucunda raporlar (usage.tokens/cost/
  tools); hem worker cap'i hem parent toplamı denetlenir → aşım =
  BudgetExceeded (worker FAILED, retry YOK — bütçe hatası kalıcıdır).
- wall_clock: worker başına zamanlayıcıda (Wave 1 budgets ile uyumlu).
- cpu/mem: süreç geneli örneklem (coroutine worker'ları süreç paylaşır —
  dürüst kapsam); parent cap aşılıysa yeni launch DURUR (kuyruk kalır).
"""
from __future__ import annotations

from app.orchestr.worker import Worker

CAP_AXES = ("token_budget", "cost_budget", "tool_budget")


class BudgetExceeded(Exception):
    pass


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class BudgetPool:
    """Parent bütçe havuzu — Supervisor tarafından oluşturulur."""

    def __init__(self, parent: dict | None = None, resource_sampler=None):
        self.parent = {k: _num((parent or {}).get(k)) for k in CAP_AXES}
        self.cpu_max_pct = _num((parent or {}).get("cpu_max_pct"))
        self.mem_max_mb = _num((parent or {}).get("mem_max_mb"))
        self._sample = resource_sampler or self._psutil_sample
        self.consumed = {k: 0.0 for k in CAP_AXES}
        self.per_worker: dict[str, dict] = {}

    @staticmethod
    def _psutil_sample():
        try:
            import psutil
            proc = psutil.Process()
            return (proc.cpu_percent(interval=None),
                    proc.memory_info().rss / (1024 * 1024))
        except Exception:
            return (0.0, 0.0)

    # ------------------------------------------------------------ plan
    def plan_check(self, workers: list[Worker]) -> None:
        """Worker cap toplamları parent'ı aşamaz (rezervasyon modeli)."""
        for axis in CAP_AXES:
            pcap = self.parent.get(axis)
            if pcap is None:
                continue
            total = sum(_num(w.budget.get(axis)) or 0.0 for w in workers)
            if total > pcap:
                raise BudgetExceeded(
                    f"plan rejected: worker {axis} sum {total:g} > parent "
                    f"{pcap:g} (workers would exceed parent budget)")

    # ------------------------------------------------------------ consume
    def consume(self, worker_id: str, *, tokens: float = 0.0, cost: float = 0.0,
                tools: float = 0.0, worker_caps: dict | None = None) -> dict:
        usage = self.per_worker.setdefault(worker_id,
                                           {k: 0.0 for k in CAP_AXES})
        delta = {"token_budget": float(tokens or 0),
                 "cost_budget": float(cost or 0),
                 "tool_budget": float(tools or 0)}
        for axis, add in delta.items():
            if add <= 0:
                continue
            new_w = usage[axis] + add
            wcap = _num((worker_caps or {}).get(axis))
            if wcap is not None and new_w > wcap:
                raise BudgetExceeded(
                    f"worker budget exceeded: {axis} {new_w:g} > {wcap:g}")
            new_p = self.consumed[axis] + add
            pcap = self.parent.get(axis)
            if pcap is not None and new_p > pcap:
                raise BudgetExceeded(
                    f"parent budget exceeded: {axis} {new_p:g} > {pcap:g}")
            usage[axis] = new_w
            self.consumed[axis] = new_p
        return dict(usage)

    # ------------------------------------------------------------ resources
    def resource_ok(self) -> bool:
        """CPU/RAM parent sınırı: aşım varsa yeni worker launch DURUR."""
        if self.cpu_max_pct is None and self.mem_max_mb is None:
            return True
        cpu, mem = self._sample()
        if self.mem_max_mb is not None and mem > self.mem_max_mb:
            return False
        if self.cpu_max_pct is not None and cpu > self.cpu_max_pct:
            return False
        return True

    def usage(self) -> dict:
        return {"parent_caps": dict(self.parent),
                "consumed": dict(self.consumed),
                "per_worker": {k: dict(v) for k, v in self.per_worker.items()}}

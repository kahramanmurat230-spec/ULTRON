"""WAVE 4 — Fusion Agent (§21): Wave 3 supervisor altında paralel
multimodal toplama.

Supervisor
├── Vision Worker     (ekran/OCR gözlemi — gerçek AdaptiveVisionLoop)
├── Browser Worker    (DOM/AX durumu — gerçek BrowserRuntime)
├── Computer Worker   (aktif pencere/UIA)
├── Coding Worker     (proje bağlamı)
└── Verification Worker (bağımsız kontrol)

Wave 3 sözleşmeleri AYNEN korunur: capability token, DAG paralelliği,
budget. Bu sınıf yalnız worker executor'larını multimodal kaynaklara
bağlar ve sonuçları MultimodalContext'e birleştirir.
"""
from __future__ import annotations

import asyncio

from app.multimodal.context_fusion import (
    ContextEntry, MultimodalContext, MultimodalEventBridge,
    MultimodalMemoryPolicy,
)


def default_executors(*, vision=None, browser=None, computer=None,
                      coding=None, verification=None, ctx=None,
                      bridge: MultimodalEventBridge | None = None):
    """Roller → Wave 3 executor sözlüğü (fn(worker, ctx) → sonuç dict).

    Her executor kendi gerçek kaynağından okur; kaynak yoksa dürüst
    unavailable/None döner (sahte veri YOK).
    """

    async def vision_worker(worker, wctx):
        if vision is None:
            return {"ok": False, "output": "vision unavailable",
                    "confidence": 0.0, "evidence": []}
        out = vision()                        # gerçek loop/frame özeti
        if bridge:
            bridge.emit("screen.changed", {"by": "vision_worker"})
        return {"ok": True, "output": out, "confidence": 0.8,
                "evidence": ["vision"]}

    async def browser_worker(worker, wctx):
        if browser is None:
            return {"ok": False, "output": "browser unavailable",
                    "confidence": 0.0, "evidence": []}
        out = browser()
        if bridge:
            bridge.emit("browser.page_changed", {"by": "browser_worker"})
        return {"ok": True, "output": out, "confidence": 0.85,
                "evidence": ["dom"]}

    async def computer_worker(worker, wctx):
        if computer is None:
            return {"ok": False, "output": "computer unavailable",
                    "confidence": 0.0, "evidence": []}
        if bridge:
            bridge.emit("computer.action_started", {"by": "computer_worker"})
        out = computer()
        if bridge:
            bridge.emit("computer.action_verified",
                        {"by": "computer_worker", "ok": bool(out)})
        return {"ok": bool(out), "output": out or "gözlem yok",
                "confidence": 0.75 if out else 0.2, "evidence": ["uia"]}

    async def coding_worker(worker, wctx):
        if coding is None:
            return {"ok": False, "output": "proje bağlamı yok",
                    "confidence": 0.0, "evidence": []}
        out = coding()
        return {"ok": True, "output": out, "confidence": 0.9,
                "evidence": ["code"]}

    async def verification_worker(worker, wctx):
        if verification is None:
            return {"ok": False, "output": "doğrulama kaynağı yok",
                    "confidence": 0.0, "evidence": []}
        out = verification()
        return {"ok": True, "output": out, "confidence": 0.85,
                "evidence": ["independent"]}

    return {"VISION": vision_worker, "BROWSER": browser_worker,
            "COMPUTER": computer_worker, "CODING": coding_worker,
            "VERIFICATION": verification_worker}


class FusionAgent:
    """Kullanıcı hedefi → paralel multimodal toplama → bağlam birleştirme."""

    def __init__(self, orchestrator, *, context: MultimodalContext | None =
                 None, bridge: MultimodalEventBridge | None = None,
                 memory_policy: MultimodalMemoryPolicy | None = None,
                 task_id: str = "fusion"):
        self.orch = orchestrator          # Wave 3 SupervisorOrchestrator
        self.context = context or MultimodalContext()
        self.bridge = bridge or MultimodalEventBridge()
        self.memory_policy = memory_policy or MultimodalMemoryPolicy()
        self.task_id = task_id

    async def gather(self, executors: dict, spec: list[dict] | None = None,
                     judge_worker_id="supervisor-judge") -> dict:
        """Wave 3 DAG'de paralel toplama; sonuçlar bağlama düşer."""
        if spec is None:
            spec = [
                {"role": "VISION", "name": "v"},
                {"role": "BROWSER", "name": "b"},
                {"role": "COMPUTER", "name": "c"},
                {"role": "CODING", "name": "k"},
                {"role": "VERIFICATION", "name": "ver",
                 "depends_on": ["v", "b", "c", "k"], "priority": 8},
            ]
        ws = self.orch.plan(self.task_id, spec)
        report = await self.orch.execute(self.task_id, ws, executors,
                                         judge_worker_id=judge_worker_id)
        for w in ws:
            state = report["states"].get(w.worker_id)
            if state != "SUCCEEDED":
                continue
            raw = report.get("results", {}).get(w.worker_id) or {}
            text = str(raw.get("output", ""))[:500]
            if not text:
                continue
            self.context.add(ContextEntry(
                kind={"VISION": "vision", "BROWSER": "browser",
                      "COMPUTER": "screen", "CODING": "code",
                      "VERIFICATION": "task"}.get(w.role, "task"),
                content=text, source=f"worker:{w.role}",
                confidence=float(raw.get("confidence", 0.5))))
        return report

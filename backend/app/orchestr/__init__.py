"""WAVE 3 — Multi-agent orchestration (additive over frozen Waves 1-2).

Supervisor otoritesi korunur; uzman worker'lar DAG üzerinden güvenli
paralel çalışır. Modüller:
  worker.py    — worker modeli + deterministik durum makinesi + kalıcılık
  tokens.py    — imzalı capability token'ları (escalation imkânsız)
  messages.py  — worker↔worker güvenli mesaj veriyolu (loop korumalı)
  artifacts.py — immutable artifact yöneticisi (erişim kontrollü)
  budgets.py   — worker bütçe izolasyonu (parent üst sınırı)
  scheduler.py — DAG paralel zamanlayıcı (bounded concurrency, fair queue)
  results.py   — sonuç hattı: validation → reviewer → judge → merge
  council.py   — sınırlı uzmanlar kurulu (round/budget/timeout)
  orchestrator.py — Supervisor: goal → DAG → paralel → sonuç → karar

Güvenlik: security core immutabledır; Wave 1/2 sözleşmeleri değişmez.
"""

# Wave 3 durumu: FROZEN (12/12 commit; bkz. /WAVE3_AUDIT.md)
WAVE3_FROZEN = True

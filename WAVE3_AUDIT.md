# WAVE 3 — Audit & Final Report (Supervisor Altında Çoklu Uzman Worker)

Tarih: 2026-08-29 · Dal: `arena/01a0489d-ultron` · Taban: Wave 2 FROZEN (313+2)

## §FINAL

### STATUS
**COMPLETE → FROZEN.** Tüm kabul kriterleri gerçek testlerle kanıtlandı; sahte
paralellik/recovery/benchmark YOK; security core'a (app/security/**, auth.py,
executor.py, codegen.py, runtime.py) dokunulmadı; Wave 1/2 davranışları additive
korundu (regression floor kırılmadı).

### CURRENT
Wave 3 kapsamı kapalı. Wave 4'e geçilmez (talimat: Wave 3 bitince DUR).

### CHANGED (additive — hepsi yeni dosya, çekirdek sıfır değişiklik)
`backend/app/orchestr/`: worker.py, scheduler.py, budgets.py, messages.py,
tokens.py, artifacts.py, results.py, orchestrator.py, council.py,
safe_text.py (+ `__init__.py`). Wave 1/2 dosyaları unchanged.

### FILES / WORKER TYPES
| Modül | İçerik |
|---|---|
| worker.py | Worker şeması (worker_id/task_id/parent_task_id/role/capabilities/permissions/budget/state/trace_id/created_at/started_at/finished_at), 10 state FSM, MAX_ATTEMPTS=3 → dead-letter |
| scheduler.py | DAG doğrulama (cycle→DAGCycleError), global/per-task/role limitleri, aging (+1/10s, cap 4), max-wait, fail_fast/retry/timeout policy, budget_pool kapısı |
| budgets.py | BudgetPool: CPU/RAM/token/tool/wall-clock/cost; plan_check + per-worker+parent consume; psutil sampler enjekte edilebilir |
| messages.py | AgentMessageBus: SQLite dayanıklı, 64KB payload cap, registry kimlik doğrulama (impersonation/cross-task RED), dedup/loop/quota, wait_for async, redaction |
| tokens.py | CapabilityTokenAuthority: HMAC token (uct.*), risk tablosu (WRITE_WORKSPACE=HIGH…), HIGH/CRITICAL yalnız approve() sonrası, restart→tüm token'lar geçersiz |
| artifacts.py | ArtifactManager: immutable PK, owner-task erişim kontrolü, hash bütünlüğü, RLock'lu yarış güvenliği |
| results.py | make_result/validate/detect_conflicts/resolve_conflict(evidence>provenance>confidence; beraberlik→UNCERTAIN)/review/judge/merge |
| orchestrator.py | SupervisorOrchestrator: plan→DAG, execute (tracer span zinciri, budget kapısı, fail_fast), ctx güvenli kanallar (grant/send/artifact), approve(), recover() (keep/retry/poison), ResultMemoryPolicy (default DENY) |
| council.py | CouncilSession: max_rounds+timeout+mesaj bütçesi; yasak alanlar (capability/permission/security/budget/token/approval); gizli kanal YASAK; kendi-önerisini-kabul SAYILMAZ; ratify yalnız SUPERVISOR/JUDGE |
| safe_text.py | Kanal seviyesinde 2. geçiş maske (core redaction'un "token password: X" kalıntısını kapatır — core'a dokunulmadan) |

Roller: RESEARCH/CODING/VISION/BROWSER/COMPUTER/SYSTEM/MEMORY/VERIFICATION/
REVIEWER (+JUDGE kararı supervisor-judge çalıştırır).

### DAG / CONCURRENCY / BUDGETS
Bağımsız task'lar paralel koşar; bağımlılar precondition bekler (gereksiz seri
YASAK — speedup testiyle kanıt). Limitler: global=4 (varsayılan), per-task,
per-role=2; limit aşılırsa worker READY kuyrukta kalır (peak==4 testiyle
kanıtlandı; sınırsız worker YASAK). Budget: parent cap plan_check'te, kaynak
kapısı her pump'ta, tüketim başarıda; aşımda FAILED (retry'sız).

### MESSAGING / TOKENS
Bus: güvenli abstraction; payload 64KB; process/memory erişimi YOK; loop
koruması + replay penceresi; secret kanala düşmez (2. geçiş maske). Token
zinciri: malformed/forged/tampered/expired/wrong-worker/cross-task/scope-escape/
revoked RED; HIGH/CRITICAL onay kapılı; secret token gövdesinde yok.

### RESULT PIPELINE / REVIEWER / JUDGE / COUNCIL
worker çıktısı → make_result → validate (artifact hash dahil) → çelişki tespiti
→ çözüm (evidence>provenance>confidence; çözülmezse UNCERTAIN — TAHMİN YOK) →
bağımsız review → judge (ACCEPT/REJECT/RETRY/REPLAN/ESCALATE; self-approval→
ESCALATE; kanıtsız→RETRY) → merge. Council: çelişki çözülmezse müzakere
(limitli); bypass alanları ihlal kaydı; final otorite Supervisor/Judge.

### RECOVERY
DETECT→CLASSIFY→RETRY→RECOVER→REPLAN. restart'ta SUCCEEDED asla yeniden koşmaz
(gerçek subprocess crash + in-process DB reopen testleriyle kanıt); FAILED
attempts<MAX→retry, doluysa poison (otomatik sonsuz retry YOK); CANCELLED
terminal→kalan iş REPLAN ile yeni worker.

### SECURITY (14/14) — test_wave3_security.py
escalation, token forgery, expired, wrong-worker, cross-task, scope escape,
impersonation, message spoofing, replay, payload injection, shared-state race,
artifact access, secret leakage (tüm kanallar: trace/artifact/message/event),
approval bypass (authority + council yolu). Gerçek bulgu: core redaction kalıntı
değer sızıntısı → Wave 3 kanallarında 2. geçiş maske ile kapatıldı (core
DOKUNULMADI).

### FAILURE INJECTION (14/14) — test_wave3_failure.py
worker crash, timeout, supervisor restart, message loss (dayanıklılık),
duplicate, out-of-order, deadlock (cycle reddi + wait timeout), DB kesintisi
(loud failure), artifact çakışma (immutable), reviewer failure, judge yok
(supervisor-judge), kısmi DAG, mid-run cancel, parent crash (REPLAN). Kurtarma
idempotent kanıtlandı.

### PERFORMANCE / BASELINE / SPEEDUP — test_wave3_performance.py
Sequential baseline KARŞILAŞTIRMALI (sahte yok): 4 bağımsız worker × 80 ms →
**seq=0.339 s, par=0.091 s, speedup=3.74×** (eşik 1.6×). Scheduler overhead
16 worker < 1.2 s ek; bus p50 < 5 ms; 100 sonuçluk pipeline < 2 s; worker
startup < 50 ms; budget kapısı < 100 µs/call. Kazanç kanıtı olmadan concurrency
iddiası YOK.

### TESTS / PASSED / FAILED / SKIPPED / GOLDEN
- Wave 3 testleri: **140** (workers 11, scheduler 19, budgets 9, messages 11,
  tokens 13, results 19, orchestrator 8, council 15, security 14, failure 14,
  performance 7)
- Tam suite: **453 passed, 0 failed, 2 skipped** (skip: bilinen ortam bağımlı
  2 vaka; taban Wave 2: 313+2 → kırılım YOK)
- Golden (test_eval_golden): **7/7 geçti** (46 vaka: intent/planning/risk/
  browser/url/router/taskroute) — test değiştirilmedi
- Test geçmek için mevcut testler DEĞİŞTİRİLMEDİ

### COMMITS (12/12)
1. ef1ac2f worker abstraction · 2. d24e393 DAG scheduler · 3. 26d7468
concurrency/budgets · 4. 2b486cc message bus · 5. 70fe000 capability tokens ·
6. 13e5d4c artifacts+results (reviewer/judge) · 7. fefad20 orchestrator
(cancellation/recovery) · 8. bbe83a8 council/negotiation · 9. ca1bb07
security+failure · 10. ede12a4 performance/regression · 11. docs (bu dosya) ·
12. FREEZE

### REMAINING / FREEZE
Kalan iş: **YOK**. Kalıcı ilkeler korunur: secret kanallarda görünmez;
CRITICAL işlemler açık onaysız çalışmaz; LLM/worker çıktısı asla trusted
command değil; poison→DLQ+human notify. **WAVE 3 FROZEN.**

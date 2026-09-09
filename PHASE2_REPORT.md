# PHASE 2 REPORT — P0/P1 Fixes + Architectural Unification

Devamı: `BASELINE_AUDIT.md` (Faz 1 denetimi, donmuş durum).

## Test durumu

| Aşama | Passed | Failed | Skipped | Golden |
|---|---|---|---|---|
| Baseline (Faz 1) | 952 | 0 | 5 | 36/36 |
| P0-1 sonrası | 961 | 0 | 5 | 36/36 |
| P0-2/P0-3 + P1 sonrası | 983 | 0 | 5 | 36/36 |
| **Birleştirme (bu rapor) sonrası** | **1004** | **0** | **5** | **36/36** |

Yeni test dosyaları: `test_shell_policy.py` (+~30), `test_tasks_pause_resume.py` (13),
`test_tasks_api.py` (3, gerçek HTTP E2E), `test_terminal_parsing.py` (16),
`test_plan_task_pipeline.py` (16), `test_plan_task_e2e.py` (5, gerçek HTTP E2E).

## Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `backend/app/security/shell_policy.py` | P0-1: yıkıcı komut algılama (rm/sudo/mkfs/dd/fork bombası/chmod/chown), kanonikleştirme (tırnak, `$(...)`, base64/hex pipe, zincir), onay asla hard block'u aşamaz |
| `backend/app/tasks/engine.py` | P0-2 `list(status)`, P0-3 gerçek `pause()/resume()`, kalıcı **one-shot** `user_approved` kolonu (ALTER TABLE göçü), NeedsApproval deneme iadesi, başarısız adımın yapılandırılmış çıktısının (executed/succeeded/verified) kaybı düzeltildi |
| `backend/app/tasks/__init__.py` | FSM: RUNNING→PAUSED, PAUSED→{RUNNING,CANCELLED} |
| `backend/agent.py` | P1: `_extract_terminal_command` — "terminalde X çalıştır" → tam X (önek + iyelik-son ek biçimi, büyük/küçük korunur) |
| `backend/app/agent/supervisor.py` | **PlannerWorker** (gerçek LLM planı → kalıcı görev adımları), **ToolStepWorker** (risk sınıflandırma, onay kapısı, yürütme, bağımsız doğrulama), audit/plan yönlendirmesi, **sınırlı replan** (replan_budget=2) |
| `backend/app/agent/step_verify.py` | YENİ — bağımsız etki doğrulama: dosya sistemi bit-durumu, süreç tablosu (psutil), canlı tarayıcı durumu, `expect{}` beklentileri, salt-okunur araçlarda şekil kontrolü; gözlemlenemeyen etki `verified=None` (asla sahte başarı) |
| `backend/app/agent/planner.py` | `world_fn` → sistem prompt'unda sınırlı dünya-model bağlamı (bellek + doğrulanmış sonuçlar zaten bağlıydı) |
| `backend/app/core/runtime.py` | Planner'a `world_fn`; **önceden var olan gerçek hata düzeltmesi**: sandboxed_* dosya araçları çağrı anında NameError veriyordu (import `__init__` içinde, lambda'lar modül globali arıyordu) — modül seviyesine taşındı |
| `backend/server.py` | planner/tool_step worker'ları gerçek V16 runtime'dan kayıt; approve/resume/recovery artık kind=plan + WAITING_BRAIN kapsıyor; TASK_COMPLETE → OutcomeLearner; DurableEventBus izi; TaskScheduler (cron → supervisor boru hattı) + tick döngüsü; WorldStore periyodik kalıcılık; Memory V3 stats; yeni uçlar: `/api/events`, `/api/schedules` (GET/POST/DELETE), `/api/world/history`, `/api/memory/v3` |

## Birleşik üretim yolu (tek yol, kopya yok)

```
Kullanıcı → /api/tasks veya sohbet
  → SupervisorAgent.submit  (yönlendirme: audit-anahtarlı hedef → sabit audit hattı;
                             diğerleri → kind="plan")
  → PlannerWorker: Planner.make_plan (bellek + doğrulanmış sonuçlar + dünya modeli
    bağlamı; yetenek-doğrulamalı ≤12 adım) — beyin kapalıysa dürüst WAITING_BRAIN
  → kalıcı TaskEngine (SQLite, journal/WAL, checkpoint, FSM)
  → ToolStepWorker: risk ön-sınıflandırma
       hard block (self-coding sınırı vb.) → adım BAŞARISIZ, onay bunu ASLA aşamaz
       MEDIUM+ onaysız → WAITING_APPROVAL → /api/tasks/{id}/approve → kalıcı
       one-shot onay → yürütme
  → gerçek Executor + FilesystemSandbox + UndoJournal → gerçek etki
  → StepVerifier: bağımsız gözlem (disk/süreç/tarayıcı) — aracın iddiasına güven yok;
    iddia ≠ etki → verified=False → adım başarısız
  → başarısız adım → sınırlı replan (kalan adımlar planner'a yeniden kurulur,
    replan_budget tükenince dürüst FAILED; başarısız adım geçmişte FAILED kalır)
  → verification + kanıt temelli rapor → COMPLETED
  → OutcomeLearner: "Verified outcome ..." kaydı → Planner'ın okuduğu aynı bellek
  → DurableEventBus: her olay kalıcı iz; WorldStore: periyodik dünya durumu;
    TaskScheduler: cron hedefleri AYNI boru hattından
```

## E2E kanıtı (gerçek HTTP, gerçek sunucu süreci)

`tests/test_plan_task_e2e.py` — yalnızca yerel LLM yerine vekil HTTP servis (Ollama bu
sandbox'ta yok); diğer her şey üretim kodu:
- genel hedef → kind=plan → planner gerçek HTTP çağrısı → plan sahneye kondu
- write_text (tehlikeli) → WAITING_APPROVAL; onay öncesi diskte dosya YOK
- `/api/tasks/{id}/approve` → gerçek Executor+Sandbox yazdı → bağımsız dosya sistemi
  doğrulaması `verified=True` → verification+rapor → COMPLETED; onay tüketildi
- sonuç (`Verified outcome ...`) GERÇEK V16 belleğe yazıldı, `/api/memory/v16/search`
  buluyor (planner'ın okuduğu depo)
- `/api/events` tüm koşuyu kaydetti (canlı sunucuda 167 olay); `/api/world/history`
  presence geçmişini döndürüyor (arka plan kalıcılık döngüsü çalışıyor)
- audit hedefi ("repo analizi ve testleri calistir") E2E'de hâlâ tam audit hattıyla COMPLETED

## Üretime bağlanan (önceden test-only) bileşenler

TaskScheduler (+`/api/schedules`, cron→supervisor), DurableEventBus (`/api/events`,
görev olay izi), WorldStore (periyodik upsert + `/api/world/history`), MemoryStore V3
(`/api/memory/v3`, salt-okunur), OutcomeLearner (TASK_COMPLETE'te yazar), Planner
world bağlamı, PlannerWorker/ToolStepWorker (genel plan yolu).

## Kalan P1/P2 (dürüst liste)

- P1 yok — tüm P0/P1 maddeleri kapandı ve test edildi.
- P2 küçük: `gui.py` yinelenen `find_text_in_elements`; onay audit kaydında onaylayan
  kimliği yok; `TOOL_RISK` sözlüğünde "shell_exec" etiketi yok (kayıtlı dangerous=True
  → yine de onay kapısında); kök `data/metrics` artefakt dizini .gitignore'da değil.

## Bu ortamda DOĞRULANAMAYANLAR (Linux sandbox; sahte/PASS iddiası YOK)

- **Ollama'sız**: gerçek yerel LLM plan kalitesi (qwen2.5-coder:7b) — planner zinciri
  vekil beyinle test edildi; gerçek model davranışı doğrulanmadı.
- **Gerçek tarayıcı yok**: browser adımlarının canlı sayfa doğrulaması kod yoluyla
  bağlı (BrowserAgent.url()/title(), browser_verify) ama bu ortamda çalıştırılmadı.
- **Windows** (pygetwindow, kayıt defteri, Windows araçları): Linux'ta doğrulanamaz — AÇIK.
- **Donanım** (mikrofon/kamera/Bluetooth/IoT/ekran): donanımsız — AÇIK.
- **GUI otomasyonu** (pyautogui): DISPLAY yok — AÇIK.

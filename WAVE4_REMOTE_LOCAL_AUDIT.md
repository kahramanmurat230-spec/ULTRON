# WAVE 4 REMOTE ↔ LOCAL AUDIT — READ-ONLY COMPARISON REPORT

Tarih: 2026-08-29 · Dal: `arena/01a0489d-ultron`
REMOTE HEAD: `54f5cdb510a23db463ddbf01d7904e74e6a2c079` (orijinal JARVIS Foundation zinciri, PR #1 head)
LOCAL HEAD : `dfa1702ab25b385f278917d784da57de0a401233` (integrity + Wave 4 + repair + FREEZE)
merge-base: `2252f99` (ULTRON Clean Neural baseline) → zincirler DIVERGE.
Metot: `git diff FETCH_HEAD HEAD`, `git show`, kaynak incelemesi. **Hiçbir dosya
değiştirilmedi; bu rapor commit edilmedi.**

---

## 0. ÖZET MATRİS

- Farklı dosya: **104** = 29 M (iki tarafta da var, içerik farklı) + 75 A (yalnız yerelde).
- Uzakta olup yerelde HİÇ bulunmayan dosya: **0** (uzaktaki tüm ek içerik 29 M
  dosyanın içindedir).
- Yerelde olup uzakta bulunmayan: 75 dosya — **tüm Wave 2/3/4 katmanları**
  (orchestr 11, events 3, memory-v3 4, multimodal 13, world 2, observability,
  tasks/scheduler, bench scriptleri, WAVE2/3/4_AUDIT.md, 13 wave4 test dosyası).
- Testler: iki tarafta da ortak test dosyaları BİREBİR AYNI (tests/ M listesinde
  değil) + yerelde 13 wave4 test dosyası ekstra. Yerel suite: 631P/4S.

## 1. ODAK DOSYALAR (tam analız)

### FILE: backend/app/security/vault.py
- REMOTE_LINES: 143 · LOCAL_LINES: 107
- REMOTE_FUNCTIONS: class VaultUnavailable; CredentialVault{__init__,_load_or_create_key,_require,_load,_save,set,get,delete,list_names,all_values,redact,health}
- LOCAL_FUNCTIONS: CredentialVault{__init__,_load_or_create_key,_read_store,_write_store,set,get,delete,list_names,redact}
- REMOTE_CAPABILITIES: Fernet at-rest; ULTRON_VAULT_KEY env; .vault.key 0600;
  InvalidToken handle; **cryptography eksikse VaultUnavailable ile dürüst degrade**;
  set() audit yazar (VAULT_SET/DELETE, değer ASLA loglanmaz); girdilerde meta+ts;
  **all_values() → redaction tohumlama (yalnız sunucu-tara)**; redact() =
  pattern+concrete değer birleşimi; **health()** (available/entries/key_source);
  HTTP üzerinden değer okuma API'si bilinçli olarak YOK.
- LOCAL_CAPABILITIES: aynı çekirdek sözleşme (test_sandbox_vault 12/12): Fernet,
  env öncelik, keyfile 0600, set/get/delete/list_names/redact; store 0600;
  atomic yazım (.tmp+os.replace).
- REMOTE_ONLY: VaultUnavailable degrade; all_values(); health(); audit entegrasyonu;
  meta/ts alanları; liste şekli [{name,meta,ts}]; default dizin data/vault;
  InvalidToken özel yakalama; redact'te pattern birleşimi.
- LOCAL_ONLY: store {"secrets":{...}} zarfı; secrets.json 0600; değer <4 kar. filtresi yok.
- CONFLICT: **D** — imza farkları: list_names() str-list vs dict-list; set() None vs
  {"ok":True}; default dizin farkı (data/security/vault vs data/vault). Ortak testler
  iki şekli de geçiriyor (test yalnız "değer sızmasın" diyor); ancak uzak
  server.py api_vault_list dict şekli bekler.
- KATEGORİ: all_values/health/audit/degrade = **B (yerelde eksik)**; imzalar = **D**.
- RECOMMENDATION: uzak sürüm temel alınmalı (daha zengin + degrade dürüst); yerel
  atomic-write + 0600 store korunarak birleştirilmeli. Testler etkilenmez.

### FILE: backend/app/tools/system_tools.py
- REMOTE_LINES: 86 · LOCAL_LINES: 72
- REMOTE_FUNCTIONS: system_status, process_list(sort="cpu",limit=30,name), process_info(pid), process_kill(pid), system_settings_view
- LOCAL_FUNCTIONS: system_status, process_list(limit=50,name), process_info(pid), process_kill(pid), system_settings_view
- REMOTE_CAPABILITIES: process_list alanları {pid,name,username,cpu_percent,memory_percent,status}, cpu/memory sıralaması, limit clamp 1..100; process_info {create_time, connections(izinsizse None dürüst), cmdline[:20], cpu_percent, memory_percent, exe}; settings_view: HKCU+HKLM çift kovan startup envanteri + python/platform bilgisi.
- LOCAL_CAPABILITIES: aynı isimler; daha yalın alanlar {pid,name,(cmdline,status,created_at)}; kill self-guard PermissionError + wait doğrulaması (test sözleşmesi tam).
- REMOTE_ONLY: sort parametresi + zengin alanlar; connections sayısı; exe; HKLM kovanı; platform.python_version.
- LOCAL_ONLY: — (alt küme + created_at adı farkı)
- CONFLICT: **D** — process_list imza sırası (sort,limit,name) vs (limit,name);
  process_info alan adları (create_time vs created_at). test_process_tools her
  ikisiyle de geçer (sözleşme pid/name/cmdline/killed/self-red).
- RECOMMENDATION: uzak imza+alanlar adopt edilip yerel self-guard/wait aynen
  korunmalı (ikisi de aynı güvenlik kuralı).

### FILE: backend/app/proactive/monitor.py
- REMOTE_LINES: 90 · LOCAL_LINES: 90
- REMOTE_FUNCTIONS: __init__,start,stop,_check_metric,evaluate,_loop (hata backoff _last_error_at)
- LOCAL_FUNCTIONS: __init__,start,stop,evaluate,_loop
- REMOTE_CAPABILITIES: spike-suppression (sustained_samples), hysteresis bandı,
  alarm cooldown, metrik başına durum, döngü hata backoff'u.
- LOCAL_CAPABILITIES: birebir aynı semantik evaluate() (repair commit 9051aae
  sözleşmeden türetildi); + süreklilik koşulu cooldown'dan önce.
- REMOTE_ONLY: _check_metric ayrımı; _last_error_at hata backoff (gövde farkı).
- LOCAL_ONLY: _METRICS tablosu (aynı davranışın veri-yapısal hali)
- CONFLICT: **A** — davranış eşdeğer (aynı testler iki tarafta da geçer).
- RECOMMENDATION: geçerli yerel sürüm kalabilir; backoff gövdesi port edilabilir (opsiyonel).

### FILE: backend/codegen.py
- REMOTE_LINES: 190 · LOCAL_LINES: 162
- REMOTE_FUNCTIONS: CodeGen{__init__,propose,apply(commit:bool=False),reject,status}, _llm_patch
- LOCAL_FUNCTIONS: CodeGen{__init__,propose,apply,reject,status}, _llm_patch
- REMOTE_CAPABILITIES: SelfCodeBoundary check (try İÇİNDE), timestamp'lı backup
  dizini (pid_ts), backup gerçek içerik, regression→rollback, **optional git commit
  adımı (commit=True)**, _llm_patch (Ollama ile gerçek patch).
- LOCAL_CAPABILITIES: SelfCodeBoundary check **try DIŞINDA** (PermissionError
  rollback akışına girmez — test sözleşmesi), backup pid dizini, rollback,
  backup_dir dönüşü (test sözleşmesi).
- REMOTE_ONLY: commit=False opsiyonel adımı; timestamp'lı yedek adı.
- LOCAL_ONLY: boundary-try-dışı konum; apply dönüşünde backup_dir (test bunu ister).
- CONFLICT: **B/D** — commit adımı yerelde eksik (B); boundary konumu farklı ama
  yerel konum test sözleşmesinin gerektirdiği davranışı üretir (D, yerel korunsun).
- RECOMMENDATION: yerel apply üzerine uzak commit adımı + timestamp eklenmeli.

### FILE: backend/auth.py
- REMOTE_LINES: 66 · LOCAL_LINES: 72
- REMOTE_ONLY: last_seen tabanlı sliding renewal (idle-oturum ömrü)
- LOCAL_ONLY: created üzerine yazma ile sliding; _prune konum farkı
- CONFLICT: **A** — eşdeğer semantik, aynı testler geçer.
- RECOMMENDATION: remote last_seen modeli biraz daha doğru (idle); opsiyonel port.

### FILE: backend/app/automation/gui.py
- REMOTE_LINES: 86 · LOCAL_LINES: 63
- REMOTE_ONLY metotlar: **read_screen_elements, click_text**
- LOCAL_ONLY: find_text_in_elements (static, test sözleşmesi)
- CONFLICT: **B** — read_screen_elements/click_text yerelde YOK.
  ⚠️ DİKKAT (gözlemlenen tuzak): yerel test
  test_read_screen_elements_honest_without_display PASSES çünkü eksik metot
  AttributeError üretir ve hata metni "read_screen_element**s**" içinde "screen"
  geçtiği için kabul anahtar kelimesine takılır. **Yerel suite'nin geçmesi kayıp
  olmadığının kanıtı değildir** (talimat #8'in tam örneği).
- REMOTE_CAPABILITIES: ekran okuma + metne tıklama (OCR/UIA zinciri için temel).
- RECOMMENDATION: iki metot da uzaktan aynen port edilmeli (Wave 4 grounding
  zinciriyle uyumlu); find_text_in_elements korunmalı.

### FILE: backend/app/mesh/dual_node_engine.py
- REMOTE_LINES: 72 · LOCAL_LINES: 75
- REMOTE_CAPABILITIES: handshake caps= beyanları **güvenilmez** işaretlenir
  ([str(c)[:40]][:20] sanitizasyon), rol caps ile ayrı tutulur.
- LOCAL_CAPABILITIES: beyanlar temizlenip declared_caps'te tutulur AMA status
  caps listesine birleştirilir (daha güvenen model).
- CONFLICT: **D** — güven modeli farkı; uzak daha sıkı (LLM/worker çıktısı
  trusted değil ilkesiyle uzak hizalı). Ortak testler ikisini de geçirir.
- RECOMMENDATION: uzak "untrusted declared" semantiği adopt edilmeli; yerel
  status caps birleşimi test gereği korunuyorsa ayrı alanla verilmeli.

### FILE: backend/app/agent/executor.py
- REMOTE_LINES: 26 · LOCAL_LINES: 30
- REMOTE_CAPABILITIES: **app.security.risk.guard() birleşik 5-seviye risk
  motoru** entegrasyonu; audit'te risk=<level>.
- LOCAL_CAPABILITIES: SelfCodeBoundary path-check (write ailesi — VAULT HASARI
  kök nedenini kapatan fix, 9051aae) + risk=high/low kaba seviye.
- CONFLICT: **D** — ikisi birleşebilir: boundary check + risk_guard birlikte.
- RECOMMENDATION: integrate: uzak risk_guard + yerel boundary-check aynı anda
  (yerel check asla düşmemeli — güvenlik regresyonu olur).

### FILE: backend/app/agent/agent.py
- REMOTE_LINES: 280 · LOCAL_LINES: 261
- REMOTE_ONLY: ctor'da **router, world_fn, redact_fn**; _redact() (prompt/tool
  çıktısına vault redaction — "secrets never reach prompts/logs"); world-state
  bağlam enjeksiyonu (WorldModel).
- LOCAL_ONLY: —
- CONFLICT: **B** — üç entegrasyon kancası da yerelde eksik.
- RECOMMENDATION: port edilmeli (secret-kanal ilkesinin prompt tarafı uzakta
  daha eksiksiz).

### FILE: backend/app/core/brain.py
- REMOTE_LINES: 61 · LOCAL_LINES: 61
- REMOTE_ONLY: ask/chat'te **model=** parametresi (router/devirgeme desteği)
- CONFLICT: **B** — yerelde sabit model. ROUTER_CASES golden testleri uzak
  router'ını hedefler; yerelde router testleri? (tests birebir aynı → yerel suite
  bu testleri geçtiğine göre yereldeki router impl. başka yerde; model= yine de
  port edilmeli.)
- RECOMMENDATION: model= parametresi eklenmeli (additive, geriye uyumlu).

### FILE: backend/app/memory/sqlite_memory.py
- REMOTE_LINES: 73 · LOCAL_LINES: 69 · REMOTE_ONLY: __init__(redact_fn=) —
  yazmada vault redaction. KATEGORİ **B**. RECOMMENDATION: port.

### FILE: backend/app/security/audit.py
- REMOTE_LINES: 21 · LOCAL_LINES: 18 · REMOTE_ONLY: __init__(extra_values_fn=)
  — vault.all_values ile dinamik redaction tohumlama (pattern'e ek). KATEGORİ
  **B**. LOCAL: her write()'ta pattern redact (sabit). RECOMMENDATION: port
  (ikisi birlikte: pattern + concrete).

### FILE: backend/app/voice/voice_stack_v2.py
- REMOTE_LINES: 287 · LOCAL_LINES: 269
- REMOTE_ONLY: **WakeWordManager kablolaması** — wake duyulmadan STT'e
  girilmez; wake audio'da doğrulandıysa metin temizliği. (wake.py İKİ tarafta
  da birebir aynı mevcut; yerelde sadece kablolama eksik.)
- KATEGORİ **B**. RECOMMENDATION: port (Wave 4 wake_vad modülüyle çakışmaz,
  tamamlayıcı).

### FILE: backend/app/core/runtime.py
- REMOTE_LINES: 341 · LOCAL_LINES: 253 (üst-düzey fonksiyonlar aynı; fark gövdede)
- REMOTE_ONLY ctor kablolaması: CredentialVault(audit=None); WakeWordManager(vault=);
  FilesystemSandbox(settings, workspace_root=cwd) + sandboxed_* araçları;
  Memory(redact_fn=vault.redact); AuditLog(extra_values_fn=vault.all_values);
  lazy gerçek Playwright BrowserAgent.
- KATEGORİ **B/D** — yerel runtime bu güvenlik kablolamalarının büyük kısmını
  yapmıyor. RECOMMENDATION: entegrasyonun kalbi burası; uzak ctor kablolaması
  yerel Wave-4 runtime ile birleştirilmeli.

### FILE: backend/server.py
- REMOTE_LINES: 1616 · LOCAL_LINES: 1299
- REMOTE_ONLY endpoint'ler (17): api_vault_set/list/delete (+_vault helper),
  api_tasks_create/list/get/approve/cancel/pause, api_skills, api_world,
  api_wake_status, api_models_health, api_connectors_health, api_mesh_heartbeat,
  get_system_stats_dict.
- LOCAL_ONLY: api_debug_runtime, api_debug_vision_selftest (+ multimodal
  endpoint zenginliği: 42 vs 30 multimodal/fusion/vision/v voice referansı).
- KATEGORİ: uzak endpoint'ler **B**; yerel multimodal katman yerelde kalır.
- CONFLICT: 29 dosyanın en büyüğü; satır bazlı çakışma kaçınılmaz.
- RECOMMENDATION: merge'de uzak endpoint blokları + yerel multimodal bloklar
  birleştrilmeli; approval akışı (api_tasks_approve ↔ agent.request_approval)
  uzakta uçtan-uca bağlı — yerelde agent kapısı var ama server kablosuz.

### FILE: backend/app/iot/iot_nexus.py
- REMOTE_LINES: 190 · LOCAL_LINES: 192 · REMOTE_ONLY: ULTRON_HA_URL env fallback;
  list() tek-geçiş implementasyon. Davranış eşdeğer (aynı testler). KATEGORİ **A**
  (+env fallback port edilebilir).

### FILE: backend/app/code_agent/code_agent.py
- REMOTE_LINES: 72 · LOCAL_LINES: 70 · REMOTE_ONLY: yazma yolunda
  **SelfCodeBoundary.check** (güvenlik çekirdeği self-patch'e kapalı).
  KATEGORİ **B — GÜVENLİK AÇIĞI (yerelde eksik)**. RECOMMENDATION: acil port
  (tek satır katmanı; executor'daki yerel fix'ten bağımsız olarak gerekli).

### FILE: backend/app/security/sovereign_privacy.py
- REMOTE_LINES: 73 · LOCAL_LINES: 80 · LOCAL_ONLY: _tts_is_local + CLOUD_TTS
  kümesi (bulut motoru asla 'local' denmez). KATEGORİ **A** (yerel daha dürüst;
  uzakta eşdeğer davranış test tarafından zorlanıyor). RECOMMENDATION: yerel kalsın.

### FILE: backend/app/tasks/engine.py
- REMOTE_LINES: 362 · LOCAL_LINES: 1007 · REMOTE_ONLY: — · LOCAL_ONLY:
  BrainUnavailable, _HardDeadlineExceeded, step_i_hint (+ poison/DLQ, bağımlılık
  motoru Wave 2/3 katmanları). KATEGORİ **A — yerel üst küme**.

## 2. MEKANİK/KONFİG DOSYALARI (29 M'den kalanlar)

| FILE | R/L satır | REMOTE_ONLY | KATEGORİ | REC |
|---|---|---|---|---|
| .gitignore | 8/6 | ek desenler | C | incele, birleştir |
| ARCHITECTURE.md | 168/109 | Foundation mimari anlatımı | C(dok) | uzak temel + Wave 4 bölümü eklenmeli |
| backend/config/settings.json | 114/85 | **routing tablosu** (coder/vision/fast/general), filesystem sandbox kökleri, timeout/retry_backoff | **B** | port (sandbox kökleri güvenlik ile ilgili) |
| backend/requirements.txt | 18/9 | Pillow, pyautogui, pygetwindow, pytesseract, cryptography, playwright, pvporcupine, openwakeword | **B (release packaging)** | venv'de kurulu ama dosyada YOK — release kırığı |
| frontend*/package*.json | ≈ | sürüm kaymaları | C | uzak sürümler temel |
| scripts/install_windows.bat | 49/44 | kurulum adımları | C | uzak temel |

## 3. YALNIZ YERELDE OLAN 75 DOSYA (LOCAL-ONLY)

orchestr/ (11 py: council, scheduler, budgets, worker, tokens…), events/ (bus,
schema, integration), memory v3 (intelligence, layers, retrieval, store_v3),
**multimodal/ (13 py — tüm Wave 4)**, world/ (entities, store),
observability/trace.py, tasks/scheduler.py, bench_wave1/2.py, WAVE2/3/4_AUDIT.md,
**13 × test_wave4_*.py (180 test)**, scripts/check_deps.py, diğer testler.
KATEGORİ: uzakta hiç yok → **D (entegrasyon değil; merge'de otomatik korunur)**.

## 4. KATEGORİ ÖZETİ (uzak-yalnız yetenekler)

- **A (yerelde korunuyor — eşdeğer)**: monitor spike-suppression, auth TTL, mesh
  temel caps, iot is_simulated/vault token, sovereign tts-local dürüstlüğü,
  codegen backup/rollback/boundary, tasks engine çekirdeği.
- **B (yerelde EKSİK)**: vault all_values/health/audit/degrade; vault HTTP API'si
  (3 endpoint); tasks lifecycle API'si (6 endpoint); skills/world/wake-status/
  models-health/connectors-health/mesh-heartbeat API'leri; gui read_screen_elements
  + click_text; agent router/world_fn/redact_fn; brain model=; sqlite_memory
  redact_fn; audit extra_values_fn; voice_stack wake kablolama; runtime vault/
  sandbox/wake BrowserAgent kablolaması; code_agent SelfCodeBoundary (güvenlik);
  settings routing/sandbox kökleri; requirements 8 bağımlılık kaydı; codegen
  commit adımı; system_tools zengin alanlar; ARCHITECTURE.md içeriği.
- **C (ölü/eski)**: package-lock sürüm kaymaları, .gitignore desenleri (küçük).
- **D (uyumsuz — entegrasyon gerekli)**: vault imzaları (list_names/set dönüşü,
  default dizin), executor risk-guard vs boundary (ikisi birleşmeli), mesh
  declared-caps güven modeli (uzak: untrusted), system_tools imza sırası,
  auth last_seen modeli, codegen boundary konumu (yerel test-gerektir).

## 5. POTANSİEL KAYIPLAR

- Force-push (YASAK) yapılırsa uzak zincirin B/D envanteri kaybolur: vault
  sağlıksız API + tasks/skills/world/wake API'leri + gui ekran-okuma +
  agent redaction kablolaması + code_agent güvenlik satırı + packaging.
- Merge uzak→yerel yapılırsa: 75 yerel dosya TEHDİT ALTINDA DEĞİL (uzakta
  yoklar); çakışma yalnız 29 M dosyada; asıl dikkat executor/vault/mesh/
  server'da işaretlenen D noktaları.

## 6. ÖNERİLEN ENTEGRASYON PLANı (onay bekler — hiçbiri uygulanmadı)

1. Kullanıcı onayıyla `git merge 54f5cdb` (geçmiş yeniden yazılmaz; force gerekmez).
2. 29 dosyada çözüm ilkesi: **uzak zengin implementations temel; yerel güvenlik
   fixları (executor boundary, sovereign dürüstlük, codegen try-dışı boundary)
   ve Wave 4 bağımlılıkları korunur**; D imzalarında uzak forma geçilir (testler
   iki formu da geçiyor, tüketiciler uzak forma göre).
3. gui read_screen_elements/click_text + code_agent boundary + vault
   all_values/health + audit extra_values_fn + sqlite redact_fn + agent
   redact_fn/world_fn/router + brain model= + voice wake kablolama + runtime
   vault/sandbox kablolama + server 17 endpoint: birleşik ağaçta doğrulanır.
4. FULL suite + Wave 4 + golden yeniden koşulur (hedef: ≥631P/4S + 7/7 + 178P/2S).
5. requirements/settings/ARCHITECTURE release kalitesine çıkarılır.
6. Normal push (merge sonrası fast-forward olur, force YASAK zaten).

## 7. YÖNTEM NOTLARI (şeffaflık)

- Tüm satır/fonksiyon sayıları `git show FETCH_HEAD:<path>` / `git show HEAD:<path>`
  üzerinden üretildi; hiçbiri tahmin değil.
- "Yerel suite geçiyor" uzak içeriğin gereksiz olduğu anlamına GELMEZ — §1 gui.py
  örneği bunun kanıtı (AttributeError metnindeki "screen" kelimesi testi şansla
  geçiriyor). Talimat #8 aynen uygulandı.
- Bu rapor sırasında hiçbir üretim dosyası değiştirilmedi, commit/push yapılmadı.

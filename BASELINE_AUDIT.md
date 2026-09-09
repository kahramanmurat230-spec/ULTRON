# BASELINE AUDIT — Gerçek Durum Tespiti (Implementation Öncesi)

Tarih: 2026-09-09 · Dal: `arena/01a08707-ultron` · HEAD: `f2924287`
Metot: kod incelemesi + gerçek test koşumu + gerçek server boot + API testleri.
**Hiçbir üretim kodu değiştirilmedi, commit yapılmadı.** (Bu dosya istisnadır — rapor.)

---

## 1. Ortam envanteri (bu sandbox)

| Kaynak | Durum |
|---|---|
| CPU / RAM | 2 çekirdek / 4 GB |
| Ollama | YOK (ollama.com ve indirme bloklu) → canlı LLM testi imkânsız |
| Gerçek tarayıcı (Chromium) | YOK (cdn.playwright.dev bloklu) → E2E honest-skip |
| tesseract binary | YOK (debian repo + GitHub release asset'leri bloklu) → OCR honest-skip |
| Mikrofon / ekran / kamera / BT | YOK (headless) |
| PicoVoice AccessKey | YOK (kullanıcı anahtarı) |
| Ağ | pypi + npm + github.com AÇIK; CDN/debian/ollama/release-asset BLOKLU |
| Kurulan Python paketleri | pytest, aiohttp, psutil, Pillow, cryptography, numpy, pypdf, pvporcupine, pytesseract, edge-tts, playwright(paket), pyautogui, pygetwindow |

Sonuç: referans koşu ortamı (WAVE4 audit'indeki venv) birebir eşlendi; bu ortamda
doğrulanamayacak her şey aşağıda dürüstçe OPEN/UNVERIFIED işaretlenmiştir.

## 2. Gerçek baseline test sonuçları

```
cd backend && python -m pytest -q tests/
→ 952 passed, 0 failed, 5 skipped  (~47 s)
→ golden eval: tests/test_eval_golden.py → 36/36 (%100)
```

5 skip (hepsi dürüst ortam skip'i):
1. gerçek tarayıcı E2E (chromium binary yok)
2. deterministic parser tanıyamadı (tek vaka)
3. PICOVOICE_ACCESS_KEY yok
4. gerçek cihaz E2E (Windows+mic+screen+browser+ocr gerekli)
5. OCR latency benchmark (tesseract binary yok)

Not: çıplak ortamda (pvporcupine kurulu değilken) `test_wake_word.py::test_porcupine_unavailable_without_key`
1 kez düştü — kod hatası değil, ortam-duyarlı test (paket kurulu olunca geçiyor).

## 3. Gerçek runtime kanıtı (server boot)

- `python server.py` → :8000 ayakta; `/api/system` gerçek psutil verisi, `/api/ai`
  connected:false (dürüüst), `/api/tools` browser=UNAVAILABLE + fs/terminal=READY,
  `/api/memory`, `/api/world` canlı.
- `/api/tasks` POST → Supervisor gerçek koştu: kod analizi 364 dosya/187 issue +
  verification + final report → COMPLETED (bütçe/timeout içinde).
- Onay sınırı: tehlikeli komut → sunucu-taraflı pending task (TTL 15 dk) → onay
  sonrası çalışır. Client `approved=true` asla güvenilmiyor (kod + canlı test).
- TTS: backend=None + RuntimeError (sahte ses YOK). Voice girişi: donanım YOK.

## 4. P0 / P1 / P2 listesi

### P0 — güvenlik / kırık API

| # | Bulgu | Kanıt |
|---|---|---|
| P0-1 | **Shell policy (Level 52/53) Linux yıkıcı komutlarını BLOKLAMIYOR**: `rm -rf /`, `sudo rm -rf /`, `mkfs.ext4 /dev/sda`, `dd if=/dev/zero of=/dev/sda`, fork bomb `:(){ :\|:& };:`, `chmod -R 777 /` → hepsi `risk=standard, allowed=True`. `_BLOCKED` desenleri yalnız Windows (format/diskpart/del /s/reg/sc/netsh...). `test_shell_policy.py` de yalnız Windows desenlerini test ediyor → "destructive blocked" iddiası Linux için YANLIŞ. | canlı: onaylı `...rm -rf /...` komutu `/bin/sh`'e verildi (`de: not found` şans eseri kurtardı); birim: `ShellPolicy().evaluate('rm -rf /') → allowed=True` |
| P0-2 | **`GET /api/tasks` → 500**: `api_tasks_list` `engine.list(status=..., limit=...)` çağırıyor, `TaskEngine.list(self, limit=100)` `status` almıyor → TypeError. Görev listesi UI paneli kırık. | canlı curl 500 + birim repro |
| P0-3 | **`POST /api/tasks/{id}/pause` → 500**: `engine.pause` diye bir metot YOK (`cancel` `app/tasks/__init__.py` monkeypatch'iyle var, `pause` hiç yazılmamış). | canlı curl 500 + `hasattr(engine,'pause')==False` |

### P1 — kopuk zincir / yanlış davranış

| # | Bulgu | Kanıt |
|---|---|---|
| P1-1 | **Türkçe ek ayrıştırma hatası**: "terminalde X çalıştır" → komut `de X çalıştır` olarak çıkarılıyor (`backend/agent.py:81` regex'i `terminal` kökünü suffix'ten ayırmıyor). Yanlış komut çalışır / tehlikeli komut kalıbı bozulur. | canlı aktivite logu |
| P1-2 | **Outcome-learning döngüsü üretimde kapalı**: yazıcı `outcome_learning.py` hiçbir üretim yoluna bağlı değil; okuyucu `OutcomePlanningContext` (Planner içinde) hep boş bulur. Level 37–50 fiilen uyuyor. | import grafiği |
| P1-3 | **Wave 2–5 katmanları üretim yoluna bağlı DEĞİL** (yalnız kendi testleri): `orchestr/*` (Wave 3 DAG supervisor/council/token), `multimodal/*` (Wave 4 runtime; yalnız `camera_adapter`+`pdf_workspace` tools.py'de), `cognitive/*` (Wave 5 tamamen), `events/bus.py`, `memory/store_v3.py`, `world/store.py`, `tasks/scheduler.py`. Üretim zinciri = V15.1 aiohttp + bridge + V16 runtime + TaskEngine + keyword-Supervisor + WorldModel. | import grafiği (tests hariç) |
| P1-4 | **Üretim Supervisor'ı genel amaçlı planlayıcı DEĞİL**: `SupervisorAgent.plan()` keyword-tabanlı, 5 sabit worker (code_analysis/tests/diagnostic/verification/report) — "proje denetim hattı". Gerçek genel planlayıcı (`Planner.make_plan` LLM→JSON plan→capability doğrulama→HybridPlanExecutor, 60 sn deadline) yalnız sohbet akışında; kalıcı TaskEngine yolunda değil. | kod incelemesi |
| P1-5 | `/api/audit` V16 bridge yokken boş döner — V15.1 activity/event kanalı orada görünmez (iki ayrı denetim sistemi, UI paneli yanıltıcı boş). | canlı curl `[]` |

### P2 — kalite / sağlamlık

| # | Bulgu |
|---|---|
| P2-1 | `app/automation/gui.py` içinde `find_text_in_elements` İKİ KEZ tanımlı (ikincisi birinciyi gölgeliyor; skorlama semantiği farklı). |
| P2-2 | `_pyautogui` yalnız `(ImportError, SystemExit)` yakalıyor; headless Linux'ta pyautogui kuruluysa `KeyError('DISPLAY')` yakalanmaz → dürüst-degrade yolu o konfigürasyonda kırılır (Windows hedefi etkilenmez). |
| P2-3 | `api_tasks_approve` denetim kaydına onaylayanın kimliğini yazmıyor (yalnız task id). |
| P2-4 | WAITING_APPROVAL→approve→tekrar NeedsApproval döngüsü kullanıcı her onayladığında yeniden koşar (denemesiz/sınırsız manuel tur; otomatik loop yok). |
| P2-5 | README "COMPLETE" etiketlerinin bir kısmı üretim bağlantısı olmayan katmanlar için kullanılmış (ör. Supervisor "5 gerçek worker" → keyword planlayıcı; Skills/Connectors "COMPLETE" → canlı kimlik yok). |

## 5. Özellik özellik gerçek durum (A–X)

| | Bileşen | Durum | Kanıt/Not |
|---|---|---|---|
| A | Brain/Ollama | **PARTIAL** | Kod gerçek (sovereign, bulut bloklu, unit testli); canlı LLM bu ortamda doğrulanamaz (Ollama yok). |
| B | Model Router | **VERIFIED (mantık)** | test_model_router geçti; capability/fallback/backoff kodda; canlı model listesi doğrulanamadı. |
| C | Planner/Intent | **VERIFIED + sınırlı** | intent.py keyword (tasarım gereği, dürüst); Planner LLM-tabanlı gerçek + capability-aware doğrulama (canlı: Ollama gerek). |
| D | Supervisor | **WORKS ama keyword** | Canlı koştu (364 dosya analizi); plan() 5 sabit worker — genel amaçlı değil (P1-4). |
| E | TaskEngine | **VERIFIED** | journal/WAL replay/dead-letter/checkpoint/bütçe/deadline/requeue testleri geçti + canlı create→complete. `pause` endpoint yok (P0-3), `list` kırık (P0-2). |
| F | Tool Registry | **VERIFIED** | Gerçek availability check'leri; 60+ araç kayıtlı; V15.1 status katmanı canlı. |
| G | Executor | **VERIFIED** | risk.guard + permissions + SelfCodeBoundary + undo-journal; hata asla success'e çevrilmiyor. |
| H | Risk/Approval | **VERIFIED + P0-1 deliği** | Client flag güvenilmiyor (canlı); CRITICAL onay kapılı; güvenlik çekirdeği self-coding'e mimari kapalı (onaylı bile). **Ama** shell policy Linux yıkıcı komutlarını görmüyor (P0-1). |
| I | Browser Agent | **UNVERIFIED (canlı)** | Kod gerçek (Playwright worker-thread, verify, çoklu motor fallback, ULTRON_BROWSER_EXECUTABLE); binary yok → honest skip. |
| J | Computer Use | **UNVERIFIED (canlı)** | Kod gerçek (pyautogui + OCR-anchored click_text: stale-frame RED + eylem sonrası görsel verify); headless'ta cihaz yok. |
| K | Vision/OCR | **PARTIAL** | Foundation analiz gerçek (PIL, testli); OCR binary yok; LLaVA Ollama gerek. Vision→Computer-Use doğrulama zinciri `click_text` içinde gerçek. |
| L | Voice pipeline | **PARTIAL** | Engine'ler gerçek (porcupine/oww wake, energy/webrtc/silero VAD, faster-whisper STT, piper/espeak TTS, barge-in/EchoGuard) + state machine testleri; donanım yok → canlı UNVERIFIED, dürüst unavailable. |
| M | Memory | **VERIFIED** | SQLite + TF-IDF semantic ajan prompt'una bağlı; V3 store test-only. |
| N | World Model | **VERIFIED** | Level 51 sertleştirilmiş; `world_fn` → ajan bağlamına bağlı (server:1427); WorldStore test-only. |
| O | Self-coding | **VERIFIED (pipeline)** | BACKUP→APPLY→TEST→COMMIT/ROLLBACK gerçek + güvenlik çekirdeği ön-doğrulamayla RED; LLM patch canlı = Ollama gerek. Şablonlar dürüst placeholder (NotImplementedError). |
| P | Skills | **VERIFIED (mantık)** | Risk kayıtlı araçlardan hesaplanır; dangerous adım onay kapılı; canlı skill envanteri sınırlı. |
| Q | Connectors | **PARTIAL** | Kod + protokol testleri gerçek; canlı kimlikler vault'ta yok → canlı UNVERIFIED. |
| R | Proactive/Presence | **VERIFIED (mantık)** | Spike-suppression+cooldown+hysteresis testli; presence gerçek kaynaklı; canlı donanım yönleri UNVERIFIED. |
| S | IoT | **PARTIAL** | is_simulated dürüst etiketli; gerçek cihaz yok. |
| T | Mesh | **VERIFIED (mantık)** | Pairing/sealed-rules/heartbeat testli; PC↔mobil canlı UNVERIFIED. |
| U | Auth | **VERIFIED** | Token+TTL+rate-limit+middleware; localhost dışı bind auth'suz reddedilir. |
| V | Frontend | **BUILD OK** | `vite build` 4.34 s; API katmanı canlı server ile uyumlu (tam UI E2E bu ortamda yapılmadı). |
| W | Mobile | **BUILD OK** | 6 sayfa, `vite build` 1.32 s. |
| X | Desktop/Windows | **OPEN** | Electron+NSIS+PyInstaller yapılandırması mevcut; Linux'ta Windows paketi doğrulanamaz (kural: Linux'ta Windows PASS sayılmaz). |

## 6. Hedef zincirin mevcut kapsanması

`User → Perception → Brain → Planner → Supervisor → Task Engine → Risk/Approval → Tool Registry → Executor → Action → Observation → Verification → Recovery → Replanning → Memory/World → Response`

- **Canlı üretim zinciri (bugün)**: User(chat/WS) → intent keyword/LLM tool-call → Agent → Risk/Approval (sunucu-taraflı) → Tool Registry → Executor → Action → Observation → Response; Memory+World ajan döngüsünde; TaskEngine kalıcılık/journal/WAL/dead-letter; Supervisor sınırlı (keyword, 5 worker); Replanning sınırlı (1 kez worker retry + alt worker).
- **Kodda-var-ama-bağlı-değil**: genel amaçlı DAG supervisor (Wave 3), multimodal fusion + aksiyon-doğrulama (Wave 4), bilişsel otonomi hedefi (Wave 5) → bunlar entegrasyonla zincire katılabilir (P1-3).

## 7. İlk uygulanacak düzeltme (öneri sırası)

1. **P0-1**: `shell_policy.py` `_BLOCKED`'e çapraz-platform yıkıcı desenler ekle
   (`rm` recursive/`-rf`, `mkfs*`, `dd of=/dev/*`, `wipefs`, fork-bomb, `chmod/chown -R` kök hedefli,
   `> /dev/sd*`, `kill -9 -1`, `shutdown` zaten var…) + `test_shell_policy.py`'ye Linux vektörleri.
   Küçük, cerrahi, gerçek güvenlik deliği kapanır.
2. **P0-2**: `TaskEngine.list(limit, status=None)` imzası + filtre (server'ın beklediği şekil).
3. **P0-3**: `TaskEngine.pause()` (PAUSED geçişi zaten FSM'de tanımlı) — `app/tasks/__init__.py`'ye `cancel` gibi veya engine'e.
4. **P1-1**: terminal komut ayrıştırıcısına Türkçe ek toleransı (`terminalde|terminalde|terminale` → komut ayrımı).

Her düzeltmeden sonra: tam suite (hedef ≥952P/5S) + golden 36/36 + server boot testi.

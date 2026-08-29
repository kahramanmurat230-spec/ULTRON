# ULTRON V15 — Architecture

## Workspace analysis (2026-08-26)
The workspace contained **no pre-existing ULTRON project** (only the reference image in
`uploads/`). Therefore nothing existing was deleted or rewritten; the full stack was built
from scratch to the spec, with a real backend so that no UI element ever reports a fake
success.

## Topology
```
Browser (React 19 + three.js)  --/api,/ws-->  Vite dev server (5173, proxy)  -->  Python backend (aiohttp, 8000)
                                                                                      ├─ telemetry sampler (psutil, /proc)
                                                                                      ├─ ollama prober (live HTTP)
                                                                                      ├─ tool registry (real checks + subprocess exec)
                                                                                      ├─ agent state machine
                                                                                      └─ memory (session RAM + persistent JSON)
```

## Real-data sources
| UI element            | Source                                                              |
|-----------------------|---------------------------------------------------------------------|
| CPU / RAM / DISK / NET| `psutil` sampling loop (1 s)                                        |
| GPU / VRAM            | `nvidia-smi` if present, else `null` → UI shows **N/A**             |
| Temperatures          | `psutil.sensors_temperatures()` + `/sys/class/thermal`, else **N/A**|
| Ollama / models       | live probe of `$ULTRON_OLLAMA_HOST/api/tags` (2.5 s timeout)        |
| Agent status          | backend state machine, streamed over WS (single source of truth)    |
| Tools status          | real availability checks (`which`, writability, DISPLAY, client report for voice, ollama vision model) |
| Recent activity       | backend event log (tool runs, agent runs, checks)                   |
| Notifications         | real events only: startup, ollama transitions, tool errors, temps   |
| Memory matrix         | session deque + `backend/data/memory.json` (real file, real size)   |
| Core stability %      | measured render FPS + WS link state (frontend-measured, honest)     |
| Voice waveform        | live microphone RMS via `AnalyserNode`; flat baseline when idle     |

## Agent state machine
`IDLE → LISTENING → THINKING → PLANNING → EXECUTING → VERIFYING → DONE | ERROR → (auto) IDLE`
Intents (TR+EN): open browser / search, screenshot, system check, clear memory,
remember X, run <cmd>, time, model status. Every transition is broadcast; UI never
self-assigns states.

## Service layer (frontend `src/lib/api.ts`)
getSystemStatus, getAIStatus, getAgentStatus, getTools, getMemoryStatus,
getRecentActivity, getNotifications, executeCommand, sendVoiceCommand,
captureScreen, openBrowser — all with timeout + error surfacing.

## Error handling
Every fetch/WS/tool call: try/catch + timeout. Backend down → header shows OFFLINE and
panels freeze at last value with a stale badge; Ollama down → OFFLINE; tool failure →
ERROR state + notification. No frozen UI: WS reconnects with backoff.

## Performance
- three.js: capped pixelRatio (≤1.75), ~700 particles, additive sprites instead of bloom,
  single RAF, full dispose on unmount.
- React: external store + `useSyncExternalStore`, selectors return stable refs;
  telemetry updates replace top-level keys only.
- Backend: single sampler task; broadcasts are fire-and-forget with dead-client pruning.

## V16 backend integration (v15.2)
V16 source (`app/` tree + `config/settings.json`) integrated as backend capabilities
behind the existing V15.1 aiohttp/WebSocket layer. PySide6 desktop UI, `main.py`,
`ultron_core/`, `data/`, caches were NOT copied.

- Bridge (`backend/bridge.py`): tier-2 routing — V15.1 keyword intents stay tier-1;
  unmatched commands (GENERAL_CONVERSATION) go to V16 `Agent.handle`
  (deterministic routes → Ollama native tool-calling) via `asyncio.to_thread`.
- Unified agent events: bridge maps V16 execution onto the existing WS state
  machine (PLANNING/EXECUTING/VERIFYING/DONE/ERROR) — UI never self-assigns states.
- Memory: SQLite `Memory` (+`count()`) surfaces as `v16_total` in `/api/memory`;
  `SemanticMemory` feeds V16 context. V15.1 session/persistent JSON untouched.
- Security: `PermissionManager` gate preserved & completed — `settings.json`
  `require_confirmation_for` now covers registered dangerous tools
  (write_text, gui_*, apply_code_patch); `self_repair(apply=True)` honours the
  per-request approval flag (`runtime.approved`, set by bridge).
  UI: `/api/agent/command {approved:true}` + "Approve & Re-run" button in Command Center.
- Audit: `AuditLog` → `/api/audit` → AUDIT modal.
- Proactive: `ProactiveMonitor` → real UI notifications (verified live: CPU %100 warning).
- Tools panel: 7 V16 capability rows with real availability checks
  (importlib/platform/ollama) — UNAVAILABLE when deps missing, never faked.
- Backend TTS/STT (`app/voice`) untouched; frontend espeak lip-sync unchanged.
  Optional `/api/voice/live` endpoint gated on sounddevice+faster-whisper presence.
- Ollama offline → conversation ends in ERROR with the real backend message
  (verified); with Ollama up on the user's machine the same path answers via LLM.

## V17 JARVIS capability layer
- Code Intelligence (`app/code_intel/analyzer.py`): stdlib static analysis
  (structure, deps, bugs, security, unused, duplicates, TODO, arch violations).
- CodeGen pipeline (`codegen.py`): REQUEST→…→APPROVAL→APPLY with difflib diffs,
  auto-tests (`test_runner.py`) and automatic rollback on failure; chat-driven
  ("Onaylıyorum"/"Reddet") + UI drawer APPROVALS (Approve/Reject/View Diff).
- Agent state extended: WAITING_APPROVAL (backend ladder, frontend, 3D core).
- Orchestration lite: "A ve B" compound commands run as sequential routed steps.
- Memory categories (PROFILE/PREFERENCE/PROJECT/DEVICE/TASK/FACT/IMPORTANT) +
  management API/UI (search/add/delete/clear), sensitive-data save refusal.
- Continuous voice: wake-word ULTRON arm/disarm loop (default OFF, mic state visible).
- TTS chain: PIPER slot (user bundle+model, license-checked) → ESPEAK wasm → system;
  real rate (playbackRate) & volume (GainNode) controls; lip-sync amplitude unchanged.
- Proactive notifier with per-key cooldown (no spam); auth/session layer for
  shared-brain clients (ULTRON_AUTH=1, tokens server-side, no hardcoded secrets).
- New GUI tools (double/right click, scroll) registered as dangerous (confirmation).
- UI drawer: CODE / MEMORY / AUTOMATION / APPROVALS / AUDIT / VOICE tabs.

## V17.1 vision fix (screenshot ≠ analysis)
- `bridge.run_vision`: THINKING→PLANNING→EXECUTING SCREENSHOT→verify PNG→
  EXECUTING VISION (llava/vision auto-detect, default llava:7b)→VERIFYING→DONE.
- Vision requests bypass tier-1 keyword intents (`agent.vision_check` hook) and
  the "ve" orchestration split, so "…analiz et ve … anlat" stays one flow.
- `VisionLLM.resolve_model`: configured model if installed, else first installed
  llava/vision model, else None → real ERROR/UNAVAILABLE (never fabricated).
- Explicit path support: "Bu ekran görüntüsünü analiz et: <png>" skips capture.
- No vision model → labeled real-OCR fallback if pytesseract exists, else ERROR.

---

# JARVIS FOUNDATION (2026-08-29)

V15 tabanının üzerine eklenen foundation katmanları (tümü testli, mock'suz):

## Çekirdek döngü
- **Brain + Model Router** (`app/core/`): Ollama adapter (sovereign-local, cloud bloklu);
  capability registry (CODING/VISION/FAST/GENERAL), conservative routing, fallback +
  backoff, ask_json structured output, fit_messages context bütçesi, health.
- **Supervisor + Task Engine** (`app/agent/supervisor.py`, `app/tasks/engine.py`):
  THINK→PLAN→ACT→OBSERVE→VERIFY→REPLAN→RECOVER→COMPLETE; 8 statuslü kalıcı görevler
  (SQLite), budget'ler (iteration/timeout/tool/retry/step), WAITING_APPROVAL→approve→resume,
  boot recovery. Sonsuz loop yok.
- **World Model** (`app/world/model.py`): presence/screen/task/system/apps/files/iot/events
  tek güncel context (1200 char cap) — memory geçmiş, world şimdi.

## Güvenlik çekirdeği
- **Credential Vault** (`app/security/vault.py`): Fernet at-rest, env/keyfile anahtar,
  HTTP üzerinden değer okunamaz, redaction pattern+somut değer.
- **Filesystem Sandbox** (`app/security/sandbox.py`): kök bazlı okuma/yazma, traversal/
  UNC/symlink/system-dizin bloğu; file tools + codegen sandbox'lı.
- **Risk Engine** (`app/security/risk.py`): SAFE/LOW/MEDIUM/HIGH/CRITICAL; CRITICAL
  (shell/delete/install/ayar/process-kill) sunucu-doğrulanmış onay ister; güvenlik çekirdeği
  self-coding'e mimari olarak kapalı (onaylı olsa bile).
- **Audit + redaction + rate limit + session TTL + server-side approval** (client
  `approved=true`'ya güvenilmez).

## Yetenek katmanları
- **Voice**: gerçek wake engine'leri (Porcupine AccessKey'li / openWakeWord modelli;
  transcript araması wake DEĞİLDİR) → VAD (energy/webrtc/silero) → faster-whisper STT →
  edge-tts + barge-in. Engine yoksa dürüst unavailable + manuel tetik.
- **Vision**: LLM'siz foundation (parlaklık/edge/renk/diff) + OCR elements (kutu+güven) +
  LLaVA köprüsü + stale-screenshot koruması + aksiyon sonrası görsel doğrulama.
- **Computer Use**: pyautogui/pygetwindow + OCR-anchored click_text + süreç envanteri
  (process_kill CRITICAL, self-kill yasak) + startup görünümü.
- **Browser Agent**: Playwright; SAFE okuma / onaylı eylem; OBSERVE→VERIFY; gerçek
  Chromium E2E geçti (ULTRON_BROWSER_EXECUTABLE ile harici motor da destekli).
- **Skills**: JSON manifest (id/version/permissions/risk/input-output schema/timeout/
  verification); risk kayıt bayraklarından hesaplanır; dangerous adım onay kapısından.
- **Connectors**: hava (OWM+wttr), takvim (.ics+Outlook COM), e-posta (SMTP/IMAP gerçeği,
  kimlik vault'tan, send=HIGH onaylı).
- **Proactive/Presence/IoT**: sustained-sample alarm (spike yok sayılır) + cooldown +
  hysteresis; çok kaynaklı presence (wifi TCP reachability gerçek, kamera asla simüle
  edilmez); IoT cihazları is_simulated etiketli, driver başarısızlığı sahte başarı yok.
- **Mesh**: PC↔mobil (token veya X-Mesh-Key pairing), master_rules SHA-sealed (asla merge
  edilmez), heartbeat TTL, capability declaration.

## Self-coding
`codegen.py`: DETECT→ANALYZE→PROPOSE→DIFF→APPROVAL→**BACKUP**→APPLY→TEST→REGRESSION→
VERIFY→COMMIT(ops.)|ROLLBACK. Güvenlik çekirdeği dosyaları ön-doğrulamayla reddedilir.

## Test & Değerlendirme
23 test dosyası (174+ test) + `tests/test_eval_golden.py` (36 golden, 100%).
Kategoriler: approval, redaction, sandbox+vault, risk, task engine, supervisor, world,
model router, browser (gerçek E2E), skills, connectors (gerçek HTTP/SMTP/IMAP), email,
vision, computer-use, process, proactive+IoT, mesh, wake, VAD/barge-in, presence,
codegen pipeline, auth+rate limit, eval goldens.

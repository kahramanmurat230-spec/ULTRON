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

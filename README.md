# ULTRON — JARVIS Foundation

Yerel-öncelikli (sovereign) kişisel yapay zekâ asistanı: tek kullanıcıya ("Boss") hizmet veren,
Windows hedefli, tarayıcı + mobil arayüzü olan gerçek bir agent çekirdeği.

> **Yasak olan şey açıkça yoktur:** mock/stub ile "çalışıyor" gösterilen özellik yoktur.
> Çalışamayan her bileşen kendini `available=false` + gerekçe ile raporlar.

## Bileşen haritası

| Katman | Yer | Durum |
|---|---|---|
| AI Brain (Ollama, sovereign-local) | `backend/app/core/brain.py` | COMPLETE |
| Model Router (capability/fallback/backoff) | `backend/app/core/model_router.py` | COMPLETE |
| Intent | `backend/app/core/intent.py` | keyword tabanlı |
| Supervisor (5 gerçek worker + replan) | `backend/app/agent/supervisor.py` | COMPLETE |
| Task Engine (budget/checkpoint/persistent) | `backend/app/tasks/engine.py` | COMPLETE |
| World Model (anlık durum ≠ memory) | `backend/app/world/model.py` | COMPLETE |
| Memory (SQLite + TF-IDF semantic) | `backend/app/memory/` | COMPLETE |
| Voice: wake (Porcupine/openWakeWord) → VAD (energy/webrtc/silero) → STT (faster-whisper) → TTS (edge-tts) + barge-in | `backend/app/voice/` | engine'ler gerçek; anahtar/model/vezin donanım gerektirir |
| Vision: LLM'siz analiz + OCR + LLaVA köprüsü | `backend/app/vision/` | COMPLETE (llava kurulumu ile) |
| Computer Use: pyautogui + OCR-anchored click + süreç yönetimi | `backend/app/automation/gui.py`, `app/tools/system_tools.py` | COMPLETE (Windows) |
| Browser Agent: Playwright (navigate/read/DOM/click/type/tabs/verify) | `backend/app/browser/agent.py` | COMPLETE — gerçek Chromium E2E geçti |
| Skills (metadata'lı, onay kapılı) | `backend/app/skills/` | COMPLETE |
| Connectors: hava (OWM/wttr), takvim (.ics/Outlook), **e-posta (SMTP/IMAP)** | `backend/app/connectors/` | COMPLETE (kimlikler vault'tan) |
| Security: vault (Fernet), sandbox, 5 seviye risk motoru, redaction, audit, rate limit, session TTL | `backend/app/security/` | COMPLETE |
| Self-coding: BACKUP→APPLY→TEST→COMMIT/ROLLBACK, güvenlik çekirdeği dokunulamaz | `backend/codegen.py` | COMPLETE |
| Proactive (spike-suppressed) · Presence (wifi/bt/mobile/iot/manual) · IoT (gerçek/mock etiketli) | `backend/app/proactive|presence|iot/` | COMPLETE |
| Mesh: PC↔mobil, sealed rules, pairing key | `backend/app/mesh/` | COMPLETE |
| Frontend: React 19 + three.js · Mobil: 6 sayfa | `frontend/`, `frontend-mobile/` | build geçiyor |

## Kurulum (Windows)

1. `scripts/install_windows.bat` — venv + pip + npm + build (tek tık)
2. `python scripts/check_deps.py` — bağımlılık doğrulama (zorunlu öğeler eksikse hata)
3. Ollama: `ollama pull qwen2.5-coder:7b && ollama pull llava:7b && ollama pull qwen3:8b && ollama pull qwen3:4b`
4. Opsiyonel donanım katmanları: tesseract (OCR), `python -m playwright install chromium` (browser),
   `PICOVOICE_ACCESS_KEY` (wake word), mikrofon (VAD/STT)
5. `scripts/start_ultron.bat` → backend :8000, frontend :5173

## Güvenlik modeli (kısa)

- 5 risk seviyesi: `SAFE < LOW < MEDIUM < HIGH < CRITICAL` — CRITICAL (shell/delete/install/ayar/process-kill)
  yalnız sunucu-doğrulanmış kullanıcı onayıyla çalışır; agent kendini onaylayamaz.
- Kimlik bilgileri yalnız `data/vault/` içinde Fernet ile şifreli; prompt/log/audit'e asla girmez.
- Self-coding güvenlik çekirdeğini (`app/security/`, `auth.py`, executor, codegen) **onaylı olsa bile** değiştiremez.
- Dosya erişimi sandbox kökleriyle sınırlı (traversal/UNC/symlink/system-dizin bloğu).
- `backend/data/` (vault, memory, oturumlar, loglar) repo'ya girmez.

## Test

```bash
cd backend && python -m pytest -q tests/          # 174+ test
python tests/test_eval_golden.py                  # 36 golden senaryo skoru
```

Ayrıntı: `ARCHITECTURE.md`.

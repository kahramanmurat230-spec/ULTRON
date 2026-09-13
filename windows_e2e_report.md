# ULTRON Windows Gerçek-Ortam E2E Doğrulama Raporu

- Üretim: 2026-09-09T19:29:03.667960+00:00  ·  Süre: 308.5 sn
- Ortam: Windows 11 (10.0.26200) · Python 3.13.14
- MODEL: `qwen2.5-coder:7b`

## Sonuç: FAIL — düzeltilmesi gereken bulgular var

| Faz | PASS | FAIL | UNVERIFIED | SKIP | INFO |
|---|---|---|---|---|---|
| 1 | 7 | 1 | 0 | 0 | 0 |
| 2 | 0 | 1 | 0 | 0 | 0 |
| 3 | 0 | 0 | 1 | 0 | 0 |
| 4 | 0 | 1 | 0 | 0 | 0 |
| 5 | 10 | 1 | 0 | 0 | 0 |
| 6 | 0 | 2 | 0 | 0 | 0 |


## Faz 1

| Durum | Kontrol | Kanıt |
|---|---|---|
| PASS | Windows işletim sistemi | sys.platform=win32 platform=Windows release=11 version=10.0.26200 |
| PASS | Python >= 3.10 | 3.13.14 (tags/v3.13.14:fd17997, Jun 10 2026, 13:03:48) [MSC v.1944 64 bit (AMD64)] |
| PASS | git | git version 2.55.0.windows.3 |
| PASS | node | v24.19.0 |
| FAIL | npm | bulunamadı |
| PASS | ollama ikilisi | ollama version is 0.33.3 |
| PASS | Ollama servisi (11434) | HTTP 200 :: {'version': '0.33.3'} |
| PASS | model hazır: qwen2.5-coder:7b | yüklü modeller: ['qwen2.5-coder:7b', 'llava:7b', 'qwen3:4b', 'qwen3:8b'] |

## Faz 2

| Durum | Kontrol | Kanıt |
|---|---|---|
| FAIL | gerçek backend başlatma | boot başarısız; log kuyruğu: : True, 'mode': 'reframe'} [ULTRON-HEALTH] sqlite_ok: True [ULTRON-HEALTH] runtime: online [ULTRON-HEALTH] sovereign: {'sovereign_mode': True, 'llm': { |

## Faz 3

| Durum | Kontrol | Kanıt |
|---|---|---|
| UNVERIFIED | ajan E2E | Faz 2 backend'i çalışmadı |

## Faz 4

| Durum | Kontrol | Kanıt |
|---|---|---|
| FAIL | modül içe aktarımı | ModuleNotFoundError: No module named 'app.security.shell_policy' |

## Faz 5

| Durum | Kontrol | Kanıt |
|---|---|---|
| PASS | playwright paketi | import OK  |
| PASS | playwright chromium | sayfa metni='ultron' |
| PASS | pyautogui paketi | import OK  |
| PASS | pyautogui ekran görüntüsü | ultron_win_e2e_shot.png boyut=188436 |
| FAIL | OCR (tesseract) | okunan='ULTRONT23' (tesseract.exe PATH'te olmalı) |
| PASS | ses cihazları (sounddevice) | 22 cihaz |
| PASS | kamera (cv2) | frame okundu=True |
| PASS | edge_tts paketi | import OK  |
| PASS | pvporcupine paketi | import OK  |
| PASS | Windows ses bildirimi (winsound) | MessageBeep OK |
| PASS | windows_tools notepad aç/kapat | close→notepad.exe kapatıldı (rc=0) |

## Faz 6

| Durum | Kontrol | Kanıt |
|---|---|---|
| FAIL | backend pytest paketi | rc=1 :: 181 failed, 731 passed, 4 skipped in 135.95s (0:02:15) |
| FAIL | frontend npm build | install rc=None build rc=None :: bulunamadı |

---
UNVERIFIED = bağımlılık/ortam eksik; sahte geçiş DEĞİL. FAIL = gerçek kusur. Bu rapor otomatik üretilmiştir.
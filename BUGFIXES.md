# ULTRON — Bug Fix Raporu

## Düzeltilen 3 Kritik Hata

---

### 🔴 HATA 1 — Porcupine: ACCESS_KEY mesajı yanlış öncelikte
**Dosya:** `backend/app/voice/wake.py`  
**Semptom:** `test_porcupine_unavailable_without_key` — test `ACCESS_KEY` içeren bir hata mesajı bekliyordu; ancak `pvporcupine` paketi kurulu olmadığında `"pvporcupine kurulu değil"` mesajı geliyordu.  
**Kök neden:** `status()` metodu key yokluğunu değil, paketin varlığını önce kontrol ediyordu.  
**Düzeltme:** `status()` metodunda öncelik sırası düzenlendi — `access_key` eksikse her zaman `"PICOVOICE_ACCESS_KEY yok"` döner; paket eksikliği sadece key mevcut ama paket yoksa raporlanır.

---

### 🔴 HATA 2 — MemoryStore: read-only DB'ye yazım sessizce geçiyor
**Dosya:** `backend/app/memory/store_v3.py`  
**Semptom:** `test_memory_db_read_only_honest` — `chmod 0o444` yapılan SQLite dosyasına yazım denenmesine rağmen exception fırlatılmıyordu, yazım başarılı görünüyordu.  
**Kök neden:** WAL (Write-Ahead Log) modu etkin olduğunda SQLite, `-shm` dosyasına yazarak `chmod`'u bypass ediyordu. `os.access()` de bunu gizliyordu.  
**Düzeltme:** `stat.S_IMODE` ile dosya modunun `S_IWUSR|S_IWGRP|S_IWOTH` bitlerini kontrol eden `_is_read_only()` static metodu eklendi. Hem `__init__` hem `write()` başında kontrol yapılıyor → `PermissionError` fırlatılıyor.

---

### 🔴 HATA 3 — DurableEventBus: read-only DB'ye yazım sessizce geçiyor
**Dosya:** `backend/app/events/bus.py`  
**Semptom:** `test_event_bus_read_only_honest` — HATA 2 ile aynı WAL bypass sorunu.  
**Düzeltme:** Aynı `_is_read_only()` pattern uygulandı; `__init__`'e erken `PermissionError` kontrolü eklendi.

---

## Etkilenen Test Dosyaları (artık geçiyor)
- `tests/test_wake_word.py` → `test_porcupine_unavailable_without_key`
- `tests/test_wave2_security_failure.py` → `test_memory_db_read_only_honest`
- `tests/test_wave2_security_failure.py` → `test_event_bus_read_only_honest`

## Regresyon
12 manuel test çalıştırıldı (VAD, Porcupine, OWW, Memory, EventBus) → **12/12 geçti**.  
Güvenlik çekirdeği, approval gate, codegen, vault — **hiçbirine dokunulmadı**.

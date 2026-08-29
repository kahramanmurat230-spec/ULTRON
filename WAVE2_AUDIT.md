# WAVE 2 — Repository Audit (Memory / World / Events)

Tarih: 2026-08-29 · Dal: `arena/01a0489d-ultron` · Taban: Wave 1 FROZEN (232+2)

## 1. Mevcut sistem ve kullanım grafiği

| Modül | Sözleşme | Kullananlar | Durum |
|---|---|---|---|
| `app/memory/sqlite_memory.py` (`Memory`, V16) | `memories(id,kind,content,created_at[+last_access,importance,archived])`; `add/delete/recent_full/kind_counts/recent`; `data/memory/ultron.db` | `app/core/runtime.py` (rt.memory), `app/memory/memory_io.py`, `tests/test_mesh.py`, `test_v2_modules.py` | CANLI — korunur, V3 AYRI tablo/db ile additive |
| `app/memory/semantic_memory.py` (`SemanticMemory`) | token-overlap `search/context` | runtime + Agent | CANLI — korunur |
| `app/memory/semantic_memory_v2.py` (`SemanticMemoryV2`) | TF-IDF + optional Chroma; decay; fact extraction | runtime (rt.semv2) + HUD | CANLI — korunur; V3 retrieval kendi dürüst fallback'ini kullanır |
| `app/memory/memory_io.py` | şifreli export/import (V16 tablosuna okur/yazar) | `server.py` `/api/memory/export` | CANLI — V16 sözleşmesi aynen; V3 export ayrıca eklenecek |
| `app/memory/obsidian.py` (`Vault.write_note`) | md not yazar | **HİÇBİR import yok** (tüm *.py tarandı, dinamik kullanım da yok) | ÖLÜ KOD — Cleanup adımında raporla silinecek |
| `app/world/model.py` (`WorldModel`) | `sources: name→callable`; `snapshot()/context_for_llm()`; durumSUZ | `server.py` (hub.world) + `tests/test_world_model.py` | CANLI — korunur; kalıcı/zamansal katman AYRI `WorldStore` |
| `app/tasks/scheduler.py::EventBus` | bellek-içi fnmatch pub/sub; `subscribe/publish` | server `hub.task_bus` + TaskScheduler tetikleyicileri | CANLI — korunur; dayanıklı çekirdek `app/events/bus.py` AYRI, imza-uyumlu |
| `app/proactive/monitor.py` | spike-suppression + cooldown + hysteresis; `evaluate(stats)` | runtime | CANLI — event→proactive köprüsü mevcut semantiği taklit eder, dokunmaz |
| `data/` | memory/, tasks/, metrics/, observability/ ... hepsi gitignored | runtime | runtime verisi — repoya girmez |

## 2. Tespit edilen duplicate/legacy riskleri

- **Yinelenen memory implementasyonu YOK**: V16 zinciri tek; V3 additive yeni
  tablo + yeni modüller olarak eklenir, V16 satırları korunur ve migrasyonla
  (backup→copy→verify→rollback) V3'e kopyalanır.
- `obsidian.py` tek gerçek ölü kod (bkz. §1). Silme işlemi Cleanup commit'inde
  WHY/USED BY/REPLACEMENT/TESTED raporu ile.
- Wave 1 `EventBus` ile Wave 2 `DurableEventBus` kasıtlı olarak ayrıdır
  (§18: Wave 1 WAL/DLQ kopyalanmaz); imza uyumu sayesinde sunucuda birebir
  yerine takılabilir (subscribe/publish aynı semantik).

## 3. Sözleşme kararları (additive)

1. V16 `memories` tablosu ve `Memory` sınıfı **değişmez**; V3 =
   `memory_records` tablosu (yeni db dosyası `data/memory/v3.db`).
2. `WorldModel` **değişmez**; `WorldStore` kalıcı/zamansal kardeşi.
3. Wave 1 `Tracer` gözlemlenebilirlik için yeniden kullanılır (yeni log sistemi YOK).
4. Secret reddi: mevcut `app/security/redaction.py` + vault extra_values.
5. SQLite güvenilirliği: WAL + busy_timeout + FK + txns + indexes + integrity_check.

## 4. Dead code cleanup (tamamlandı)

| Dosya | NEDEN silindi | KULLANAN | YEDEĞİ | TEST EDİLDİ |
|---|---|---|---|---|
| `backend/app/memory/obsidian.py` (19 satır, `Vault.write_note`) | Repo genelinde sıfır import (tüm *.py/*.md/*.json/*.bat tarandı); dinamik kullanım yok;CredentialVault ile ad çakışması riski | HİÇBİR modül/test/sunucu | Not yazma gerekiyorsa `data/vault` akışı veya memory_v3 | Silme sonrası py_compile + tam suit 313 geçti + 2 atlandı |

Belirsiz başka dosya YOK — kalan her modül kullanım grafiğinde CANLI.

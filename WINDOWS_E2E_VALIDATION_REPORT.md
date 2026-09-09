# ULTRON — Windows Gerçek-Ortam E2E Doğrulama Fazı: Yürütme Raporu

Tarih: 2026-09-09 · Dal: `arena/01a08707-ultron` · Temel: `d79aad8` (önceki
final entegrasyon deneti) → bu faz sonu: `deb8fd5`

---

## 0. ÖNCÜ DÜRÜST TESPİT: Bu sandbox Windows DEĞİL

Bu fazın hedefi "gerçek Windows ortamında" doğrulamadır. Yürütme ortamı
(Arena sandbox) yeniden oluşturulmuş bir **Linux** makinesidir ve gerçek
Windows çalışma zamanı **elde edilemez**:

| Kanıt | Değer |
|---|---|
| `uname -a` | Linux e2b.local 6.1.158+ x86_64 |
| `sys.platform` / `os.name` | `linux` / `posix` |
| `wine` / `wine64` | kurulu değil |
| `qemu-system-x86_64` / `VBoxManage` | kurulu değil |
| `/dev/kvm` | yok (donanım sanallaştırma kapalı) |

Kullanıcının kalıcı kuralı gereği **Linux'tan Windows PASS çıkmaz**: bu
raporda hiçbir Windows çalışma zamanı sonucu PASS olarak iddia edilmez.
Bunun yerine bu faz, (a) Windows'a özgü **burada gerçekten kanıtlanabilir**
her şeyi kanıtladı (politika/sandbox **saf metin ve yol mantığı** —
platformdan bağımsız gerçek doğrulama), (b) gerçek Windows kusurlarını
**bu platformda bile kanıtlanabilir biçimde buldu ve düzeltti**, (c)
kullanıcının gerçek Windows makinesinde Faz 1-6'yı birebir çalıştıracak
**koşum takımını** teslim etti.

---

## 1. Bu fazda GERÇEKÇEN YÜRÜTÜLENLER (Linux'ta kanıtlı, sahtesiz)

### 1.1 Windows güvenlik politika matrisi (Faz 4'ün kanıtlanabilir çekirdeği)

`backend/app/security/shell_policy.py` bir **metin analiz** motorudur: aynı
kod gerçek Windows'ta da çalışır. Bu fazda 60+ Windows vektörü gerçek
politika üzerinden koşturuldu. **8 GERÇEK boşluk bulundu, hepsi kapatıldı:**

| # | Vektör (gerçek komut) | Önce | Sonra | Kök neden / düzeltme |
|---|---|---|---|---|
| 1 | `powershell -EncodedCommand <b64>` | **SIZDI** (allowed) | HARD-BLOCK | `\b-enc` düzeni: `\b` `-` öncesinde eşleşmez; `-enc`/`-encodedcommand` artık sürücü-denetimli |
| 2 | `powershell -enc <b64>`, `pwsh -enc` | **SIZDI** | HARD-BLOCK | aynı düzen hatası |
| 3 | `echo x > C:\Windows\System32\config\SAM` | **SIZDI** | HARD-BLOCK | `_SYSTEM_DIRS` Unix-yol merkezliydi; Windows ağaçları + `_win_norm` (harf+ayraç normalizasyonu) eklendi; redirection da aynı kapıdan geçer |
| 4 | `rm -rf C:\Windows`, `c:\windows\system32` | **SIZDI** | HARD-BLOCK | aynı; herhangi sürücü harfi + büyük/küçük harf duyarsız |
| 5 | `rm -rf C:\Users`, `C:\Users\Boss` (profil yıkımı) | SIZDI* | HARD-BLOCK | `/home/<kullanıcı>` analojisi: `users` ve `users/<tek profil>` yıkıcı; derin yollar bilinçli olarak onay kapısında kalır |
| 6 | `rm -rf C:\Users\Boss\ULTRON` (workspace atası, ters ayraç) | **SIZDI** | HARD-BLOCK | workspace kapsama kontrolü `/`-ayracı + harf-duyarlıydı; iki taraf da `_win_norm` ile normalleştirildi |
| 7 | `echo x & rm -rf C:\Windows` (tek `&` zincirleme) | **SIZDI** | HARD-BLOCK | `_SEGMENT_SPLIT`'te tek `&` yoktu (cmd.exe zincirleme); eklendi — `&&` alternasyonda önce kalır, URL'lerdeki `&` zararsız |
| 8 | `rm -rf D:\Program Files\App` (boşluklu yol, tırnaksız) | **SIZDI** | HARD-BLOCK | tokenlaştırma yolu bölüyor → birleştirilmiş-hedef geri-savunması (sadece rm/del özyinelemeli, sürücü-köküne demirli) |

\* #5 için sürücü kökü glob'u (`C:\*`) zaten blokluydu; düz profil yolu değildi.

Ek kanıtlanmış boşluklar (aynı aile):
- `del C:\Windows\System32\cmd.exe` (kritik dosya, `/s`'siz) → `del`/`erase`
  artık rm-like işleniyor; Windows kritik dosyaları (`SAM`, `SYSTEM`,
  `hosts`, `pagefile.sys`, `bcd`, `cmd.exe`, `explorer.exe`...) `_CRITICAL_FILES`'ta.
- `echo x > C:\pagefile.sys` (sistem-dışı-dizinde kritik dosya) → redirect
  kontrolü `_is_critical_file` (ayraç+harf duyarsız) kullanıyor.

### 1.2 Sandbox Windows mantığı (backend/app/security/sandbox.py)

**GERÇEK hata bulundu**: `_check_not_system` gerçek Windows'ta **ölü koddu**
— `"C:/Windows/System32"` öneki `"/windows"` ile hiç eşleşemez (sürücü harfi
yüzünden), yani `workspace=C:\` gibi bir kurulumda `C:\Windows`'a yazma
reddedilmezdi. Kanıt: `PureWindowsPath('C:/Windows/System32/x').parts` →
`('C:\\', 'Windows', 'System32', 'x')` — eski kod join edilen öneke
`startswith("/windows")` arıyordu. **Düzeltme**: parça-tabanlı saf mantık
(`_is_system_dir_path`): herhangi sürücü + `windows|program files|
programdata|$recycle.bin|system volume information|perflogs` ilk dizinse
yazma reddi. PureWindowsPath parçalarıyla **her platformda** test edilebilir
(test_windows_sandbox.py). UNC reddi zaten vardı ve doğrulandı.

### 1.3 API yüzeyi denetimi (Faz 2 hazırlığı)

Kullanıcının Faz-2 listesindeki `/api/health` **yoktu** (sadece
`/api/system|models|connectors/health` vardı) → spec-uyumlu minimal uç nokta
eklendi: `{ok, status, ollama, time}` + test. `retry` uç noktası da yok:
yeniden deneme kalıbı istemci tarafında yeni görev oluşturmaktır — koşum
takımı bunu dürüstçe INFO olarak raporlar.

### 1.4 Koşum takımı: `scripts/windows_e2e_validation.py`

Kullanıcının **gerçek Windows makinesinde** Faz 1-6'yı birebir çalıştırır:

- **Faz 1** ortam: Windows doğrulaması, python/git/node/npm, Ollama ikilisi +
  servis + model varlığı.
- **Faz 2** gerçek API bataryası: `/api/health`, `/api/system/health`,
  `/api/capabilities`, görev oluştur/izle/listele, pause/resume, cancel,
  onay akışı (MEDIUM `rm` hedefi → `WAITING_APPROVAL` → onayla → yürütme;
  kurban dosyasının bağımsız dosya-sistemi denetimiyle).
- **Faz 3** gerçek ajan E2E: hedef → gerçek Ollama planlaması → yürütme →
  `backend/data/win_e2e/probe_<ts>.txt` **gerçek dosya etkisi** →
  **bağımsız** dosya sistemi doğrulaması (API iddiasına güvenmeden) →
  memory'de sonuç-öğrenimi kaydı → kalıcı görev durumu.
- **Faz 4** Windows güvenliği: (a) 46 yıkıcı vektörlük yerel politika
  matrisi + 5 meşru-vektör aşırı-blok kontrolü; (b) sandbox UNC/sistem-dizin
  reddi; (c) **canlı blok-öncesi kanıtı**: zararsız `%TEMP%` kurban dizini
  üzerinde `rd /s /q`, `-EncodedCommand`, korumalı-yola redirect hedefleri
  gönderilir → görevin REDDEDİLMESİ ve kurban dizinin YAŞAMASI kanıtlanır.
  **Gerçek işletim sistemine karşı yıkıcı komut asla işletilmez.**
- **Faz 5** yetenekler: playwright+chromium (gerçek sayfa), pyautogui ekran
  görüntüsü, tesseract OCR (üretilmiş görüntüden `ULTRON123` okuma),
  sounddevice/cv2 cihaz varlığı, edge-tts, pvporcupine, winsound,
  windows_tools notepad aç/kapat (görünür, zararsız). Eksik bağımlılık →
  dürüst **UNVERIFIED** (sahte geçiş yok).
- **Faz 6** regresyon: `pytest tests/` (1800 sn timeout) + `npm install` +
  `npm run build`.
- Çıktılar: `windows_e2e_report.json` + `windows_e2e_report.md`; konsol
  özeti; çıkış kodu FAIL varsa 1, yoksa 0 (UNVERIFIED başarısız sayılmaz).

Çalıştırma (Windows, proje kökü, venv etkin):
```
python scripts\windows_e2e_validation.py --model qwen2.5-coder:7b
```

### 1.5 Koşum takımının bu ortamda duman testi (makinenin kendisi doğrulandı)

- `--only 1,4` koşusu: Windows → UNVERIFIED, Ollama → UNVERIFIED (red,
  bağlantı yok — sahte Ollama **kullanılmadı**), 46-vektör matrisi → PASS,
  sandbox UNC/sistem-dizin → PASS, canlı kanıt → UNVERIFIED (backend yok).
  Yani dürüst-raporlama semantiği bizzat çalıştırılarak doğrulandı.
- Backend başlat/durdur makineryası gerçek `server.py` ile doğrulandı:
  boot OK, `GET /api/health` → 200 `{"ok": true, "ollama": "offline"}`,
  `GET /api/capabilities` → 200, temiz kapanış (rc=0).

---

## 2. Alt sistem durumu tablosu (bu fazın kapsamı)

| Alt sistem | Durum | Kanıt |
|---|---|---|
| Windows politika matrisi (metin analizi) | **PASS** | 46/46 yıkıcı vektör HARD-BLOCK; 5 meşru vektör onay kapısında; `test_windows_policy_vectors.py` |
| Sandbox Windows yol mantığı | **PASS** | `_is_system_dir_path` PureWindowsPath parçalarıyla; UNC reddi; `test_windows_sandbox.py` |
| Politika Unix profili (regresyon) | **PASS** | eski 40 politika/sandbox/executor testi aynen geçti; `rm -rf /` vb. bloklu |
| `/api/health` uç noktası | **PASS** | gerçek sunucuda 200 döndü (canlı smoke) |
| Backend paketi | **PASS** | **1030 geçti / 0 başarısız / 5 atlandı** (1013 → +17 yeni test) |
| Frontend build | **PASS** | `npm run build` temiz (vite ~4 sn, yalnız chunk-uyarısı) |
| Windows **çalışma zamanı** (Faz 2/3 canlı, Faz 4 canlı, Faz 5) | **UNVERIFIED** | bu ortamda Windows yok (bkz. §0) — koşum takımı gerçek Windows'ta kanıtlar |
| Gerçek Ollama E2E (Faz 3) | **UNVERIFIED** | sandbox'ta Ollama yok; sahte kullanılmaz (kural) |

## 3. Değişen dosyalar (commit `deb8fd5`)

| Dosya | Değişiklik |
|---|---|
| `backend/app/security/shell_policy.py` | `_win_norm`, `_is_critical_file`, Windows sistem ağaçları/kritik dosyalar (herhangi sürücü, harf+ayraç duyarsız), `del`/`erase` rm-like, boşluklu-yol geri-savunması, workspace kapsama normalizasyonu, `-enc` desen düzeltmesi, tek `&` segment ayracı |
| `backend/app/security/sandbox.py` | `_check_not_system` parça-tabanlı yeniden yazım (`_is_system_dir_path`; eskisi gerçek Windows'ta ölüydü) |
| `backend/server.py` | `/api/health` uç noktası + rota |
| `backend/tests/test_windows_policy_vectors.py` | YENİ — 12 test (politika matrisi + `/api/health` kaydı) |
| `backend/tests/test_windows_sandbox.py` | YENİ — 5 test (PureWindowsPath parçaları, UNC) |
| `scripts/windows_e2e_validation.py` | YENİ — Windows E2E koşum takımı (Faz 1-6, JSON+MD rapor) |

## 4. Testler: önce / sonra

- Önce (temel `d79aad8` bu sandbox'ta yeniden doğrulandı): 1013 / 0 / 5.
- Sonra: **1030 geçti / 0 başarısız / 5 atlandı** (dürüst atlamalar:
  browser/PvS/Windows-cihaz E2E/tesseract/deterministic-parser — değişmedi).
- Frontend: `npm install` + `npm run build` temiz.

## 5. Kalan engeller ve tamamlama yolu

1. **Gerçek Windows makinesi** (tek engel): `python scripts\windows_e2e_validation.py`
   çalıştırılmalı; çıktı `windows_e2e_report.md/.json` gerçek kanıtları üretir.
   Ollama açık ve model (`qwen2.5-coder:7b` vb.) yüklü olmalı; aksi halde
   Faz 2/3 dürüstçe UNVERIFIED kalır (koşum takımı sahte Ollama kullanmaz).
2. Faz 5'te donanım gerektirenler (mikrofon, kamera, uyandırma sözcüğü,
   PTT) donanım olmadan UNVERIFIED kalır — kural gereği sahte PASS üretilmez.
3. `/api/voice/ptt` hâlâ yok (önceki denetimden bilinen P2 donanım kalemi).

## 6. Sonuç

Bu fazda iddia edilen hiçbir şey taklit edilmedi: Windows'a özgü **kanıtlanabilir**
tüm mantık (politika + sandbox + API sözleşmesi) gerçek testlerle doğrulandı;
bulunan **9 gerçek güvenlik boşluğu** (8 politika + 1 sandbox ölü-kodu) en küçük
doğru düzeltmelerle kapatıldı ve 17 yeni regresyon testiyle sabitlendi; paket
1013 → **1030/0/5**. Gerçek Windows çalışma zamanı doğrulaması için her şey
hazır: koşum takımı kullanıcının makinesinde Faz 1-6'yı kanıtlı biçimde
çalıştırır ve aynı dürüst PASS/FAIL/UNVERIFIED raporunu üretir.

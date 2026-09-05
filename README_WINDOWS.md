# ULTRON Final — Windows

## Kurulum

1. Bu klasörü istediğin yere çıkar.
2. `scripts\install_windows.bat` dosyasını **Yönetici olarak** çalıştır.
3. Ollama kurulu değilse kur ve ardından `ollama pull qwen2.5-coder:7b` ve `ollama pull llava:7b` çalıştır.
4. Kurulumdan sonra `scripts\start_ultron.bat` çalıştır.
5. Masaüstü arayüzü: `http://localhost:5173`
6. Backend: `http://127.0.0.1:8000`

## Local Voice

ULTRON'un TTS yolu tamamen lokaldir ve ağ üzerinden ses sentezi yapmaz.
Öncelikli motor **Piper local**'dır; Piper modeli yoksa yerel `eSpeak-ng` kullanılabilir.
Uygun yerel motor yoksa ULTRON başarılı ses üretmiş gibi davranmaz ve `UNAVAILABLE`/hata bildirir.
Piper modelini lisansına uygun şekilde `backend\data\voice\piper\tr_TR-ahmet-medium.onnx` konumuna yerleştirin.

## Güvenlik

Backend ve Vite varsayılan olarak yalnızca localhost'a bind edilir. LAN erişimi gerekiyorsa bilinçli olarak `ULTRON_BIND_HOST=0.0.0.0`, `ULTRON_AUTH=1` ve güçlü bir `ULTRON_PAIRING_SECRET` ayarlayın.

## Self Diagnostic

"Ultron, kendini kontrol et ve hata varsa bildir." komutu; agent, tool registry, memory, planner, local voice, vision, proactive monitor, Ollama, veritabanları, master rules, donanım, portlar ve gerçek hata olaylarını kontrol eder.

## Test

Backend klasöründe:

```text
python -m pytest -q
```

Final pakette çalışma zamanı logları, session token'ları, `__pycache__`, eski backup arşivleri ve build artıkları bulunmaz; bunlar ilk çalıştırmada yeniden oluşturulur.

# ULTRON Final — Windows

## Kurulum

1. Bu klasörü istediğin yere çıkar.
2. `scripts\install_windows.bat` dosyasını **Yönetici olarak** çalıştır.
3. Ollama kurulu değilse kur ve ardından `ollama pull qwen2.5-coder:7b` ve `ollama pull llava:7b` çalıştır.
4. Kurulumdan sonra `scripts\start_ultron.bat` çalıştır.
5. Masaüstü arayüzü: `http://localhost:5173`
6. Backend: `http://127.0.0.1:8000`

## Neural Voice

ULTRON yalnızca Microsoft Edge Neural TTS kullanır (`tr-TR-AhmetNeural`).
Robotic eSpeak/SAPI/browser speech fallback'i yoktur. Neural TTS erişilemiyorsa ULTRON sessiz kalır ve hatayı raporlar.

## Güvenlik

Backend ve Vite varsayılan olarak yalnızca localhost'a bind edilir. LAN erişimi gerekiyorsa bilinçli olarak `ULTRON_BIND_HOST=0.0.0.0`, `ULTRON_AUTH=1` ve güçlü bir `ULTRON_PAIRING_SECRET` ayarlayın.

## Self Diagnostic

"Ultron, kendini kontrol et ve hata varsa bildir." komutu; agent, tool registry, memory, planner, neural voice, vision, proactive monitor, Ollama, veritabanları, master rules, donanım, portlar ve gerçek hata olaylarını kontrol eder.

## Test

Backend klasöründe:

```text
python -m pytest -q
```

Final pakette çalışma zamanı logları, session token'ları, `__pycache__`, eski backup arşivleri ve build artıkları bulunmaz; bunlar ilk çalıştırmada yeniden oluşturulur.

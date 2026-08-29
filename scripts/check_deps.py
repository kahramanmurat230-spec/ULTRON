#!/usr/bin/env python3
"""ULTRON kurulum doğrulayıcı — gerçek kontroller, dürüst sonuç.

Kontrol: Python/Node/npm sürümleri, Python paketleri, tesseract, Ollama
erişimi + modeller, ports (8000/5173), frontend build varlığı.
Çıkış: 0 = hazır, 1 = eksik var (rapor stdout).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

REQUIRED_PY = [
    ("aiohttp", "backend HTTP sunucusu"),
    ("psutil", "sistem telemetrisi"),
    ("cryptography", "credential vault (Fernet)"),
    ("PIL", "vision foundation + screenshot"),
    ("numpy", "semantic memory TF-IDF"),
]
OPTIONAL_PY = [
    ("edge_tts", "neural TTS (cloud)"),
    ("pytesseract", "OCR (tesseract binary de gerekir)"),
    ("pyautogui", "computer use"),
    ("playwright", "browser agent"),
    ("pvporcupine", "wake word (PICOVOICE_ACCESS_KEY gerekir)"),
    ("faster_whisper", "STT"),
    ("sounddevice", "mikrofon/hoparlör I/O"),
]


FAILURES = []


def check(name, ok, detail="", required=True):
    status = "OK " if ok else ("EKSİK" if required else "opsiyonel-yok")
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not ok and required:
        FAILURES.append(name)
    return ok


def port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    print("ULTRON kurulum kontrolü")
    print("=" * 60)
    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    node = shutil.which("node")
    node_v = ""
    if node:
        try:
            node_v = subprocess.run([node, "--version"], capture_output=True,
                                    text=True, timeout=10).stdout.strip()
        except Exception:
            pass
    check("Node.js", bool(node), node_v)
    npm = shutil.which("npm")
    check("npm", bool(npm))

    for mod, why in REQUIRED_PY:
        try:
            __import__(mod)
            check(f"python: {mod}", True, why)
        except ImportError:
            check(f"python: {mod}", False, why)

    for mod, why in OPTIONAL_PY:
        try:
            __import__(mod)
            check(f"python (opsiyonel): {mod}", True, why, required=False)
        except ImportError:
            check(f"python (opsiyonel): {mod}", False, why, required=False)

    tess = shutil.which("tesseract")
    check("tesseract binary (OCR)", bool(tess),
          tess or "kurulum gerekli — olmadan OCR dürüst hata verir", required=False)

    ollama_url = os.environ.get("ULTRON_OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        import urllib.request
        with urllib.request.urlopen(ollama_url + "/api/tags", timeout=3) as r:
            models = [m.get("name") for m in json.loads(r.read()).get("models", [])]
        check("Ollama erişimi", True, ollama_url)
        wanted = ["qwen2.5-coder", "llava", "qwen3"]
        have = [w for w in wanted if any(w in (m or "") for m in models)]
        check("Modeller", bool(have),
              f"kurulu: {', '.join(models[:6]) or 'hiç'}", required=False)
    except Exception as exc:
        check("Ollama erişimi", False, f"{ollama_url} ({str(exc)[:60]})",
              required=False)

    check("port 8000 boş", port_free(8000))
    check("port 5173 boş", port_free(5173), "", required=False)

    venv = (ROOT / ".venv").exists()
    check(".venv", venv, "kurulum script'i oluşturur", required=False)
    fe_build = (ROOT / "frontend" / "dist" / "index.html").exists()
    check("frontend build", fe_build, "npm run build", required=False)

    print("=" * 60)
    if FAILURES:
        print(f"SONUÇ: EKSİK — zorunlu: {', '.join(FAILURES)}")
        return 1
    print("SONUÇ: HAZIR — zorunlu tüm öğeler tam.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

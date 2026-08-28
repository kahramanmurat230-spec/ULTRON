import subprocess
import webbrowser
import sys
import re

APP_ALIASES = {
    "chrome": "start chrome",
    "edge": "start msedge",
    "notepad": "start notepad",
    "calculator": "start calc",
    "hesap makinesi": "start calc",
    "discord": "start discord",
    "spotify": "start spotify",
}

def _chrome_executable():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(__import__("pathlib").Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    for candidate in candidates:
        if __import__("os").path.exists(candidate):
            return candidate
    return "chrome.exe"

def open_application(name):
    if sys.platform != "win32":
        raise RuntimeError("Windows application control is only supported on Windows.")
    key = name.lower().strip()
    if key == "chrome":
        subprocess.Popen([_chrome_executable()], creationflags=subprocess.CREATE_NO_WINDOW)
        return "Chrome başlatıldı."

    cmd = APP_ALIASES.get(key)
    if not cmd:
        raise ValueError(f"Unknown safe application alias: {name}")
    subprocess.Popen(["cmd", "/c", cmd], creationflags=subprocess.CREATE_NO_WINDOW)
    return f"{name} başlatıldı."

def close_application(name):
    key = name.lower().strip()
    exe = {"chrome": "chrome.exe", "edge": "msedge.exe", "notepad": "notepad.exe",
           "not defteri": "notepad.exe", "discord": "discord.exe", "spotify": "spotify.exe"}.get(key)
    if not exe:
        raise ValueError(f"Unknown safe application alias: {name}")
    if sys.platform == "win32":
        p = subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, text=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return f"{exe} kapatıldı (rc={p.returncode})"
    p = subprocess.run(["pkill", "-f", exe.replace(".exe", "")], capture_output=True, text=True)
    return f"{exe} kapatıldı (rc={p.returncode})"

def open_file(path):
    import os
    p = str(path)
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    if sys.platform == "win32":
        os.startfile(p)  # noqa
    else:
        subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Dosya açıldı: {p}"

def open_folder(path):
    import os
    p = str(path)
    if not os.path.isdir(p):
        raise FileNotFoundError(p)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", p], creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Klasör açıldı: {p}"

def open_url(url):
    url = str(url).strip()
    m = re.search(r"\]\((https?://[^)]+)\)", url)
    if m:
        url = m.group(1)

    # If this is a Chrome URL, use Chrome directly. This prevents the
    # previous behavior where "open_application(chrome)" + webbrowser.open()
    # created an extra Chrome/default-browser tab.
    if sys.platform == "win32":
        chrome = _chrome_executable()
        if "google.com/search?" in url or url.startswith(("https://", "http://")):
            subprocess.Popen(
                [chrome, "--new-tab", url],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return f"Chrome'da açıldı: {url}"

    webbrowser.open(url)
    return f"Tarayıcı açıldı: {url}"

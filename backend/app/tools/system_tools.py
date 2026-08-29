from app.telemetry.system_stats import get_system_stats

def system_status():
    return get_system_stats()


# ---------------------------------------------------------------- processes
def process_list(limit: int = 50, name: str | None = None):
    """Çalışan süreçler (psutil gerçek). name filtresi substring."""
    import psutil
    procs = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            info = p.info
        except Exception:  # noqa: BLE001
            continue
        if name and name.lower() not in (info["name"] or "").lower():
            continue
        procs.append({"pid": info["pid"], "name": info["name"] or "?"})
        if len(procs) >= int(limit):
            break
    return {"count": len(procs), "processes": procs}


def process_info(pid: int) -> dict:
    """Tek süreç detayı (cmdline dahil)."""
    import psutil
    p = psutil.Process(int(pid))
    return {"pid": p.pid, "name": p.name(),
            "cmdline": p.cmdline() or [],
            "status": p.status(),
            "created_at": round(p.create_time(), 3)}


def process_kill(pid: int) -> dict:
    """Süreç sonlandırma — KENDİ sürecine karşı RED (asla intihar etmez)."""
    import os
    import psutil
    if int(pid) == os.getpid():
        raise PermissionError("process_kill self-process reddedilir")
    p = psutil.Process(int(pid))
    name = p.name()
    p.kill()
    p.wait(timeout=5)
    return {"killed": int(pid), "name": name}


def system_settings_view() -> dict:
    """Sistem ayarları görünümü — Windows'ta gerçek startup envanteri,
    değilse dürüst not (sahte veri YOK)."""
    import platform
    out = {"os": platform.system(), "startup_items": []}
    if platform.system() == "Windows":  # pragma: no cover — gerçek Windows'ta
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\\Microsoft\\Windows\\CurrentVersion\\Run")
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                out["startup_items"].append({"name": name, "command": value})
                i += 1
        except Exception as exc:  # noqa: BLE001
            out["note"] = f"startup okunamadı: {str(exc)[:80]}"
    else:
        out["note"] = ("startup envanteri yalnız Windows'ta; bu ortamda "
                       "dürüst boş liste")
    return out

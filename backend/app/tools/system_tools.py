from app.telemetry.system_stats import get_system_stats

def system_status():
    return get_system_stats()


# ---------------- PHASE 8: process inspection & system settings view ----------------
def process_list(sort="cpu", limit=30, name=None):
    """Gerçek süreç envanteri (psutil). name substr filtresi."""
    import psutil
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "status"]):
        try:
            info = p.info
        except Exception:
            continue
        if name and name.lower() not in (info.get("name") or "").lower():
            continue
        procs.append({k: info.get(k) for k in
                      ("pid", "name", "username", "cpu_percent", "memory_percent", "status")})
    key = "memory_percent" if sort == "memory" else "cpu_percent"
    procs.sort(key=lambda x: (x.get(key) or 0.0), reverse=True)
    return {"count": len(procs), "processes": procs[: max(1, min(int(limit), 100))]}


def process_info(pid):
    """Tek süreç detayı: cmdline, create_time, connections count (izin veriyorsa)."""
    import psutil
    p = psutil.Process(int(pid))
    out = {"pid": p.pid, "name": p.name(), "status": p.status(),
           "create_time": p.create_time(), "cmdline": p.cmdline()[:20]}
    try:
        out["connections"] = len(p.net_connections())
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        out["connections"] = None  # dürüst: izin yok
    with p.oneshot():
        out["cpu_percent"] = p.cpu_percent(interval=None)
        out["memory_percent"] = round(p.memory_percent(), 2)
        out["exe"] = p.exe() if hasattr(p, "exe") else None
    return out


def process_kill(pid):
    """CRITICAL: süreç öldürür — yalnız onaylı. Kendi pid'imizi asla öldürmeyiz."""
    import os
    import psutil
    pid = int(pid)
    if pid == os.getpid():
        raise PermissionError("ULTRON kendi sürecini öldüremez")
    p = psutil.Process(pid)
    name = p.name()
    p.kill()
    p.wait(timeout=5)
    return {"killed": pid, "name": name}


def system_settings_view():
    """Salt-okunur sistem ayar görünürlüğü (Windows startup + env + güç planı)."""
    import platform
    out = {"os": platform.platform(), "python": platform.python_version()}
    if platform.system() == "Windows":
        try:
            import winreg
            run_keys = []
            for root, path in ((winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run"),
                               (winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run")):
                try:
                    with winreg.OpenKey(root, path) as k:
                        i = 0
                        while True:
                            try:
                                name, val, _ = winreg.EnumValue(k, i)
                                run_keys.append({"name": name, "command": val[:160], "hive": "HKCU" if root == winreg.HKEY_CURRENT_USER else "HKLM"})
                                i += 1
                            except OSError:
                                break
                except OSError:
                    continue
            out["startup_items"] = run_keys
        except Exception as exc:  # noqa: BLE001
            out["startup_items"] = {"error": str(exc)[:120]}
    else:
        out["startup_items"] = {"note": "Windows dışı ortam: startup kaydı yok (dürüst boş)"}
    return out

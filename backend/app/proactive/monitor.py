import threading
import time
from app.telemetry.system_stats import get_system_stats

class ProactiveMonitor:
    def __init__(self, settings, callback):
        cfg = settings.get("proactive", {})
        self.interval = int(cfg.get("interval_seconds", 10))
        self.ram_limit = float(cfg.get("ram_warning_percent", 90))
        self.cpu_limit = float(cfg.get("cpu_warning_percent", 95))
        self.disk_limit = float(cfg.get("disk_warning_percent", 95))
        self.callback = callback
        self.running = False
        self._warned_disk = False
        # PHASE 11: spike-suppression parametreleri + metrik başına durum
        self.sustained_samples = int(cfg.get("sustained_samples", 3))
        self.alarm_cooldown = float(cfg.get("alarm_cooldown_seconds", 900))
        self.hysteresis = float(cfg.get("hysteresis_percent", 5))
        self._streak = {"cpu": 0, "ram": 0, "disk": 0}
        self._last_alarm = {"cpu": -1e18, "ram": -1e18, "disk": -1e18}

    _METRICS = (
        ("cpu", "cpu_percent", "CPU", "cpu_limit"),
        ("ram", "ram_percent", "RAM", "ram_limit"),
        ("disk", "disk_percent", "Disk", "disk_limit"),
    )

    def evaluate(self, snapshot: dict, now: float | None = None) -> list:
        """Tek ölçüm değerlendir → alarm listesi (string).

        Spike suppression: tek seferlik sıçrama alarm ÜRETMEZ; limit ÜSTÜ
        `sustained_samples` aralıksız örnek gerekir. Hysteresis: değer
        (limit - hysteresis) ALTINA düşmedikçe sayaç SIFIRLANMAZ (toparlanma
        sayılmaz). Alarm sonrası `alarm_cooldown` boyunca aynı metrik
        susar."""
        now = time.time() if now is None else float(now)
        alarms = []
        for key, field, label, limattr in self._METRICS:
            val = snapshot.get(field)
            if val is None:
                continue
            limit = getattr(self, limattr)
            val = float(val)
            if val >= limit:
                self._streak[key] += 1
                if (self._streak[key] >= self.sustained_samples
                        and now - self._last_alarm[key] >= self.alarm_cooldown):
                    alarms.append(f"{label} kullanımı sürekli kritik: %{val:.0f}")
                    self._last_alarm[key] = now
            elif val < limit - self.hysteresis:
                self._streak[key] = 0   # gerçek toparlanma → sayaç sıfır
            # arada (hysteresis bandı): DOKUNMA — spike sayılmaz, sıfırlanmaz
        return alarms

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        warned_ram = False
        warned_cpu = False
        while self.running:
            try:
                s = get_system_stats()
                if s["ram_percent"] >= self.ram_limit and not warned_ram:
                    self.callback(f"RAM kullanımı kritik: %{s['ram_percent']}")
                    warned_ram = True
                elif s["ram_percent"] < self.ram_limit - 5:
                    warned_ram = False

                if s["cpu_percent"] >= self.cpu_limit and not warned_cpu:
                    self.callback(f"CPU kullanımı kritik: %{s['cpu_percent']}")
                    warned_cpu = True
                elif s["cpu_percent"] < self.cpu_limit - 5:
                    warned_cpu = False

                disk = s.get("disk_percent", 0)
                if disk >= self.disk_limit and not self._warned_disk:
                    self.callback(f"Disk alanı kritik: %{disk}")
                    self._warned_disk = True
                elif disk < self.disk_limit - 5:
                    self._warned_disk = False
            except Exception as e:
                self.callback(f"Proaktif izleme hatası: {e}")
            time.sleep(self.interval)

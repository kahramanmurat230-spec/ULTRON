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

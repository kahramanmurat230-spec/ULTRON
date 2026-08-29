import threading
import time

from app.telemetry.system_stats import get_system_stats


class ProactiveMonitor:
    """Resource watch with SPIKE-SUPPRESSION.

    An alarm fires only when a metric stays above its limit for
    `sustained_samples` consecutive samples (a single CPU spike is noise),
    and repeats at most once per `alarm_cooldown_s` per metric. Recovery
    requires dropping `hysteresis` points below the limit — flapping values
    around the threshold never produce alarm storms.
    """

    def __init__(self, settings, callback):
        cfg = settings.get("proactive", {})
        self.interval = int(cfg.get("interval_seconds", 10))
        self.ram_limit = float(cfg.get("ram_warning_percent", 90))
        self.cpu_limit = float(cfg.get("cpu_warning_percent", 95))
        self.disk_limit = float(cfg.get("disk_warning_percent", 95))
        self.sustained = max(1, int(cfg.get("sustained_samples", 3)))
        self.cooldown_s = float(cfg.get("alarm_cooldown_seconds", 900))
        self.hysteresis = float(cfg.get("hysteresis_percent", 5))
        self.callback = callback
        self.running = False
        # metric -> {"over_count": int, "last_alarm": ts|None}
        self._state = {m: {"over_count": 0, "last_alarm": None}
                       for m in ("ram", "cpu", "disk")}
        self._last_error_at = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    # ------------------------------------------------------------ core logic
    def _check_metric(self, metric, value, limit, now):
        """Returns an alarm message or None. Pure aside from state/clock."""
        st = self._state[metric]
        if value >= limit:
            st["over_count"] += 1
            if st["over_count"] >= self.sustained:
                last = st["last_alarm"]
                if last is None or (now - last) >= self.cooldown_s:
                    st["last_alarm"] = now
                    label = {"ram": "RAM", "cpu": "CPU", "disk": "Disk"}[metric]
                    return f"{label} kullanımı sürekli kritik: %{value:.0f}"
        elif value < limit - self.hysteresis:
            # gerçek toparlanma: sayaç sıfırlanır (hysteresis altına inmediyse sayaç korunur)
            st["over_count"] = 0
        return None

    def evaluate(self, stats, now=None):
        """Check one stats sample; fires callback per alarm, returns them."""
        now = time.time() if now is None else now
        alarms = []
        for metric, key, limit in (
                ("ram", "ram_percent", self.ram_limit),
                ("cpu", "cpu_percent", self.cpu_limit),
                ("disk", "disk_percent", self.disk_limit)):
            val = stats.get(key)
            if val is None:
                continue
            msg = self._check_metric(metric, float(val), limit, now)
            if msg:
                alarms.append(msg)
        for a in alarms:
            self.callback(a)
        return alarms

    # ------------------------------------------------------------ loop
    def _loop(self):
        while self.running:
            try:
                alarms = self.evaluate(get_system_stats())
                for a in alarms:
                    self.callback(a)
            except Exception as e:  # noqa: BLE001
                # hata spam'i de cooldown'lı (aynı pencerede tek rapor)
                now = time.time()
                if now - self._last_error_at >= self.cooldown_s:
                    self._last_error_at = now
                    self.callback(f"Proaktif izleme hatası: {e}")
            time.sleep(self.interval)

"""Real system telemetry sampler. No fake values: everything is read from the OS."""
import asyncio
import glob
import shutil
import subprocess
import time

import psutil


class Telemetry:
    def __init__(self) -> None:
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.monotonic()
        self.net_down_mbps = 0.0
        self.net_up_mbps = 0.0
        self.cpu = 0.0
        self.gpu = None  # None => no NVIDIA GPU tool => UI must show N/A
        self.temps: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._tick = 0
        self.started_at = time.time()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        psutil.cpu_percent(None)  # prime the delta
        while True:
            await asyncio.sleep(1.0)
            self._tick += 1
            self.cpu = psutil.cpu_percent(None)

            net = psutil.net_io_counters()
            now = time.monotonic()
            dt = max(now - self._last_net_t, 1e-6)
            self.net_down_mbps = max(0.0, (net.bytes_recv - self._last_net.bytes_recv) / dt * 8 / 1e6)
            self.net_up_mbps = max(0.0, (net.bytes_sent - self._last_net.bytes_sent) / dt * 8 / 1e6)
            self._last_net = net
            self._last_net_t = now

            self._sample_temps()
            if self._nvidia_smi and self._tick % 5 == 0:
                try:
                    self.gpu = await asyncio.to_thread(self._sample_gpu)
                except Exception:
                    self.gpu = None

    @staticmethod
    def _sample_gpu() -> dict | None:
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            if not out:
                return None
            util, used, total, temp, *name = [p.strip() for p in out.split(",")]
            return {
                "util": float(util),
                "mem_used_mb": float(used),
                "mem_total_mb": float(total),
                "temp_c": float(temp),
                "name": ",".join(name) or "NVIDIA GPU",
            }
        except Exception:
            return None

    def _sample_temps(self) -> None:
        temps: dict[str, float] = {}
        try:
            sensors = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
            for chip, entries in (sensors or {}).items():
                for e in entries:
                    label = e.label or chip
                    if e.current:
                        temps.setdefault(label, float(e.current))
        except Exception:
            pass
        if not temps:
            for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
                try:
                    with open(path) as f:
                        v = float(f.read().strip()) / 1000.0
                    if 0 < v < 120:
                        zone = path.split("/")[-2]
                        temps.setdefault(zone, v)
                except Exception:
                    continue
        self.temps = temps

    def snapshot(self) -> dict:
        vm = psutil.virtual_memory()
        du = psutil.disk_usage("/")
        cpu_freq = psutil.cpu_freq()
        return {
            "ts": time.time(),
            "uptime_s": round(time.time() - self.started_at, 0),
            "cpu": {"percent": round(self.cpu, 1), "freq_mhz": round(cpu_freq.current, 0) if cpu_freq else None},
            "ram": {
                "percent": round(vm.percent, 1),
                "used_gb": round(vm.used / 2**30, 1),
                "total_gb": round(vm.total / 2**30, 1),
            },
            "disk": {
                "percent": round(du.percent, 1),
                "used_gb": round(du.used / 2**30, 1),
                "total_gb": round(du.total / 2**30, 1),
            },
            "gpu": self.gpu,
            "net": {"down_mbps": round(self.net_down_mbps, 1), "up_mbps": round(self.net_up_mbps, 1)},
            "temps": {k: round(v, 1) for k, v in self.temps.items()},
        }

import platform
import psutil

def get_system_stats():
    result = {
        "platform": platform.platform(),
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "ram_percent": psutil.virtual_memory().percent,
        "ram_used_gb": round(psutil.virtual_memory().used / 1024**3, 2),
        "ram_total_gb": round(psutil.virtual_memory().total / 1024**3, 2),
        "disk_percent": psutil.disk_usage("/").percent,
        "network": {
            "bytes_sent": psutil.net_io_counters().bytes_sent,
            "bytes_recv": psutil.net_io_counters().bytes_recv,
        }
    }
    try:
        import pynvml
        pynvml.nvmlInit()
        devices = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(devices):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            gpus.append({
                "index": i,
                "util_percent": util.gpu,
                "vram_percent": round(mem.used / mem.total * 100, 1) if mem.total else 0,
                "temperature_c": temp
            })
        result["gpus"] = gpus
    except Exception:
        result["gpus"] = []
    return result

"""System and local-inference runtime resources without a response deadline."""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import time
from typing import Any


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class Telemetry:
    def __init__(self):
        self.last: tuple[int, int, int] | None = None

    @staticmethod
    def _runtime_workers() -> list[dict[str, Any]]:
        try:
            import psutil

            workers = []
            for process in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
                try:
                    info = process.info
                    name = str(info.get("name") or "")
                    folded = name.casefold()
                    if not ("ollama_llama_server" in folded or "llama-server" in folded or "llama_server" in folded):
                        continue
                    memory = info.get("memory_info")
                    workers.append({
                        "pid": int(info["pid"]), "name": name,
                        "cpu_percent": max(0.0, float(process.cpu_percent(interval=None))),
                        "rss_bytes": int(memory.rss) if memory else None,
                    })
                except (psutil.Error, OSError, TypeError, ValueError, KeyError):
                    continue
            return workers
        except Exception:
            return []

    @staticmethod
    def _runtime_gpu_memory(worker_pids: set[int]) -> int | None:
        if not worker_pids or not shutil.which("nvidia-smi"):
            return None
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                return None
            attributed = []
            for line in result.stdout.splitlines():
                fields = [field.strip() for field in line.split(",", 2)]
                if len(fields) != 3:
                    continue
                try:
                    pid, memory = int(fields[0]), int(fields[2])
                except ValueError:
                    continue
                if pid in worker_pids:
                    attributed.append(memory)
            return sum(attributed) if attributed else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def snapshot(self, loaded=None):
        out = {
            "time": time.time(), "cpu_percent": None, "ram_total_bytes": None,
            "ram_available_bytes": None, "gpu": None, "gpu_stats": None,
            "loaded": loaded or [], "runtime_workers": [], "runtime_worker_count": 0,
            "runtime_worker_cpu_percent": None, "runtime_worker_rss_bytes": None,
            "runtime_gpu_memory_mib": None,
            "runtime_gpu_attribution": "runtime process VRAM; not selected-model attribution",
        }
        try:
            kernel = ctypes.windll.kernel32
            idle, kernel_time, user_time = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
            if kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                current = (idle.value, kernel_time.value, user_time.value)
                if self.last:
                    idle_delta = current[0] - self.last[0]
                    total_delta = current[1] - self.last[1] + current[2] - self.last[2]
                    out["cpu_percent"] = max(0, min(100, (total_delta - idle_delta) / total_delta * 100)) if total_delta else 0
                self.last = current
            memory = MEMORYSTATUSEX()
            memory.dwLength = ctypes.sizeof(memory)
            if kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
                out.update(ram_total_bytes=memory.ullTotalPhys, ram_available_bytes=memory.ullAvailPhys)
        except Exception:
            pass

        workers = self._runtime_workers()
        out["runtime_workers"] = workers
        out["runtime_worker_count"] = len(workers)
        if workers:
            out["runtime_worker_cpu_percent"] = round(sum(row["cpu_percent"] for row in workers), 1)
            rss_values = [row["rss_bytes"] for row in workers if isinstance(row.get("rss_bytes"), int)]
            out["runtime_worker_rss_bytes"] = sum(rss_values) if rss_values else None
            out["runtime_gpu_memory_mib"] = self._runtime_gpu_memory({row["pid"] for row in workers})

        if shutil.which("nvidia-smi"):
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,power.draw", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0:
                    out["gpu"] = result.stdout.strip()
                    first = result.stdout.strip().splitlines()[0].split(",") if result.stdout.strip() else []
                    if len(first) >= 5:
                        def number(value: str, integer: bool = False):
                            try:
                                parsed = float(value.strip())
                                return int(parsed) if integer else parsed
                            except (TypeError, ValueError):
                                return None
                        out["gpu_stats"] = {
                            "name": first[0].strip(), "utilization_percent": number(first[1], True),
                            "memory_used_mib": number(first[2], True), "memory_total_mib": number(first[3], True),
                            "power_w": number(first[4]),
                        }
            except (OSError, subprocess.TimeoutExpired):
                pass
        return out

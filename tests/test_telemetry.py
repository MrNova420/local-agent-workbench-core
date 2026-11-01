from types import SimpleNamespace

import telemetry as telemetry_module


class FakeProcess:
    def __init__(self, pid, name, rss, cpu):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "memory_info": SimpleNamespace(rss=rss)}
        self._cpu = cpu

    def cpu_percent(self, interval=None):
        return self._cpu


def test_runtime_worker_metrics_are_separate_from_device_gpu_metrics(monkeypatch):
    import psutil

    processes = [FakeProcess(101, "ollama_llama_server.exe", 2_000, 72.5),
                 FakeProcess(202, "llama-server.exe", 3_000, 11.0),
                 FakeProcess(303, "electron.exe", 9_000, 99.0)]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: processes)
    monkeypatch.setattr(telemetry_module.shutil, "which", lambda name: "nvidia-smi.exe" if name == "nvidia-smi" else None)
    monkeypatch.setattr(telemetry_module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="101, ollama_llama_server.exe, 512\n202, llama-server.exe, 256\n"))

    snapshot = telemetry_module.Telemetry().snapshot()

    assert snapshot["runtime_worker_cpu_percent"] == 83.5
    assert snapshot["runtime_worker_rss_bytes"] == 5_000
    assert snapshot["runtime_worker_count"] == 2
    assert snapshot["runtime_gpu_memory_mib"] == 768
    assert snapshot["runtime_gpu_attribution"] == "runtime process VRAM; not selected-model attribution"
    assert snapshot["gpu_stats"] is None

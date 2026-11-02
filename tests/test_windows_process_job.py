import os
import subprocess
import sys
import time

import psutil
import pytest

from windows_process_job import WindowsProcessJob


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")
def test_terminating_job_stops_process_and_child_started_after_assignment():
    root_code = (
        "import subprocess,sys; "
        "input(); "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid,flush=True); "
        "input()"
    )
    root = subprocess.Popen([sys.executable, "-c", root_code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    job = WindowsProcessJob.attach(root.pid)
    if job is None:
        root.kill()
        root.wait(timeout=5)
        pytest.skip("The current Windows host does not permit assigning a child process to a Job Object")
    child_pid = None
    try:
        assert root.stdin is not None and root.stdout is not None
        root.stdin.write("start\n")
        root.stdin.flush()
        child_pid = int(root.stdout.readline().strip())
        assert psutil.pid_exists(child_pid)

        assert job.terminate()
        root.wait(timeout=5)
        deadline = time.monotonic() + 5
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(child_pid)
    finally:
        job.close()
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except psutil.Error:
                pass
        if root.poll() is None:
            root.kill()
            root.wait(timeout=5)

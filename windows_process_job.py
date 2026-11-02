"""Windows Job Object support for terminating approved command process trees."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
THREAD_SUSPEND_RESUME = 0x0002


class WindowsProcessJob:
    """Own a Job Object assigned to one command root process.

    Job Objects provide process-tree lifecycle control only. They do not limit
    filesystem, registry, network, or user-token access.
    """

    def __init__(self, handle: int, kernel32: object):
        self._handle = handle
        self._kernel32 = kernel32

    @classmethod
    def attach(cls, process_id: int) -> "WindowsProcessJob | None":
        if os.name != "nt":
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        process = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, process_id)
        if not process:
            kernel32.CloseHandle(job)
            return None
        try:
            if not kernel32.AssignProcessToJobObject(job, process):
                kernel32.CloseHandle(job)
                return None
        finally:
            kernel32.CloseHandle(process)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        return cls(int(job), kernel32)

    @staticmethod
    def resume_primary_thread(process_id: int) -> bool:
        """Resume the suspended primary thread after its process joins the job."""
        if os.name != "nt":
            return True
        import psutil

        try:
            threads = psutil.Process(process_id).threads()
        except (psutil.Error, OSError):
            return False
        if not threads:
            return False

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, threads[0].id)
        if not thread:
            return False
        try:
            return kernel32.ResumeThread(thread) != 0xFFFFFFFF
        finally:
            kernel32.CloseHandle(thread)

    def terminate(self, exit_code: int = 1) -> bool:
        return bool(self._handle and self._kernel32.TerminateJobObject(self._handle, exit_code))

    def close(self) -> None:
        handle, self._handle = self._handle, 0
        if handle:
            self._kernel32.CloseHandle(handle)

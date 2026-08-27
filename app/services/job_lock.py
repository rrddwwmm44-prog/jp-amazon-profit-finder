from __future__ import annotations

import ctypes
import json
import os
import re
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class LockMetadata:
    job_name: str
    pid: int
    started_at: str
    hostname: str
    lock_id: str
    process_started_at: str | None = None


@dataclass(frozen=True)
class LockAcquireResult:
    acquired: bool
    metadata: LockMetadata | None = None
    existing: LockMetadata | None = None
    stale_recovered: bool = False


class AlreadyRunningError(RuntimeError):
    def __init__(self, existing: LockMetadata | None):
        self.existing=existing
        super().__init__("job_already_running")


class JobLock:
    """Cross-platform atomic job lock with Windows PID identity checking."""

    def __init__(self, directory: Path, job_name: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",job_name):
            raise ValueError("invalid_job_name")
        self.directory=Path(directory)
        self.job_name=job_name
        self.path=self.directory/f"{job_name}.lock"
        self.metadata: LockMetadata | None=None

    def acquire(self) -> LockAcquireResult:
        self.directory.mkdir(parents=True,exist_ok=True)
        stale_recovered=False
        for _ in range(4):
            metadata=LockMetadata(
                self.job_name,os.getpid(),datetime.now(timezone.utc).isoformat(),
                socket.gethostname(),uuid4().hex,_process_started_at(os.getpid()),
            )
            try:
                descriptor=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:
                existing=_read_metadata(self.path)
                if existing is not None and _same_process_is_alive(existing):
                    return LockAcquireResult(False,existing=existing,stale_recovered=stale_recovered)
                if not _remove_if_unchanged(self.path,existing):
                    continue
                stale_recovered=True
                continue
            try:
                payload=json.dumps(asdict(metadata),ensure_ascii=False,separators=(",",":")).encode("utf-8")
                os.write(descriptor,payload); os.fsync(descriptor)
            except Exception:
                try: self.path.unlink()
                except FileNotFoundError: pass
                raise
            finally:
                os.close(descriptor)
            self.metadata=metadata
            return LockAcquireResult(True,metadata=metadata,stale_recovered=stale_recovered)
        existing=_read_metadata(self.path)
        return LockAcquireResult(False,existing=existing,stale_recovered=stale_recovered)

    def release(self) -> bool:
        if self.metadata is None:
            return False
        current=_read_metadata(self.path)
        if current is None or current.lock_id != self.metadata.lock_id:
            self.metadata=None
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            self.metadata=None
            return False
        self.metadata=None
        return True

    def __enter__(self) -> "JobLock":
        result=self.acquire()
        if not result.acquired:
            raise AlreadyRunningError(result.existing)
        return self

    def __exit__(self,exc_type,exc,tb):
        self.release()
        return False


def _read_metadata(path: Path) -> LockMetadata | None:
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        return LockMetadata(**payload)
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return None


def _remove_if_unchanged(path: Path, expected: LockMetadata | None) -> bool:
    current=_read_metadata(path)
    if expected is None:
        if current is not None:
            return False
    elif current is None or current.lock_id != expected.lock_id:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _same_process_is_alive(metadata: LockMetadata) -> bool:
    alive,identity=_process_state(metadata.pid)
    if not alive:
        return False
    if metadata.process_started_at is None or identity is None:
        return True
    return metadata.process_started_at == identity


def _process_started_at(pid: int) -> str | None:
    alive,identity=_process_state(pid)
    return identity if alive else None


def _process_state(pid: int) -> tuple[bool,str | None]:
    if pid <= 0:
        return False,None
    if sys.platform == "win32":
        return _windows_process_state(pid)
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        return False,None
    except PermissionError:
        return True,None
    identity=None
    try:
        identity=Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError,IndexError):
        pass
    return True,identity


def _windows_process_state(pid: int) -> tuple[bool,str | None]:
    process_query_limited_information=0x1000
    kernel32=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel32.OpenProcess.argtypes=(ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong)
    kernel32.OpenProcess.restype=ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes=(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p)
    kernel32.GetProcessTimes.restype=ctypes.c_int
    kernel32.CloseHandle.argtypes=(ctypes.c_void_p,)
    kernel32.CloseHandle.restype=ctypes.c_int
    handle=kernel32.OpenProcess(process_query_limited_information,False,pid)
    if not handle:
        # Access denied means the PID exists but cannot be inspected. Treat it
        # as live conservatively; invalid parameter means it does not exist.
        return (True,None) if ctypes.get_last_error()==5 else (False,None)
    try:
        creation=ctypes.c_ulonglong(); exit_time=ctypes.c_ulonglong()
        kernel=ctypes.c_ulonglong(); user=ctypes.c_ulonglong()
        ok=kernel32.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel),ctypes.byref(user))
        return True,str(creation.value) if ok else None
    finally:
        kernel32.CloseHandle(handle)

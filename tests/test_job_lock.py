from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.config import Settings
from app.services.job_lock import AlreadyRunningError, JobLock, LockMetadata


class JobLockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/"locks"

    def test_acquire_writes_required_metadata(self):
        lock=JobLock(self.directory,"track-virtual-purchases"); result=lock.acquire()
        self.assertTrue(result.acquired); self.assertTrue(lock.path.exists())
        payload=json.loads(lock.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["job_name"],"track-virtual-purchases")
        self.assertEqual(payload["pid"],os.getpid())
        self.assertTrue(payload["started_at"]); self.assertTrue(payload["hostname"]); self.assertTrue(payload["lock_id"])
        lock.release()

    def test_release_removes_owned_lock(self):
        lock=JobLock(self.directory,"job"); lock.acquire()
        self.assertTrue(lock.release()); self.assertFalse(lock.path.exists())

    def test_double_acquire_is_rejected_for_live_pid(self):
        first=JobLock(self.directory,"job"); second=JobLock(self.directory,"job")
        first.acquire(); result=second.acquire()
        self.assertFalse(result.acquired); self.assertEqual(result.existing.pid,os.getpid()); first.release()

    def test_stale_dead_pid_is_recovered(self):
        self.directory.mkdir(parents=True)
        stale=LockMetadata("job",2147483647,"2026-01-01T00:00:00+00:00","host","stale","unknown")
        (self.directory/"job.lock").write_text(json.dumps(asdict(stale)),encoding="utf-8")
        lock=JobLock(self.directory,"job"); result=lock.acquire()
        self.assertTrue(result.acquired); self.assertTrue(result.stale_recovered); lock.release()

    def test_pid_reuse_identity_mismatch_is_recovered(self):
        self.directory.mkdir(parents=True)
        stale=LockMetadata("job",os.getpid(),"2026-01-01T00:00:00+00:00","host","stale","not-current-process")
        (self.directory/"job.lock").write_text(json.dumps(asdict(stale)),encoding="utf-8")
        lock=JobLock(self.directory,"job"); result=lock.acquire()
        self.assertTrue(result.acquired); self.assertTrue(result.stale_recovered); lock.release()

    def test_release_does_not_delete_another_owner(self):
        lock=JobLock(self.directory,"job"); lock.acquire()
        other=replace(lock.metadata,lock_id="other")
        lock.path.write_text(json.dumps(asdict(other)),encoding="utf-8")
        self.assertFalse(lock.release()); self.assertTrue(lock.path.exists())

    def test_context_manager_releases_after_exception(self):
        lock=JobLock(self.directory,"job")
        with self.assertRaisesRegex(RuntimeError,"boom"):
            with lock: raise RuntimeError("boom")
        self.assertFalse(lock.path.exists())

    def test_context_manager_reports_already_running(self):
        first=JobLock(self.directory,"job"); first.acquire()
        with self.assertRaises(AlreadyRunningError):
            with JobLock(self.directory,"job"): pass
        first.release()

    def test_atomic_concurrent_acquisition_has_one_winner(self):
        barrier=threading.Barrier(2); locks=[JobLock(self.directory,"job") for _ in range(2)]
        def acquire(lock): barrier.wait(); return lock.acquire().acquired
        with ThreadPoolExecutor(max_workers=2) as pool:
            winners=list(pool.map(acquire,locks))
        self.assertEqual(sum(winners),1)
        for lock in locks: lock.release()

    def test_windows_safe_lock_path(self):
        lock=JobLock(self.directory,"track-virtual-purchases")
        self.assertEqual(lock.path.name,"track-virtual-purchases.lock")
        self.assertNotIn(":",lock.path.name)

    def test_invalid_job_name_is_rejected(self):
        with self.assertRaises(ValueError): JobLock(self.directory,"../unsafe")

    def test_cli_already_running_is_success_and_does_not_touch_db_or_api(self):
        db_path=Path(self.temp.name)/"must-not-exist.db"
        holder=JobLock(self.directory,"track-virtual-purchases"); holder.acquire()
        env={"APP_DB_PATH":str(db_path),"APP_LOCK_DIR":str(self.directory)}
        with patch("app.config._load_dotenv"),patch.dict(os.environ,env,clear=True),patch("app.storage.db.Database.migrate",side_effect=AssertionError("DB touched")),patch("app.providers.keepa.KeepaHttpClient.get_product",side_effect=AssertionError("API called")),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["track-virtual-purchases"]),0)
        report=json.loads(output.getvalue()); self.assertEqual(report["status"],"already_running")
        self.assertEqual(report["existing_pid"],os.getpid()); self.assertFalse(db_path.exists()); holder.release()

    def test_dry_run_uses_same_lock(self):
        holder=JobLock(self.directory,"track-virtual-purchases"); holder.acquire()
        env={"APP_DB_PATH":str(Path(self.temp.name)/"dry.db"),"APP_LOCK_DIR":str(self.directory)}
        with patch("app.config._load_dotenv"),patch.dict(os.environ,env,clear=True),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["track-virtual-purchases","--dry-run"]),0)
        self.assertEqual(json.loads(output.getvalue())["status"],"already_running"); holder.release()

    def test_cli_exception_releases_lock(self):
        env={"APP_DB_PATH":str(Path(self.temp.name)/"error.db"),"APP_LOCK_DIR":str(self.directory)}
        with patch("app.config._load_dotenv"),patch.dict(os.environ,env,clear=True),patch("app.virtual_purchases.tracking.VirtualPurchaseTrackingService.run",side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError,"boom"): main(["track-virtual-purchases","--mode","mock"])
        self.assertFalse((self.directory/"track-virtual-purchases.lock").exists())

    def test_gitignore_excludes_runtime_locks(self):
        root=Path(__file__).resolve().parents[1]
        self.assertIn("data/locks/",(root/".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()

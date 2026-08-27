from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr,redirect_stdout
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch

from scripts import register_virtual_tracking_task as registration
from scripts import run_virtual_tracking as wrapper


class TaskSchedulerTests(unittest.TestCase):
    def test_plan_uses_existing_absolute_python(self):
        plan=registration.build_plan()
        self.assertTrue(Path(plan.executable).is_absolute()); self.assertTrue(Path(plan.executable).exists())
        self.assertEqual(Path(plan.executable),Path(sys.executable).resolve())

    def test_project_root_and_working_directory(self):
        plan=registration.build_plan()
        self.assertEqual(Path(plan.working_directory),Path(__file__).resolve().parents[1])
        self.assertTrue((Path(plan.working_directory)/"app").is_dir())

    def test_wrapper_is_scheduler_action(self):
        plan=registration.build_plan()
        self.assertEqual(plan.arguments,(str(registration.WRAPPER),)); self.assertTrue(registration.WRAPPER.exists())

    def test_env_or_secret_is_not_in_plan_or_xml(self):
        plan=registration.build_plan(); text=json.dumps(plan.__dict__)+registration.task_xml(plan)
        for forbidden in ("KEEPA_API_KEY","Authorization",".env","password"):
            self.assertNotIn(forbidden,text)

    def test_default_cli_is_dry_run_and_changes_nothing(self):
        with patch.object(registration,"install",side_effect=AssertionError("changed")),patch.object(registration,"uninstall",side_effect=AssertionError("changed")),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(registration.main([]),0)
        report=json.loads(output.getvalue()); self.assertEqual(report["mode"],"dry_run"); self.assertFalse(report["task_scheduler_modified"])

    def test_explicit_dry_run_changes_nothing(self):
        with patch.object(registration,"subprocess") as process,redirect_stdout(io.StringIO()):
            self.assertEqual(registration.main(["--dry-run","--time","07:30"]),0)
        process.run.assert_not_called()

    def test_install_command_is_argument_array_without_shell(self):
        plan=registration.build_plan(); runner=Mock(side_effect=[subprocess.CompletedProcess([],1,"",""),subprocess.CompletedProcess([],0,"","")])
        with patch.object(registration,"schtasks_path",return_value=r"C:\Windows\System32\schtasks.exe"):
            self.assertEqual(registration.install(plan,runner=runner),0)
        command=runner.call_args_list[1].args[0]
        self.assertEqual(command[:4],[r"C:\Windows\System32\schtasks.exe","/Create","/TN",plan.task_name])
        self.assertIn("/XML",command); self.assertFalse(runner.call_args_list[1].kwargs["shell"])

    def test_uninstall_command_targets_only_selected_task(self):
        runner=Mock(side_effect=[subprocess.CompletedProcess([],0,"",""),subprocess.CompletedProcess([],0,"","")])
        with patch.object(registration,"schtasks_path",return_value="schtasks.exe"):
            self.assertEqual(registration.uninstall("SafeTask",runner=runner),0)
        self.assertEqual(runner.call_args_list[1].args[0],["schtasks.exe","/Delete","/TN","SafeTask","/F"])

    def test_uninstall_absent_task_is_safe_noop(self):
        runner=Mock(return_value=subprocess.CompletedProcess([],1,"",""))
        with patch.object(registration,"schtasks_path",return_value="schtasks.exe"):
            self.assertEqual(registration.uninstall("SafeTask",runner=runner),0)
        self.assertEqual(runner.call_count,1)

    def test_existing_task_requires_replace(self):
        runner=Mock(return_value=subprocess.CompletedProcess([],0,"",""))
        with patch.object(registration,"schtasks_path",return_value="schtasks.exe"):
            with self.assertRaisesRegex(RuntimeError,"use_replace"): registration.install(registration.build_plan(),runner=runner)
        self.assertEqual(runner.call_count,1)

    def test_replace_is_explicit_and_adds_force(self):
        runner=Mock(side_effect=[subprocess.CompletedProcess([],0,"",""),subprocess.CompletedProcess([],0,"","")])
        with patch.object(registration,"schtasks_path",return_value="schtasks.exe"):
            self.assertEqual(registration.install(registration.build_plan(),replace=True,runner=runner),0)
        self.assertEqual(runner.call_args_list[1].args[0][-1],"/F")

    def test_replace_without_install_is_rejected(self):
        with redirect_stderr(io.StringIO()): self.assertEqual(registration.main(["--replace"]),2)

    def test_task_name_validation(self):
        self.assertEqual(registration.validate_task_name("Safe Task-1"),"Safe Task-1")
        for invalid in ("../bad",r"folder\bad","/bad",""):
            with self.assertRaises(ValueError): registration.validate_task_name(invalid)

    def test_schedule_validation(self):
        self.assertEqual(registration.validate_time("06:00"),"06:00")
        for invalid in ("6:00","24:00","12:60","noon"):
            with self.assertRaises(ValueError): registration.validate_time(invalid)

    def test_xml_has_daily_working_directory_and_safe_settings(self):
        xml=registration.task_xml(registration.build_plan(),now=datetime(2026,1,1,5,0,tzinfo=timezone.utc))
        self.assertIn("<DaysInterval>1</DaysInterval>",xml)
        self.assertIn("<WorkingDirectory>"+str(registration.PROJECT_ROOT)+"</WorkingDirectory>",xml)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",xml)
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>",xml)
        self.assertIn("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",xml)
        self.assertIn("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",xml)

    def test_windows_path_arguments_are_quoted_by_stdlib(self):
        plan=registration.build_plan(); spaced=registration.TaskPlan(plan.task_name,plan.schedule,plan.executable,(r"C:\A Folder\run.py",),plan.working_directory,plan.log_location)
        self.assertIn('"C:\\A Folder\\run.py"',registration.task_xml(spaced))

    def test_log_location_and_retention(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw)
            for number in range(35):
                path=directory/f"virtual-tracking-{number:02}.log"; path.write_text("x",encoding="utf-8")
                path.touch()
            wrapper._retain_latest(directory,30)
            self.assertEqual(len(list(directory.glob("*.log"))),30)

    def test_wrapper_runs_existing_cli_and_returns_exit_code(self):
        with tempfile.TemporaryDirectory() as raw,patch.object(wrapper.subprocess,"run",return_value=subprocess.CompletedProcess([],7,'{"status":"ok"}',"warning")) as run:
            self.assertEqual(wrapper.run_tracking(dry_run=True,log_directory=Path(raw)),7)
            command=run.call_args.args[0]
            self.assertEqual(command[:4],[str(Path(sys.executable).resolve()),"-m","app","track-virtual-purchases"])
            self.assertEqual(command[-1],"--dry-run"); self.assertEqual(run.call_args.kwargs["cwd"],wrapper.PROJECT_ROOT); self.assertFalse(run.call_args.kwargs["shell"])
            log=next(Path(raw).glob("*.log")).read_text(encoding="utf-8")
            self.assertIn("exit_code=7",log); self.assertIn('{"status":"ok"}',log); self.assertIn("warning",log)

    def test_existing_application_job_lock_remains_connected(self):
        source=(registration.PROJECT_ROOT/"app"/"cli.py").read_text(encoding="utf-8")
        self.assertIn('JobLock(settings.job_lock_dir,"track-virtual-purchases")',source)


if __name__ == "__main__": unittest.main()

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring


DEFAULT_TASK_NAME="JPAmazonProfitFinderVirtualTracking"
DEFAULT_TIME="06:00"
PROJECT_ROOT=Path(__file__).resolve().parents[1]
WRAPPER=PROJECT_ROOT/"scripts"/"run_virtual_tracking.py"
LOG_DIRECTORY=PROJECT_ROOT/"logs"/"virtual_tracking"


@dataclass(frozen=True)
class TaskPlan:
    task_name: str
    schedule: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    log_location: str
    multiple_instances: str = "IgnoreNew"
    start_when_available: bool = True
    enabled: bool = True
    description: str = "Track open virtual purchases with Keepa budget and application job lock"


def validate_task_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,99}",value):
        raise ValueError("invalid_task_name")
    return value


def validate_time(value: str) -> str:
    if not re.fullmatch(r"\d{2}:\d{2}",value): raise ValueError("invalid_schedule_time")
    hour,minute=map(int,value.split(":"))
    if hour>23 or minute>59: raise ValueError("invalid_schedule_time")
    return value


def build_plan(task_name: str = DEFAULT_TASK_NAME, schedule_time: str = DEFAULT_TIME) -> TaskPlan:
    validate_task_name(task_name); validate_time(schedule_time)
    executable=Path(sys.executable).resolve()
    if not executable.is_absolute() or not executable.exists():
        raise RuntimeError("python_executable_not_found")
    if not PROJECT_ROOT.is_absolute() or not WRAPPER.exists():
        raise RuntimeError("project_runtime_not_found")
    return TaskPlan(task_name,f"Daily {schedule_time}",str(executable),(str(WRAPPER),),str(PROJECT_ROOT),str(LOG_DIRECTORY))


def task_xml(plan: TaskPlan, *, now: datetime | None = None) -> str:
    now=now or datetime.now().astimezone()
    hour,minute=map(int,plan.schedule.split()[-1].split(":"))
    start=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if start<=now: start+=timedelta(days=1)
    namespace="http://schemas.microsoft.com/windows/2004/02/mit/task"
    task=Element("Task",{"version":"1.4","xmlns":namespace})
    registration=SubElement(task,"RegistrationInfo"); SubElement(registration,"Description").text=plan.description
    triggers=SubElement(task,"Triggers"); calendar=SubElement(triggers,"CalendarTrigger")
    # Task Scheduler interprets StartBoundary as local wall-clock time.
    SubElement(calendar,"StartBoundary").text=start.replace(tzinfo=None).isoformat(timespec="seconds")
    SubElement(calendar,"Enabled").text="true"; daily=SubElement(calendar,"ScheduleByDay"); SubElement(daily,"DaysInterval").text="1"
    principals=SubElement(task,"Principals"); principal=SubElement(principals,"Principal",{"id":"Author"})
    SubElement(principal,"LogonType").text="InteractiveToken"; SubElement(principal,"RunLevel").text="LeastPrivilege"
    settings=SubElement(task,"Settings")
    SubElement(settings,"MultipleInstancesPolicy").text=plan.multiple_instances
    SubElement(settings,"DisallowStartIfOnBatteries").text="false"
    SubElement(settings,"StopIfGoingOnBatteries").text="false"
    SubElement(settings,"StartWhenAvailable").text="true"
    SubElement(settings,"ExecutionTimeLimit").text="PT0S"
    SubElement(settings,"Enabled").text="true" if plan.enabled else "false"
    actions=SubElement(task,"Actions",{"Context":"Author"}); execute=SubElement(actions,"Exec")
    SubElement(execute,"Command").text=plan.executable
    SubElement(execute,"Arguments").text=subprocess.list2cmdline(list(plan.arguments))
    SubElement(execute,"WorkingDirectory").text=plan.working_directory
    return '<?xml version="1.0" encoding="UTF-16"?>\n'+tostring(task,encoding="unicode")


def schtasks_path() -> str:
    found=shutil.which("schtasks.exe") or shutil.which("schtasks")
    if not found: raise RuntimeError("schtasks_not_found")
    return str(Path(found).resolve())


def task_exists(task_name: str, *, runner=subprocess.run) -> bool:
    completed=runner([schtasks_path(),"/Query","/TN",task_name],capture_output=True,text=True,shell=False)
    return completed.returncode==0


def install(plan: TaskPlan, *, replace: bool = False, runner=subprocess.run) -> int:
    exists=task_exists(plan.task_name,runner=runner)
    if exists and not replace: raise RuntimeError("task_already_exists_use_replace")
    with tempfile.TemporaryDirectory() as raw:
        xml_path=Path(raw)/"virtual-tracking-task.xml"
        xml_path.write_text(task_xml(plan),encoding="utf-16")
        command=[schtasks_path(),"/Create","/TN",plan.task_name,"/XML",str(xml_path)]
        if replace: command.append("/F")
        return runner(command,capture_output=True,text=True,shell=False).returncode


def uninstall(task_name: str, *, runner=subprocess.run) -> int:
    validate_task_name(task_name)
    if not task_exists(task_name,runner=runner): return 0
    return runner([schtasks_path(),"/Delete","/TN",task_name,"/F"],capture_output=True,text=True,shell=False).returncode


def status(task_name: str, *, runner=subprocess.run) -> int:
    validate_task_name(task_name)
    completed=runner([schtasks_path(),"/Query","/TN",task_name,"/FO","LIST","/V"],capture_output=True,text=True,shell=False)
    print(completed.stdout if completed.returncode==0 else json.dumps({"exists":False,"task_name":task_name}))
    return completed.returncode


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Safely register the virtual tracking scheduled task")
    parser.add_argument("--task-name",default=DEFAULT_TASK_NAME); parser.add_argument("--time",default=DEFAULT_TIME)
    actions=parser.add_mutually_exclusive_group(); actions.add_argument("--dry-run",action="store_true"); actions.add_argument("--install",action="store_true"); actions.add_argument("--uninstall",action="store_true"); actions.add_argument("--status",action="store_true")
    parser.add_argument("--replace",action="store_true")
    args=parser.parse_args(argv)
    try:
        plan=build_plan(args.task_name,args.time)
        if args.replace and not args.install: raise ValueError("replace_requires_install")
        if args.uninstall: return uninstall(plan.task_name)
        if args.status: return status(plan.task_name)
        if args.install: return install(plan,replace=args.replace)
        output=asdict(plan); output["mode"]="dry_run"; output["task_scheduler_modified"]=False
        print(json.dumps(output,ensure_ascii=False,indent=2)); return 0
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())

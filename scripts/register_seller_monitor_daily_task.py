from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

try:
    from scripts.register_virtual_tracking_task import (
        TaskPlan, install, status, uninstall, validate_task_name, validate_time,
    )
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from register_virtual_tracking_task import (
        TaskPlan, install, status, uninstall, validate_task_name, validate_time,
    )


DEFAULT_TASK_NAME="JPAmazonProfitFinderSellerMonitorDaily"
PROPOSED_TIME="05:15"
PROJECT_ROOT=Path(__file__).resolve().parents[1]
WRAPPER=PROJECT_ROOT/"scripts"/"run_seller_monitor_daily.py"
LOG_DIRECTORY=PROJECT_ROOT/"logs"/"seller_monitor_daily"


def build_plan(task_name: str = DEFAULT_TASK_NAME, schedule_time: str = PROPOSED_TIME,
               *, enabled: bool = False) -> TaskPlan:
    validate_task_name(task_name); validate_time(schedule_time)
    executable=Path(sys.executable).resolve()
    if not executable.is_absolute() or not executable.exists() or not WRAPPER.exists():
        raise RuntimeError("project_runtime_not_found")
    return TaskPlan(
        task_name,f"Daily {schedule_time}",str(executable),(str(WRAPPER),),
        str(PROJECT_ROOT),str(LOG_DIRECTORY),enabled=enabled,
        description="Check enabled sellers and process NEW detections with Keepa budget and application job lock",
    )


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Prepare the Seller Monitor daily scheduled task")
    parser.add_argument("--task-name",default=DEFAULT_TASK_NAME)
    parser.add_argument("--time",default=PROPOSED_TIME)
    actions=parser.add_mutually_exclusive_group()
    actions.add_argument("--install",action="store_true")
    actions.add_argument("--uninstall",action="store_true")
    actions.add_argument("--status",action="store_true")
    parser.add_argument("--replace",action="store_true")
    parser.add_argument("--enabled",action="store_true",help="Explicitly enable the task when installing")
    args=parser.parse_args(argv)
    try:
        plan=build_plan(args.task_name,args.time,enabled=args.enabled)
        if args.replace and not args.install: raise ValueError("replace_requires_install")
        if args.uninstall: return uninstall(plan.task_name)
        if args.status: return status(plan.task_name)
        if args.install: return install(plan,replace=args.replace)
        output=asdict(plan); output["mode"]="dry_run"; output["task_scheduler_modified"]=False
        print(json.dumps(output,ensure_ascii=False,indent=2)); return 0
    except (ValueError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())

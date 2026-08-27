from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT=Path(__file__).resolve().parents[1]
LOG_DIRECTORY=PROJECT_ROOT/"logs"/"virtual_tracking"
MAX_LOG_FILES=30


def run_tracking(*, dry_run: bool = False, log_directory: Path = LOG_DIRECTORY) -> int:
    command=[str(Path(sys.executable).resolve()),"-m","app","track-virtual-purchases"]
    if dry_run: command.append("--dry-run")
    started_at=datetime.now().astimezone()
    completed=subprocess.run(command,cwd=PROJECT_ROOT,capture_output=True,text=True,shell=False)
    log_directory.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    log_path=log_directory/f"virtual-tracking-{stamp}.log"
    log_path.write_text(
        f"started_at={started_at.isoformat()}\n"
        f"exit_code={completed.returncode}\n"
        "stdout:\n"+completed.stdout+"\nstderr:\n"+completed.stderr,
        encoding="utf-8",
    )
    _retain_latest(log_directory,MAX_LOG_FILES)
    return completed.returncode


def _retain_latest(directory: Path,limit: int) -> None:
    logs=sorted(directory.glob("virtual-tracking-*.log"),key=lambda item:item.stat().st_mtime,reverse=True)
    for old in logs[max(0,limit):]: old.unlink()


def main(argv=None) -> int:
    import argparse
    parser=argparse.ArgumentParser(description="Run virtual purchase tracking with local logs")
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    return run_tracking(dry_run=args.dry_run)


if __name__ == "__main__": raise SystemExit(main())

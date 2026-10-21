"""Tiny portable entry point; reuse the worker and its shared dependencies."""

from pathlib import Path
import os
import subprocess
import sys


def main() -> int:
    root = Path(sys.executable).resolve().parent
    worker = root / "IbayRentalWorker.exe"
    if not worker.exists():
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "Keep Show_Dashboard.exe beside IbayRentalWorker.exe in the portable folder.",
            "iBay Rental Dashboard", 0x10,
        )
        return 1
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    log_path = reports / "dashboard.log"
    env = os.environ.copy()
    env["IBAY_DESKTOP_PARENT_PID"] = str(os.getpid())
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.call(
            [str(worker), "--dashboard-only", *sys.argv[1:]], cwd=root, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    if result:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f"Unable to start dashboard. See {log_path}\nIf the port is occupied, use --port 8502.",
            "iBay Rental Dashboard", 0x10,
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())

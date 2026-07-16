"""Portable desktop launcher for the rental scraper and dashboard."""

from __future__ import annotations

import argparse
import atexit
import os
from pathlib import Path
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from ibay_rentals.settings import PROCESSED_DIR, PROJECT_ROOT, SCHEMA_ALIGNED_IMPORT_DIR
from ibay_rentals.sources import SOURCE_IBAY, SOURCE_LABELS, SOURCE_PROPERTY_MV


APP_TITLE = "iBay Rental Dashboard"
DEFAULT_PORT = 8501
STREAMLIT_BIND_ADDRESS = "127.0.0.1"
LOCAL_DASHBOARD_HOST = STREAMLIT_BIND_ADDRESS
PARENT_PID_ENV = "IBAY_DESKTOP_PARENT_PID"
PROCESSED_DATASETS = (
    PROCESSED_DIR / "ibay_rentals_master.csv.gz",
    PROCESSED_DIR / "ibay_rentals_master.csv",
    PROCESSED_DIR / "ibay_rentals_master.parquet",
)
_LAUNCHED_PROCESSES: set[subprocess.Popen[str]] = set()
_LAUNCHED_PROCESS_LOCK = threading.Lock()
_DASHBOARD_WAS_STARTED = False


def _bundled_root() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle).resolve() if bundle else PROJECT_ROOT


def _dashboard_app_path() -> Path:
    return _bundled_root() / "dashboard" / "app.py"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _worker_executable() -> str:
    worker = Path(sys.executable).with_name("IbayRentalWorker.exe")
    return str(worker if worker.exists() else Path(sys.executable))


def _python_command(*args: str) -> list[str]:
    if _is_frozen():
        return [_worker_executable(), "--cli", *args]
    return [sys.executable, "-m", "ibay_rentals", *args]


def _source_command(command_name: str, max_listings: int, sources: list[str]) -> list[str]:
    if command_name not in {"scrape", "pipeline"}:
        raise ValueError(f"Unsupported source command: {command_name}")
    command = _python_command(command_name, "--max-listings", str(max_listings))
    for source in sources:
        command.extend(["--source", source])
    return command


def _streamlit_command(port: int = DEFAULT_PORT) -> list[str]:
    if _is_frozen():
        return [_worker_executable(), "--streamlit", "--port", str(port)]
    return [sys.executable, "-m", "ibay_rentals.desktop", "--streamlit", "--port", str(port)]


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def _register_process(process: subprocess.Popen[str]) -> None:
    with _LAUNCHED_PROCESS_LOCK:
        _LAUNCHED_PROCESSES.add(process)


def _unregister_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    with _LAUNCHED_PROCESS_LOCK:
        _LAUNCHED_PROCESSES.discard(process)


def _stop_process_tree(process: subprocess.Popen[str] | None) -> None:
    """Stop a launched worker and any child processes it created."""
    if process is None or process.poll() is not None:
        _unregister_process(process)
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            startupinfo=_startupinfo(),
        )
        _unregister_process(process)
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    _unregister_process(process)


def _address_uses_port(address: str, port: int) -> bool:
    return address.rsplit(":", 1)[-1] == str(port)


def _kill_windows_listeners_on_port(port: int) -> None:
    if os.name != "nt":
        return
    try:
        output = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_startupinfo(),
        )
    except Exception:
        return

    pids: set[int] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3].upper() != "LISTENING":
            continue
        if _address_uses_port(parts[1], port):
            try:
                pids.add(int(parts[-1]))
            except ValueError:
                continue

    for pid in pids:
        if pid == os.getpid():
            continue
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            startupinfo=_startupinfo(),
        )


def _cleanup_launched_processes() -> None:
    with _LAUNCHED_PROCESS_LOCK:
        processes = list(_LAUNCHED_PROCESSES)
    for process in processes:
        _stop_process_tree(process)
    if _DASHBOARD_WAS_STARTED:
        _kill_windows_listeners_on_port(DEFAULT_PORT)


def _install_shutdown_handlers() -> None:
    def handle_shutdown(signum: int, frame: object) -> None:
        _cleanup_launched_processes()
        raise SystemExit(128 + signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, handle_shutdown)
        except (OSError, ValueError):
            continue


def _parent_process_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not process:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(process, 0) == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _start_parent_monitor() -> None:
    parent_pid = os.environ.get(PARENT_PID_ENV)
    if not parent_pid:
        return
    try:
        pid = int(parent_pid)
    except ValueError:
        return

    def monitor() -> None:
        while _parent_process_exists(pid):
            time.sleep(1)
        os._exit(0)

    threading.Thread(target=monitor, daemon=True).start()


atexit.register(_cleanup_launched_processes)


class DesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x560")
        self.minsize(720, 500)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_process: subprocess.Popen[str] | None = None
        self.dashboard_process: subprocess.Popen[str] | None = None

        self.status_var = tk.StringVar(value="Ready")
        self.max_listings_var = tk.IntVar(value=0)
        self.source_vars = {
            SOURCE_IBAY: tk.BooleanVar(value=True),
            SOURCE_PROPERTY_MV: tk.BooleanVar(value=True),
        }

        self._build_ui()
        self._refresh_dataset_status()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        header = ttk.Frame(self, padding=(18, 16, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=f"Data folder: {PROJECT_ROOT / 'data'}",
            foreground="#555555",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        controls = ttk.Frame(self, padding=(18, 8))
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(6, weight=1)

        ttk.Label(controls, text="Max listings (0 = full)").grid(row=0, column=0, sticky="w", padx=(0, 8))
        max_box = ttk.Spinbox(controls, from_=0, to=10000, width=8, textvariable=self.max_listings_var)
        max_box.grid(row=0, column=1, sticky="w", padx=(0, 14))

        sources_box = ttk.LabelFrame(controls, text="Data sources", padding=(8, 4))
        sources_box.grid(row=0, column=2, sticky="w", padx=(0, 14))
        ttk.Checkbutton(sources_box, text=SOURCE_LABELS[SOURCE_IBAY], variable=self.source_vars[SOURCE_IBAY]).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
        )
        ttk.Checkbutton(
            sources_box,
            text=SOURCE_LABELS[SOURCE_PROPERTY_MV],
            variable=self.source_vars[SOURCE_PROPERTY_MV],
        ).grid(row=0, column=1, sticky="w")

        self.pipeline_button = ttk.Button(controls, text="Run Pipeline", command=self._start_pipeline)
        self.pipeline_button.grid(row=0, column=3, padx=4)
        self.import_button = ttk.Button(controls, text="Import Data", command=self._import_schema_aligned_file)
        self.import_button.grid(row=0, column=4, padx=4)
        self.dashboard_button = ttk.Button(controls, text="Show Dashboard", command=self._show_dashboard)
        self.dashboard_button.grid(row=0, column=5, padx=4, sticky="w")
        self.stop_button = ttk.Button(controls, text="Stop Task", command=self._stop_worker, state="disabled")
        self.stop_button.grid(row=0, column=6, padx=(4, 0), sticky="w")

        progress_area = ttk.Frame(self, padding=(18, 8))
        progress_area.grid(row=2, column=0, sticky="ew")
        progress_area.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(progress_area, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_area, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.dataset_var = tk.StringVar()
        ttk.Label(self, textvariable=self.dataset_var, padding=(18, 0, 18, 6), foreground="#555555").grid(
            row=3,
            column=0,
            sticky="ew",
        )

        log_frame = ttk.Frame(self, padding=(18, 8, 18, 16))
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = tk.Text(log_frame, wrap="word", height=16, state="disabled", font=("Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def _refresh_dataset_status(self) -> None:
        existing = [path for path in PROCESSED_DATASETS if path.exists()]
        if not existing:
            self.dataset_var.set("Processed dataset: not found")
            return
        newest = max(existing, key=lambda path: path.stat().st_mtime)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest.stat().st_mtime))
        self.dataset_var.set(f"Processed dataset: {newest.name} updated {timestamp}")

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        state = "disabled" if busy else "normal"
        self.pipeline_button.configure(state=state)
        self.import_button.configure(state=state)
        self.stop_button.configure(state="normal" if busy else "disabled")
        if status:
            self.status_var.set(status)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _run_command(self, command: list[str], label: str) -> None:
        if self.worker_process and self.worker_process.poll() is None:
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return

        self._set_busy(True, f"{label} running...")
        self._append_log(f"\n[{label}] {' '.join(command)}\n")

        def worker() -> None:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    startupinfo=_startupinfo(),
                )
                self.events.put(("process", process))
                _register_process(process)
                assert process.stdout is not None
                for line in process.stdout:
                    self.events.put(("log", line))
                return_code = process.wait()
                _unregister_process(process)
                self.events.put(("done", (label, return_code)))
            except Exception as exc:
                self.events.put(("error", (label, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _selected_source_command(self, command_name: str) -> list[str] | None:
        try:
            max_listings = int(self.max_listings_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror(APP_TITLE, "Max listings must be zero or a positive number.")
            return
        if max_listings < 0:
            messagebox.showerror(APP_TITLE, "Max listings must be zero or a positive number.")
            return
        selected_sources = [source for source, variable in self.source_vars.items() if variable.get()]
        if not selected_sources:
            messagebox.showerror(APP_TITLE, "Select at least one data source for web scraping.")
            return None
        return _source_command(command_name, max_listings, selected_sources)

    def _start_pipeline(self) -> None:
        command = self._selected_source_command("pipeline")
        if command is not None:
            self._run_command(command, "Pipeline")

    def _import_schema_aligned_file(self) -> None:
        if self.worker_process and self.worker_process.poll() is None:
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return
        selected = filedialog.askopenfilename(
            title="Import schema-aligned dataset",
            filetypes=[
                ("Schema-aligned datasets", "*.xlsx *.csv"),
                ("Excel workbook", "*.xlsx"),
                ("CSV file", "*.csv"),
            ],
        )
        if not selected:
            return
        source = Path(selected)
        if source.suffix.casefold() not in {".xlsx", ".csv"}:
            messagebox.showerror(APP_TITLE, "Select a schema-aligned .xlsx or .csv file.")
            return
        try:
            SCHEMA_ALIGNED_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
            destination = SCHEMA_ALIGNED_IMPORT_DIR / source.name
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            self._append_log(f"\n[Import] {source} -> {destination}\n")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Unable to import dataset:\n{exc}")
            return
        self._run_command(_python_command("preprocess"), "Import and preprocess")

    def _show_dashboard(self) -> None:
        global _DASHBOARD_WAS_STARTED

        local_url = f"http://{LOCAL_DASHBOARD_HOST}:{DEFAULT_PORT}"
        app_path = _dashboard_app_path()
        if not app_path.exists():
            messagebox.showerror(APP_TITLE, f"Dashboard file was not found:\n{app_path}")
            return
        if not any(path.exists() for path in PROCESSED_DATASETS):
            messagebox.showwarning(APP_TITLE, "No processed dataset was found. Run Pipeline first.")
            return
        if self.dashboard_process and self.dashboard_process.poll() is None:
            webbrowser.open(local_url)
            self.status_var.set("Dashboard is already running.")
            return

        command = _streamlit_command(DEFAULT_PORT)
        self._append_log(f"\n[Dashboard] {' '.join(command)}\n")
        env = os.environ.copy()
        env[PARENT_PID_ENV] = str(os.getpid())
        try:
            self.dashboard_process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=_startupinfo(),
            )
            _register_process(self.dashboard_process)
            _DASHBOARD_WAS_STARTED = True
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Unable to start dashboard:\n{exc}")
            return
        self.status_var.set(f"Dashboard starting on {local_url}; access is limited to this computer.")
        self.after(1500, lambda: webbrowser.open(local_url))

    def _stop_worker(self) -> None:
        if self.worker_process and self.worker_process.poll() is None:
            _stop_process_tree(self.worker_process)
            self.status_var.set("Stopping task...")

    def _drain_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "process":
                self.worker_process = payload  # type: ignore[assignment]
            elif event == "log":
                self._append_log(str(payload))
            elif event == "done":
                label, return_code = payload  # type: ignore[misc]
                self.worker_process = None
                self._set_busy(False, f"{label} finished." if return_code == 0 else f"{label} failed.")
                self._append_log(f"[{label}] exited with code {return_code}\n")
                self._refresh_dataset_status()
                if return_code != 0:
                    messagebox.showerror(APP_TITLE, f"{label} failed. Check the progress log for details.")
            elif event == "error":
                label, message = payload  # type: ignore[misc]
                self.worker_process = None
                self._set_busy(False, f"{label} failed.")
                self._append_log(f"[{label}] {message}\n")
                messagebox.showerror(APP_TITLE, f"{label} failed:\n{message}")
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        if self.worker_process and self.worker_process.poll() is None:
            if not messagebox.askyesno(APP_TITLE, "A task is running. Stop it and close the app?"):
                return
        _cleanup_launched_processes()
        self.worker_process = None
        self.dashboard_process = None
        self.destroy()


def run_streamlit(port: int) -> None:
    global _DASHBOARD_WAS_STARTED

    _DASHBOARD_WAS_STARTED = True
    _start_parent_monitor()
    app_path = _dashboard_app_path()
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--global.developmentMode=false",
        f"--server.address={STREAMLIT_BIND_ADDRESS}",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    from streamlit.web.cli import main as streamlit_main

    streamlit_main()


def _run_cli(argv: list[str]) -> None:
    from ibay_rentals.cli import main as cli_main

    cli_main(argv)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--cli"]:
        _run_cli(argv[1:])
        return
    if argv[:1] == ["--streamlit"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--port", type=int, default=DEFAULT_PORT)
        args = parser.parse_args(argv[1:])
        run_streamlit(args.port)
        return

    _install_shutdown_handlers()
    app = DesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()

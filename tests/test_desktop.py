from __future__ import annotations

import subprocess

from ibay_rentals import desktop


class _FakeProcess:
    pid = 12345

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> None:
        return None

    def kill(self) -> None:
        self.killed = True


def test_stop_process_tree_uses_taskkill_for_windows_process_tree(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(desktop.os, "name", "nt")
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    monkeypatch.setattr(desktop, "_startupinfo", lambda: None)

    desktop._stop_process_tree(_FakeProcess())  # type: ignore[arg-type]

    assert calls
    assert calls[0][0] == ["taskkill", "/PID", "12345", "/T", "/F"]
    assert calls[0][1]["check"] is False


def test_stop_process_tree_terminates_non_windows_process(monkeypatch) -> None:
    process = _FakeProcess()

    monkeypatch.setattr(desktop.os, "name", "posix")

    desktop._stop_process_tree(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is False


def test_streamlit_command_uses_project_wrapper_when_not_frozen(monkeypatch) -> None:
    monkeypatch.setattr(desktop.sys, "frozen", False, raising=False)

    command = desktop._streamlit_command(8765)

    assert command[:3] == [desktop.sys.executable, "-m", "ibay_rentals.desktop"]
    assert command[-3:] == ["--streamlit", "--port", "8765"]


def test_streamlit_dashboard_binds_to_loopback_only() -> None:
    assert desktop.STREAMLIT_BIND_ADDRESS == "127.0.0.1"
    assert desktop.LOCAL_DASHBOARD_HOST == "127.0.0.1"


def test_pipeline_source_command_uses_selected_sources(monkeypatch) -> None:
    monkeypatch.setattr(desktop.sys, "frozen", False, raising=False)

    command = desktop._source_command("pipeline", 25, ["ibay", "property_mv"])

    assert command[:3] == [desktop.sys.executable, "-m", "ibay_rentals"]
    assert command[3:] == [
        "pipeline",
        "--max-listings",
        "25",
        "--source",
        "ibay",
        "--source",
        "property_mv",
    ]


def test_kill_windows_listeners_on_port_kills_matching_listener(monkeypatch) -> None:
    commands = []
    netstat_output = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:8501           0.0.0.0:0              LISTENING       1111
  TCP    [::]:8501              [::]:0                 LISTENING       2222
  TCP    127.0.0.1:9000         0.0.0.0:0              LISTENING       3333
"""

    def fake_check_output(command, **kwargs):
        return netstat_output

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(desktop.os, "name", "nt")
    monkeypatch.setattr(desktop.os, "getpid", lambda: 2222)
    monkeypatch.setattr(desktop.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    monkeypatch.setattr(desktop, "_startupinfo", lambda: None)

    desktop._kill_windows_listeners_on_port(8501)

    assert commands == [["taskkill", "/PID", "1111", "/T", "/F"]]

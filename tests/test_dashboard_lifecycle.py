import socket
import subprocess
import sys
import time

from websockets.sync.client import connect

from ibay_rentals.dashboard_lifecycle import IdleShutdown


def test_idle_shutdown_allows_refresh_and_limits_startup_wait():
    policy = IdleShutdown(grace_seconds=15, startup_seconds=120)
    policy.started_at = 0
    assert not policy.expired(False, 119)
    assert policy.expired(False, 120)
    assert not policy.expired(True, 121)
    assert not policy.expired(False, 135)
    assert not policy.expired(True, 136)
    assert not policy.expired(False, 150)
    assert policy.expired(False, 151)


def test_real_streamlit_releases_port_after_last_websocket_closes(tmp_path):
    app = tmp_path / "app.py"
    app.write_text("import streamlit as st\nst.write('Lifecycle test')\n")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    code = '''
import sys
import ibay_rentals.dashboard_lifecycle as lifecycle
from streamlit.web import bootstrap
policy = lifecycle.IdleShutdown
lifecycle.IdleShutdown = lambda: policy(grace_seconds=2, startup_seconds=30)
lifecycle.install_browser_lifecycle(int(sys.argv[2]), open_browser=False)
flags = {
    "server.port": int(sys.argv[2]), "server.address": "127.0.0.1",
    "server.headless": True, "server.fileWatcherType": "none",
    "browser.gatherUsageStats": False, "global.developmentMode": False,
}
bootstrap.load_config_options(flag_options=flags)
bootstrap.run(sys.argv[1], False, [], flags)
'''
    log = tmp_path / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen([sys.executable, "-c", code, str(app), str(port)], stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 25
            while True:
                try:
                    first = connect(f"ws://127.0.0.1:{port}/_stcore/stream", subprotocols=["streamlit"])
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError(log.read_text())
                    time.sleep(.2)
            with first:
                with connect(f"ws://127.0.0.1:{port}/_stcore/stream", subprotocols=["streamlit"]):
                    time.sleep(1.2)
                time.sleep(3)
                assert process.poll() is None, log.read_text()
            assert process.wait(timeout=15) == 0, log.read_text()
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

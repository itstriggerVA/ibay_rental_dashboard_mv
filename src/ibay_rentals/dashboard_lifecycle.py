"""Stop an owned Streamlit server after its last browser session disconnects."""

from __future__ import annotations

import asyncio
import time
import webbrowser


class IdleShutdown:
    def __init__(self, grace_seconds: float = 15, startup_seconds: float = 120):
        self.grace_seconds = grace_seconds
        self.startup_seconds = startup_seconds
        self.started_at = time.monotonic()
        self.last_connected_at: float | None = None

    def expired(self, connected: bool, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if connected:
            self.last_connected_at = now
            return False
        if self.last_connected_at is None:
            return now - self.started_at >= self.startup_seconds
        return now - self.last_connected_at >= self.grace_seconds


def install_browser_lifecycle(port: int, *, open_browser: bool) -> None:
    # Streamlit has no public last-tab-close callback. Keep the bootstrap
    # integration isolated here and exercise it against the packaged version.
    from streamlit.web import bootstrap
    from streamlit.runtime import Runtime
    from streamlit.runtime.runtime import RuntimeState

    original = bootstrap._on_server_start

    def on_start(server):
        original(server)
        policy = IdleShutdown()

        async def monitor():
            while True:
                connected = Runtime.instance().state == RuntimeState.ONE_OR_MORE_SESSIONS_CONNECTED
                if policy.expired(connected):
                    server.stop()
                    return
                await asyncio.sleep(1)

        asyncio.create_task(monitor())
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")

    bootstrap._on_server_start = on_start

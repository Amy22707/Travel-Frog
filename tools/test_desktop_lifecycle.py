"""Desktop watchdog behavior after a computer sleep and a normal window close."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop.app import DesktopGame


class DesktopLifecycleTests(unittest.TestCase):
    def test_resume_allows_browser_to_send_a_fresh_heartbeat(self):
        app = SimpleNamespace(last_watch=100, last_ping=100, closed_at=0,
                              game_open=True, busy=False, root=Mock(), close=Mock(), watch=Mock())
        with patch("desktop.app.time.monotonic", return_value=1000):
            DesktopGame.watch(app)
        app.close.assert_not_called()
        self.assertEqual(app.last_ping, 1000)
        app.root.after.assert_called_once()

    def test_closed_window_still_stops_the_service(self):
        app = SimpleNamespace(last_watch=990, last_ping=990, closed_at=995,
                              game_open=True, busy=False, root=Mock(), close=Mock(), watch=Mock())
        with patch("desktop.app.time.monotonic", return_value=1000):
            DesktopGame.watch(app)
        app.close.assert_called_once_with(force=True)

    def test_export_in_progress_keeps_the_watchdog_running(self):
        app = SimpleNamespace(last_watch=990, last_ping=990, closed_at=995,
                              game_open=True, busy=True, root=Mock(), close=Mock(), watch=Mock())
        with patch("desktop.app.time.monotonic", return_value=1000):
            DesktopGame.watch(app)
        app.close.assert_not_called()
        app.root.after.assert_called_once()


if __name__ == "__main__":
    unittest.main()

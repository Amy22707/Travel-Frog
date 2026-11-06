"""Local server lifetime follows the native game window."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from desktop.app import DesktopGame


class DesktopLifecycleTests(unittest.TestCase):
    def test_running_window_keeps_server_alive(self):
        app = SimpleNamespace(game_window=Mock(poll=Mock(return_value=None)),
                              game_open=True, busy=False, root=Mock(), close=Mock(), watch=Mock())
        DesktopGame.watch(app)
        app.close.assert_not_called()
        app.root.after.assert_called_once()

    def test_closed_window_still_stops_the_service(self):
        app = SimpleNamespace(game_window=Mock(poll=Mock(return_value=0)),
                              game_open=True, busy=False, root=Mock(), close=Mock(), watch=Mock())
        DesktopGame.watch(app)
        app.close.assert_called_once_with(force=True)

    def test_export_in_progress_keeps_the_watchdog_running(self):
        app = SimpleNamespace(game_window=Mock(poll=Mock(return_value=0)),
                              game_open=True, busy=True, root=Mock(), close=Mock(), watch=Mock())
        DesktopGame.watch(app)
        app.close.assert_not_called()
        app.root.after.assert_called_once()


if __name__ == "__main__":
    unittest.main()

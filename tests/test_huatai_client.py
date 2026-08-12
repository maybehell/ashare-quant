import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ashare_quant.huatai_client import (
    build_huatai_client_status,
    request_huatai_client_window_wake,
    restore_huatai_trading_window,
)


class HuataiClientTests(unittest.TestCase):
    def config(self) -> dict:
        executable = Path.cwd() / "test_outputs" / "huatai_client" / "hexin.exe"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        return {
            "paths": {"output_dir": str(executable.parent)},
            "desktop_client": {
                "executable": str(executable),
                "auto_start": True,
                "startup_wait_seconds": 0.5,
            },
        }

    def test_restore_minimized_window_does_not_depend_on_descendants(self):
        executable = Path(self.config()["desktop_client"]["executable"]).resolve()
        process_paths = {
            101: executable,
            202: executable.parent / "xiadan.exe",
        }

        class Handle:
            def __init__(self, pid):
                self.pid = pid

            def Close(self):
                pass

        class Window:
            minimized = True
            visible = False

            def process_id(self):
                return 101

            def is_minimized(self):
                return self.minimized

            def is_visible(self):
                return self.visible

            def restore(self):
                self.minimized = False
                self.visible = True

            def descendants(self):
                raise AssertionError("minimized descendants must not be inspected")

        window = Window()
        win32api = SimpleNamespace(OpenProcess=lambda _access, _inherit, pid: Handle(pid))
        win32con = SimpleNamespace(PROCESS_QUERY_INFORMATION=1, PROCESS_VM_READ=2)
        win32process = SimpleNamespace(
            EnumProcesses=lambda: [101, 202],
            GetModuleFileNameEx=lambda handle, _module: str(process_paths[handle.pid]),
        )
        pywinauto = SimpleNamespace(
            Desktop=lambda backend: SimpleNamespace(
                windows=lambda visible_only: [window]
            )
        )
        with patch.dict(
            sys.modules,
            {
                "win32api": win32api,
                "win32con": win32con,
                "win32process": win32process,
                "pywinauto": pywinauto,
            },
        ):
            restored, message = restore_huatai_trading_window(
                executable, pause=lambda _seconds: None
            )

        self.assertTrue(restored)
        self.assertIn("verified", message)

    def test_restore_minimized_window_fails_closed_on_multiple_roots(self):
        executable = Path(self.config()["desktop_client"]["executable"]).resolve()
        process_paths = {
            101: executable,
            202: executable.parent / "xiadan.exe",
        }

        class Handle:
            def __init__(self, pid):
                self.pid = pid

            def Close(self):
                pass

        class Window:
            restore = Mock()

            def process_id(self):
                return 101

            def is_minimized(self):
                return True

        windows = [Window(), Window()]
        win32api = SimpleNamespace(OpenProcess=lambda _access, _inherit, pid: Handle(pid))
        win32con = SimpleNamespace(PROCESS_QUERY_INFORMATION=1, PROCESS_VM_READ=2)
        win32process = SimpleNamespace(
            EnumProcesses=lambda: [101, 202],
            GetModuleFileNameEx=lambda handle, _module: str(process_paths[handle.pid]),
        )
        pywinauto = SimpleNamespace(
            Desktop=lambda backend: SimpleNamespace(
                windows=lambda visible_only: windows
            )
        )
        with patch.dict(
            sys.modules,
            {
                "win32api": win32api,
                "win32con": win32con,
                "win32process": win32process,
                "pywinauto": pywinauto,
            },
        ):
            restored, message = restore_huatai_trading_window(
                executable, pause=lambda _seconds: None
            )

        self.assertFalse(restored)
        self.assertIn("found 2", message)
        for window in windows:
            window.restore.assert_not_called()
    def test_windowless_wake_uses_only_pinned_executable(self):
        config = self.config()
        config["desktop_client"]["wake_windowless_on_refresh"] = True
        launcher = Mock()
        restorer = Mock(return_value=(False, "no minimized trading root"))

        attempted, message = request_huatai_client_window_wake(
            config,
            launcher=launcher,
            restorer=restorer,
            process_counts=lambda: {"hexin.exe": 0, "xiadan.exe": 0},
        )

        self.assertTrue(attempted)
        self.assertIn("wake requested", message)
        executable = Path(config["desktop_client"]["executable"]).resolve()
        restorer.assert_called_once_with(executable)
        launcher.assert_called_once_with(
            [str(executable)],
            cwd=str(executable.parent),
            shell=False,
        )

    def test_window_wake_opens_trading_module_before_duplicate_launch(self):
        config = self.config()
        config["desktop_client"]["wake_windowless_on_refresh"] = True
        config["desktop_client"]["open_trading_module_on_refresh"] = True
        launcher = Mock()
        restorer = Mock(return_value=(False, "not minimized"))
        opener = Mock(return_value=(True, "F12 requested"))

        attempted, message = request_huatai_client_window_wake(
            config,
            launcher=launcher,
            restorer=restorer,
            module_opener=opener,
            process_counts=lambda: {"hexin.exe": 1, "xiadan.exe": 1},
        )

        self.assertTrue(attempted)
        self.assertEqual(message, "F12 requested")
        opener.assert_called_once_with(Path(config["desktop_client"]["executable"]).resolve())
        launcher.assert_not_called()

    def test_window_wake_blocks_duplicate_launch_when_processes_exist(self):
        config = self.config()
        config["desktop_client"]["wake_windowless_on_refresh"] = True
        launcher = Mock()

        attempted, message = request_huatai_client_window_wake(
            config,
            launcher=launcher,
            restorer=Mock(return_value=(False, "not minimized")),
            process_counts=lambda: {"hexin.exe": 1, "xiadan.exe": 1},
        )

        self.assertFalse(attempted)
        self.assertIn("duplicate launch blocked", message)
        launcher.assert_not_called()

    def test_window_wake_restores_existing_trading_window_before_launch(self):
        config = self.config()
        config["desktop_client"]["wake_windowless_on_refresh"] = True
        launcher = Mock()
        restorer = Mock(return_value=(True, "restored trading root"))

        attempted, message = request_huatai_client_window_wake(
            config, launcher=launcher, restorer=restorer
        )

        self.assertTrue(attempted)
        self.assertEqual(message, "restored trading root")
        restorer.assert_called_once_with(Path(config["desktop_client"]["executable"]).resolve())
        launcher.assert_not_called()

    def test_windowless_wake_is_disabled_by_default(self):
        launcher = Mock()
        attempted, message = request_huatai_client_window_wake(
            self.config(), launcher=launcher
        )
        self.assertFalse(attempted)
        self.assertIn("disabled", message)
        launcher.assert_not_called()

    def test_running_trading_process_is_ready_without_launch(self):
        launcher = Mock()
        status = build_huatai_client_status(
            self.config(),
            start_if_needed=True,
            process_counts=lambda: {"hexin.exe": 1, "xiadan.exe": 1},
            launcher=launcher,
        )
        self.assertEqual(status.loc[0, "status"], "trading_process_ready")
        launcher.assert_not_called()

    def test_offline_client_starts_only_when_explicitly_requested(self):
        launcher = Mock()
        counts = iter([
            {"hexin.exe": 0, "xiadan.exe": 0},
            {"hexin.exe": 1, "xiadan.exe": 0},
        ])
        status = build_huatai_client_status(
            self.config(),
            start_if_needed=True,
            process_counts=lambda: next(counts),
            launcher=launcher,
            pause=lambda _: None,
        )
        self.assertEqual(status.loc[0, "status"], "started_login_required")
        self.assertTrue(bool(status.loc[0, "start_attempted"]))
        launcher.assert_called_once()
        self.assertFalse(bool(launcher.call_args.kwargs["shell"]))

    def test_status_check_does_not_start_client(self):
        launcher = Mock()
        status = build_huatai_client_status(
            self.config(),
            start_if_needed=False,
            process_counts=lambda: {"hexin.exe": 0, "xiadan.exe": 0},
            launcher=launcher,
        )
        self.assertEqual(status.loc[0, "status"], "installed_not_running")
        self.assertTrue(bool(status.loc[0, "sha256_match"]))
        launcher.assert_not_called()

    def test_sha256_mismatch_blocks_start_even_when_process_names_exist(self):
        config = self.config()
        config["desktop_client"]["executable_sha256"] = "0" * 64
        launcher = Mock()

        status = build_huatai_client_status(
            config,
            start_if_needed=True,
            process_counts=lambda: {"hexin.exe": 1, "xiadan.exe": 1},
            launcher=launcher,
        )

        self.assertEqual(status.loc[0, "status"], "client_integrity_mismatch")
        self.assertFalse(bool(status.loc[0, "sha256_match"]))
        launcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()

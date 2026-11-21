import unittest
from unittest.mock import patch

import adb
from session_service import ServiceSettings, SessionService, StartRequest
from sessions import SessionStore


class BatteryAdbTests(unittest.TestCase):
    def test_commands_return_stdout_unchanged(self):
        cases = [
            (lambda: adb.dumpsys_battery("device"), ["dumpsys", "battery"]),
            (lambda: adb.reset_batterystats("device"), ["dumpsys", "batterystats", "--reset"]),
            (lambda: adb.dumpsys_batterystats("device", "com.example.app"),
             ["dumpsys", "batterystats", "--charged", "com.example.app"]),
        ]
        for call, command in cases:
            with patch("adb.run_adb", return_value="raw\n") as run:
                self.assertEqual(call(), "raw\n")
            self.assertEqual(run.call_args.args[0], ["-s", "device", "shell", *command])

    def test_package_name_is_validated_before_running_adb(self):
        with patch("adb.run_adb") as run, self.assertRaises(adb.InvalidPackageNameError):
            adb.dumpsys_batterystats("device", "x; reboot")
        run.assert_not_called()


class BatterySnapshotTests(unittest.TestCase):
    def snapshot(self, package, before, fail=None):
        # Fresh store per call: a device may have only one unfinished session.
        self.store = SessionStore()
        self.service = SessionService(self.store, "adb", ServiceSettings(15, 30))
        session = self.store.create("device", 15, package)
        request = StartRequest("device", 15, package, 10123 if package else None)

        def fake(name):
            def run(*args):
                if name == fail:
                    raise adb.AdbError("unsupported")
                return name
            return run

        with (
            patch("adb.boottime_ns", return_value=5),
            patch("adb.dumpsys_battery", side_effect=fake("battery")),
            patch("adb.reset_batterystats", side_effect=fake("reset")),
            patch("adb.dumpsys_batterystats", side_effect=fake("stats")),
        ):
            self.service._snapshot_battery(session.id, request, before=before)
        return self.service.get_battery(session.id)

    def test_before_and_after_names(self):
        before = self.snapshot("com.example.app", before=True)
        self.assertEqual(list(before), ["batterystats_reset", "battery_before"])
        after = self.snapshot("com.example.app", before=False)
        self.assertEqual(list(after), ["batterystats", "battery_after"])
        self.assertEqual(after["batterystats"]["stdout"], "stats")
        self.assertEqual(after["battery_after"]["boottime_start_ns"], 5)

    def test_batterystats_skipped_without_package(self):
        self.assertEqual(list(self.snapshot(None, before=True)), ["battery_before"])
        self.assertEqual(list(self.snapshot(None, before=False)), ["battery_after"])

    def test_failed_snapshot_keeps_error_and_continues(self):
        after = self.snapshot("com.example.app", before=False, fail="stats")
        self.assertEqual(after["batterystats"]["error"], "AdbError: unsupported")
        self.assertIsNone(after["batterystats"]["stdout"])
        self.assertEqual(after["battery_after"]["stdout"], "battery")


if __name__ == "__main__":
    unittest.main()

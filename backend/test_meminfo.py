import threading
import unittest
from unittest.mock import patch

import adb
from session_service import ServiceSettings, SessionService
from sessions import SessionStore


class MeminfoAdbTests(unittest.TestCase):
    def test_uptime_is_converted_to_nanoseconds(self):
        self.assertEqual(adb.parse_uptime_ns("12345.67 54321.00\n"), 12_345_670_000_000)

    def test_malformed_uptime_raises(self):
        for text in ("", "abc 1.0", "12345"):
            with self.assertRaises(adb.AdbError):
                adb.parse_uptime_ns(text)

    def test_dumpsys_stdout_is_returned_unchanged(self):
        stdout = "** MEMINFO in pid 12 [com.example.app] **\n    TOTAL PSS: 1234\n"
        with patch("adb.run_adb", return_value=stdout) as run:
            self.assertEqual(adb.dumpsys_meminfo("device", "com.example.app"), stdout)
        self.assertEqual(run.call_args.args[0], [
            "-s", "device", "shell", "dumpsys", "meminfo", "-s", "--package", "com.example.app",
        ])

    def test_package_name_is_validated_before_running_adb(self):
        with patch("adb.run_adb") as run, self.assertRaises(adb.InvalidPackageNameError):
            adb.dumpsys_meminfo("device", "x; reboot")
        run.assert_not_called()


class MeminfoSamplerTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.service = SessionService(self.store, "adb", ServiceSettings(15, 30, 0.01))
        self.session = self.store.create("device", 15, "com.example.target")

    def test_interval_must_be_positive(self):
        with self.assertRaises(ValueError):
            ServiceSettings(15, 30, 0)

    def test_samples_are_stored_with_timestamps_until_done(self):
        done = threading.Event()
        calls = []

        def fake_dumpsys(serial, package, adb_path):
            calls.append((serial, package))
            if len(calls) == 3:
                done.set()
            return "meminfo"

        clock = iter(range(100, 200))
        with (
            patch("adb.dumpsys_meminfo", side_effect=fake_dumpsys),
            patch("adb.boottime_ns", side_effect=lambda serial, adb_path: next(clock)),
        ):
            self.service._sample_meminfo(self.session.id, "device", "com.example.target", done)

        samples = self.service.get_meminfo(self.session.id)
        self.assertEqual(calls, [("device", "com.example.target")] * 3)
        self.assertEqual(
            [(s["boottime_start_ns"], s["boottime_end_ns"]) for s in samples],
            [(100, 101), (102, 103), (104, 105)],
        )
        for sample in samples:
            self.assertEqual(sample["stdout"], "meminfo")
            self.assertIsNone(sample["error"])
            self.assertIn("captured_at", sample)

    def test_failed_sample_is_recorded_and_sampling_continues(self):
        done = threading.Event()
        results = [adb.AdbError("device offline"), "ok"]

        def fake_dumpsys(serial, package, adb_path):
            result = results.pop(0)
            if not results:
                done.set()
            if isinstance(result, Exception):
                raise result
            return result

        with (
            patch("adb.dumpsys_meminfo", side_effect=fake_dumpsys),
            patch("adb.boottime_ns", return_value=7),
        ):
            self.service._sample_meminfo(self.session.id, "device", "com.example.target", done)

        first, second = self.service.get_meminfo(self.session.id)
        self.assertEqual(first["error"], "AdbError: device offline")
        self.assertIsNone(first["stdout"])
        self.assertIsNone(first["boottime_end_ns"])
        self.assertEqual(second["stdout"], "ok")

    def test_returned_samples_are_copies(self):
        self.store.append_meminfo(self.session.id, {"stdout": "a"})
        self.service.get_meminfo(self.session.id)[0]["stdout"] = "changed"
        self.assertEqual(self.service.get_meminfo(self.session.id)[0]["stdout"], "a")

import unittest
from unittest.mock import patch

import adb


class PackagePssTests(unittest.TestCase):
    def test_extracts_modern_legacy_and_missing_values_per_process(self):
        sample = """Applications Memory Usage (in Kilobytes)
** MEMINFO in pid 12 [com.example.app] **
    TOTAL PSS: 1234 TOTAL RSS: 4000 TOTAL SWAP PSS: 50
** MEMINFO in pid 34 [com.example.app:worker] **
 App Summary
          TOTAL:      900       TOTAL SWAP PSS: 0
** MEMINFO in pid 56 [com.example.app:other] **
"""
        with patch.object(adb, "run_adb", return_value=sample) as run:
            self.assertEqual(adb.read_package_pss("device", "com.example.app"), [
                (12, "com.example.app", 1234),
                (34, "com.example.app:worker", 900),
                (56, "com.example.app:other", None),
            ])
        self.assertEqual(run.call_args.args[0], [
            "-s", "device", "shell", "dumpsys", "meminfo", "-s", "--package", "com.example.app",
        ])

    def test_no_process_and_invalid_output_are_distinct(self):
        with patch.object(adb, "run_adb", return_value="No process found for: com.example.app"):
            self.assertEqual(adb.read_package_pss("device", "com.example.app"), [])
        with patch.object(adb, "run_adb", return_value="Permission denied"):
            with self.assertRaises(adb.AdbError):
                adb.read_package_pss("device", "com.example.app")

import threading
import time
import unittest
from unittest.mock import patch

import adb
from session_service import ResultsNotReadyError, ServiceSettings, SessionService, StartRequest
from sessions import SessionState, SessionStore


class PhoneResultTests(unittest.TestCase):
    path = "/data/misc/perfetto-traces/test.pftrace"
    summary = '"cpu_ns","process_count","slice_count","trace_span_ns"\n42,1,2,900\n'
    details = '"kind","upid","pid","process_name","utid","tid","thread_name","cpu_ns","slice_count","thread_count"\n'
    bins = '"start_ns","end_ns","cpu_ns"\n0,900,42\n'
    rss = '"upid","pid","process_name","offset_ns","rss_bytes"\n'
    bounds = '"start_ts","end_ts"\n1000,1900\n'

    def setUp(self):
        self.store = SessionStore()
        self.service = SessionService(self.store, "adb", ServiceSettings(15, 30))
        self.request = StartRequest("device", 15, "com.example.target", None)
        self.session = self.store.create("device", 15, self.request.package)
        self.store.transition(self.session.id, SessionState.CAPTURING)

    def run_capture(self, output, details=None):
        outputs = [output, self.details if details is None else details, self.bins, self.rss, self.bounds]
        with (
            patch.object(self.service, "_capture", return_value=self.path),
            patch("adb.query_trace", side_effect=outputs) as query,
        ):
            self.service._run(self.session.id, self.request, None)
        return query.call_args_list[0].args

    def test_results_are_relayed_without_host_conversion(self):
        serial, path, sql, _ = self.run_capture(self.summary)
        self.assertEqual((serial, path), ("device", self.path))
        self.assertIn("'com.example.target'", sql)
        self.assertEqual(self.service.get(self.session.id).state, SessionState.DONE)
        self.assertEqual(self.service.get_results(self.session.id), {
            "cpu": self.summary, "cpu_details": self.details, "cpu_bins": self.bins, "rss": self.rss,
            "trace_bounds": self.bounds,
        })

    def test_details_failure_keeps_cpu_summary(self):
        self.run_capture(self.summary, adb.AdbError("details unavailable"))
        self.assertEqual(self.service.get_results(self.session.id), {
            "cpu": self.summary, "cpu_details_error": "details unavailable", "cpu_bins": self.bins, "rss": self.rss,
            "trace_bounds": self.bounds,
        })

    def test_unparseable_phone_result_never_marks_session_done(self):
        self.run_capture("not a result")
        self.assertEqual(self.service.get(self.session.id).state, SessionState.FAILED)
        with self.assertRaises(ResultsNotReadyError):
            self.service.get_results(self.session.id)


class StopTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore()
        self.service = SessionService(self.store, "adb", ServiceSettings(15, 30))
        # No package: the meminfo sampler is not started.
        self.request = StartRequest("device", 30, None, None)
        self.session = self.store.create("device", 30)

    def test_stop_interrupts_perfetto_and_keeps_the_trace(self):
        stop_event = threading.Event()
        self.service._stop_events[self.session.id] = stop_event
        running = {"value": True}
        with (
            patch("adb.start_perfetto", return_value=77),
            patch("adb.is_perfetto_running", side_effect=lambda *a: running["value"]),
            patch("adb.interrupt_perfetto", side_effect=lambda *a: running.update(value=False)) as interrupt,
            patch("adb.trace_file_size", return_value=1024),
        ):
            threading.Timer(0.05, lambda: self.service.stop(self.session.id)).start()
            started = time.monotonic()
            path = self.service._capture(self.session.id, self.request, stop_event)

        self.assertLess(time.monotonic() - started, 5)  # well before the 30 s duration
        interrupt.assert_called_once_with("device", 77, "adb")
        self.assertEqual(self.service.get(self.session.id).state, SessionState.STOPPING)
        self.assertTrue(path.endswith(f"{self.session.id}.pftrace"))

    def test_stop_of_a_finished_session_changes_nothing(self):
        stop_event = threading.Event()
        self.service._stop_events[self.session.id] = stop_event
        self.store.transition(self.session.id, SessionState.FAILED, error="x")
        self.assertEqual(self.service.stop(self.session.id).state, SessionState.FAILED)
        self.assertFalse(stop_event.is_set())

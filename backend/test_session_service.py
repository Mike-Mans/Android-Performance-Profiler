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

    def setUp(self):
        self.store = SessionStore()
        self.service = SessionService(self.store, "adb", ServiceSettings(15, 30))
        self.request = StartRequest("device", 15, "com.example.target", None)
        self.session = self.store.create("device", 15, self.request.package)
        self.store.transition(self.session.id, SessionState.CAPTURING)

    def run_capture(self, output, details=None):
        outputs = [output, self.details if details is None else details, self.bins, self.rss]
        with patch.object(self.service, "_capture", return_value=self.path), patch(
            "adb.query_trace", side_effect=outputs
        ) as query:
            self.service._run(self.session.id, self.request, None)
        return query.call_args_list[0].args

    def test_results_are_relayed_without_host_conversion(self):
        serial, path, sql, _ = self.run_capture(self.summary)
        self.assertEqual((serial, path), ("device", self.path))
        self.assertIn("'com.example.target'", sql)
        self.assertEqual(self.service.get(self.session.id).state, SessionState.DONE)
        self.assertEqual(self.service.get_results(self.session.id), {
            "cpu": self.summary, "cpu_details": self.details, "cpu_bins": self.bins, "rss": self.rss,
        })

    def test_details_failure_keeps_cpu_summary(self):
        self.run_capture(self.summary, adb.AdbError("details unavailable"))
        self.assertEqual(self.service.get_results(self.session.id), {
            "cpu": self.summary, "cpu_details_error": "details unavailable", "cpu_bins": self.bins, "rss": self.rss,
        })

    def test_unparseable_phone_result_never_marks_session_done(self):
        self.run_capture("not a result")
        self.assertEqual(self.service.get(self.session.id).state, SessionState.FAILED)
        with self.assertRaises(ResultsNotReadyError):
            self.service.get_results(self.session.id)

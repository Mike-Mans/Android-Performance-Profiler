import unittest
from unittest.mock import patch

from session_service import ResultsNotReadyError, ServiceSettings, SessionService, StartRequest
from sessions import SessionState, SessionStore


class PhoneResultTests(unittest.TestCase):
    path = "/data/misc/perfetto-traces/test.pftrace"
    summary = '"cpu_ns","process_count","slice_count","trace_span_ns"\n42,1,2,900\n'

    def setUp(self):
        self.store = SessionStore()
        self.service = SessionService(self.store, "adb", ServiceSettings(15, 30))
        self.request = StartRequest("device", 15, "com.example.target", None)
        self.session = self.store.create("device", 15, self.request.package)
        self.store.transition(self.session.id, SessionState.CAPTURING)

    def run_capture(self, output):
        with patch.object(self.service, "_capture", return_value=self.path), patch(
            "adb.query_trace", return_value=output
        ) as query:
            self.service._run(self.session.id, self.request, None)
        return query.call_args.args

    def test_results_are_relayed_without_host_conversion(self):
        serial, path, sql, _ = self.run_capture(self.summary)
        self.assertEqual((serial, path), ("device", self.path))
        self.assertIn("'com.example.target'", sql)
        self.assertEqual(self.service.get(self.session.id).state, SessionState.DONE)
        self.assertEqual(self.service.get_results(self.session.id), {"cpu": self.summary})

    def test_unparseable_phone_result_never_marks_session_done(self):
        self.run_capture("not a result")
        self.assertEqual(self.service.get(self.session.id).state, SessionState.FAILED)
        with self.assertRaises(ResultsNotReadyError):
            self.service.get_results(self.session.id)

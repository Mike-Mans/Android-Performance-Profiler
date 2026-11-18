"""Orchestrates the lifecycle of a profiling session.

Layering:
    server.py           HTTP layer (routes, status codes)
    session_service.py  this module: what happens during a session
    sessions.py         session state storage
    adb.py              adb command execution

Each session runs in its own background thread, so start() returns immediately
and the caller polls get() for progress.
"""

import csv
import io
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import adb
from sessions import InvalidTransitionError, Session, SessionState, SessionStore

# Base Perfetto config; the service appends the capture duration to it.
TRACE_CONFIG_PATH = Path(__file__).resolve().parent.parent / "on_device" / "trace.pbtxt"
CPU_QUERY_PATH = TRACE_CONFIG_PATH.with_name("cpu.sql")
TRACE_DIR_ON_DEVICE = "/data/misc/perfetto-traces"

POLL_INTERVAL_S = 1.0  # how often a running capture checks that Perfetto is alive
EXIT_GRACE_S = 10.0    # how long Perfetto may take to finish after its duration is over


class InvalidDurationError(ValueError):
    """Raised when the requested duration is outside the allowed range."""


class PackageNotFoundError(LookupError):
    """Raised when the target package is not installed on the device."""


class ResultsNotReadyError(RuntimeError):
    """Raised when results are requested before the session is done."""


@dataclass(frozen=True)
class ServiceSettings:
    default_duration_s: int  # used when the caller gives no duration
    max_duration_s: int      # upper bound; also acts as the capture timeout
    meminfo_interval_s: float = 5.0  # gap between dumpsys meminfo samples while capturing

    def __post_init__(self) -> None:
        if not self.meminfo_interval_s > 0:
            raise ValueError("meminfo_interval_s must be greater than 0")


@dataclass(frozen=True)
class StartRequest:
    """A start request after validation, with defaults and the UID resolved."""

    serial: str
    duration_s: int
    package: str | None
    uid: int | None


class SessionService:
    def __init__(self, store: SessionStore, adb_path: str, settings: ServiceSettings) -> None:
        self._store = store
        self._adb_path = adb_path
        self._settings = settings
        # One stop signal per session; stop() will set it to end a capture early.
        self._stop_events: dict[str, threading.Event] = {}

    def start(
        self,
        serial: str,
        duration_s: int | None = None,
        package: str | None = None,
    ) -> Session:
        """Validate the request, create a session and start capturing in the background.

        Returns immediately with the new session (state pending); the capture is
        started by a background thread, which moves the session on from there.
        Validation happens before any session is created, so a failed request
        leaves no trace. Raises:
            DeviceNotFoundError   serial is not connected or not in the "device" state
            InvalidDurationError  duration is below 1 or above max_duration_s
            PackageNotFoundError  package is given but not installed on the device
            DeviceBusyError       the device already has an unfinished session
        duration_s defaults to settings.default_duration_s and acts as the timeout.
        If package is given, its UID is resolved now and stored on the session.
        """
        request = self._validate_start(serial, duration_s, package)
        session = self._store.create(
            serial=request.serial,
            duration_s=request.duration_s,
            package=request.package,
            uid=request.uid,
        )
        stop_event = threading.Event()
        self._stop_events[session.id] = stop_event
        threading.Thread(
            target=self._run,
            args=(session.id, request, stop_event),
            name=f"session-{session.id[:8]}",
            daemon=True,
        ).start()
        return session

    def _validate_start(
        self, serial: str, duration_s: int | None, package: str | None
    ) -> StartRequest:
        """Check a start request and resolve defaults and the target UID.

        Creates nothing. The one-unfinished-session-per-device rule is not checked
        here; it is enforced atomically by SessionStore.create().
        """
        # Duration: default when absent, then range check. bool is a subclass of
        # int in Python, so it is rejected explicitly.
        if duration_s is None:
            duration_s = self._settings.default_duration_s
        if (
            not isinstance(duration_s, int)
            or isinstance(duration_s, bool)
            or not 1 <= duration_s <= self._settings.max_duration_s
        ):
            raise InvalidDurationError(
                f"duration_s must be an integer between 1 and {self._settings.max_duration_s}"
            )

        # Device must be connected and ready to run shell commands.
        device = adb.get_device(serial, self._adb_path)
        if device is None:
            raise adb.DeviceNotFoundError(f"device {serial} is not connected")
        if device["state"] != "device":
            raise adb.DeviceNotFoundError(f"device {serial} is not ready (state: {device['state']})")

        # Target app: resolve its UID now so a wrong name fails before capturing.
        uid = None
        if package is not None:
            found = adb.find_package(serial, package, self._adb_path)
            if found is None:
                raise PackageNotFoundError(f"package {package} is not installed on {serial}")
            uid = found["uid"]

        return StartRequest(serial=serial, duration_s=duration_s, package=package, uid=uid)

    def stop(self, session_id: str) -> Session:
        """Request an early end of the capture.

        Idempotent: if the session is already stopping or finished, the current
        state is returned unchanged. Does not wait for the trace to be finalized.
        Raises SessionNotFoundError if the id is unknown.
        """
        raise NotImplementedError

    def get(self, session_id: str) -> Session:
        """Return the current state of one session."""
        return self._store.get(session_id)

    def list(self) -> list[Session]:
        """Return all sessions, newest first. Not paginated."""
        return self._store.list()

    @property
    def meminfo_interval_s(self) -> float:
        return self._settings.meminfo_interval_s

    # Quoted: the list() method above shadows the builtin inside this class.
    def get_meminfo(self, session_id: str) -> "list[dict]":
        """Return the meminfo samples of a session, oldest first.

        Available in any state: while capturing it holds the samples taken so
        far, and once the capture has ended it is complete.
        Raises SessionNotFoundError if the id is unknown.
        """
        return self._store.get(session_id).meminfo

    def get_results(self, session_id: str) -> dict[str, str]:
        """Return the result tables (name -> CSV text) of a finished session.

        Raises ResultsNotReadyError unless the session state is done.
        """
        session = self._store.get(session_id)
        if session.state != SessionState.DONE:
            raise ResultsNotReadyError(f"session {session_id} is {session.state.value}")
        return session.results

    def shutdown(self) -> None:
        """Stop all background threads. Called when the server exits."""
        raise NotImplementedError

    def _run(self, session_id: str, request: StartRequest, stop_event: threading.Event) -> None:
        """Body of the per-session thread. Any error ends the session as failed."""
        try:
            trace_path = self._capture(session_id, request, stop_event)
            self._store.transition(session_id, SessionState.ANALYZING)
            # Package is validated against PACKAGE_NAME_RE by find_package().
            package_literal = f"'{request.package}'" if request.package else "NULL"
            sql = CPU_QUERY_PATH.read_text(encoding="utf-8").replace("__PACKAGE__", package_literal)
            output = adb.query_trace(request.serial, trace_path, sql, self._adb_path)
            rows = list(csv.DictReader(io.StringIO(output)))
            columns = {"cpu_ns", "process_count", "slice_count", "trace_span_ns"}
            if len(rows) != 1 or set(rows[0]) != columns or not all(
                value is not None and value.isdigit() for value in rows[0].values()
            ) or int(rows[0]["trace_span_ns"]) <= 0:
                raise RuntimeError("invalid CPU summary returned by phone")
            self._store.update(session_id, results={"cpu": output})
            self._store.transition(session_id, SessionState.DONE)
        except Exception as e:  # noqa: BLE001 - the thread must never die silently
            self._fail(session_id, e)

        finally:
            self._stop_events.pop(session_id, None)

    def _capture(self, session_id: str, request: StartRequest, stop_event: threading.Event) -> str:
        """Record a trace on the device until the duration is over or stop is requested."""
        serial = request.serial
        trace_path = f"{TRACE_DIR_ON_DEVICE}/{session_id}.pftrace"
        config = TRACE_CONFIG_PATH.read_text() + f"\nduration_ms: {request.duration_s * 1000}\n"

        pid = adb.start_perfetto(serial, config, trace_path, self._adb_path)
        self._store.update(session_id, trace_path=trace_path, perfetto_pid=pid)
        self._store.transition(session_id, SessionState.CAPTURING)

        # Memory is sampled for exactly as long as the trace records.
        sampler_done = threading.Event()
        sampler = threading.Thread(
            target=self._sample_meminfo,
            args=(session_id, serial, request.package, sampler_done),
            name=f"meminfo-{session_id[:8]}",
            daemon=True,
        )
        sampler.start()
        try:
            # Perfetto ends on its own when its duration is over. Poll meanwhile so a
            # stop request, or Perfetto dying early, is noticed within a second.
            deadline = time.monotonic() + request.duration_s
            while (remaining := deadline - time.monotonic()) > 0:
                if stop_event.wait(min(POLL_INTERVAL_S, remaining)):
                    break
                if not adb.is_perfetto_running(serial, pid, self._adb_path):
                    break
        finally:
            # Let a sample that is in progress land before the session moves on.
            sampler_done.set()
            sampler.join(timeout=adb.MEMINFO_TIMEOUT_S)

        self._wait_for_exit(serial, pid)
        size = adb.trace_file_size(serial, trace_path, self._adb_path)
        if not size:
            raise RuntimeError("perfetto finished without producing a trace file")
        return trace_path

    def _sample_meminfo(
        self, session_id: str, serial: str, package: str | None, done: threading.Event
    ) -> None:
        """Take a meminfo sample every meminfo_interval_s until `done` is set.

        The first sample is taken at once. Samples follow a fixed schedule from
        the start, so a slow dumpsys does not make later samples drift; a slot
        missed entirely is skipped rather than made up with a burst.
        """
        interval = self._settings.meminfo_interval_s
        start = time.monotonic()
        slot = 0
        while not done.is_set():
            self._take_meminfo_sample(session_id, serial, package)
            slot = max(slot + 1, math.ceil((time.monotonic() - start) / interval))
            if done.wait(start + slot * interval - time.monotonic()):
                break

    def _take_meminfo_sample(self, session_id: str, serial: str, package: str | None) -> None:
        """Store one meminfo sample. A failed sample is stored with its error and
        does not end the session: memory data is secondary to the trace."""
        captured_at = datetime.now(timezone.utc).isoformat()
        # The device clock is read just before and after dumpsys, so the two
        # readings bound the moment memory was measured, on the trace's clock.
        sample = {
            "captured_at": captured_at,
            "boottime_start_ns": None,
            "boottime_end_ns": None,
            "stdout": None,
            "error": None,
        }
        try:
            sample["boottime_start_ns"] = adb.boottime_ns(serial, self._adb_path)
            sample["stdout"] = adb.dumpsys_meminfo(serial, package, self._adb_path)
            sample["boottime_end_ns"] = adb.boottime_ns(serial, self._adb_path)
        except Exception as e:  # noqa: BLE001 - the sampler thread must keep going
            sample["error"] = f"{type(e).__name__}: {e}"
        self._store.append_meminfo(session_id, sample)

    def _wait_for_exit(self, serial: str, pid: int) -> None:
        """Wait for Perfetto to exit; interrupt it once if it overruns its grace period."""
        if self._wait_until_gone(serial, pid):
            return
        adb.interrupt_perfetto(serial, pid, self._adb_path)
        if not self._wait_until_gone(serial, pid):
            raise RuntimeError(f"perfetto (pid {pid}) did not exit")

    def _wait_until_gone(self, serial: str, pid: int) -> bool:
        deadline = time.monotonic() + EXIT_GRACE_S
        while adb.is_perfetto_running(serial, pid, self._adb_path):
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_INTERVAL_S / 2)
        return True

    def _fail(self, session_id: str, error: Exception) -> None:
        """Mark a session failed. A session that already ended is left as it is."""
        try:
            self._store.transition(
                session_id, SessionState.FAILED, error=f"{type(error).__name__}: {error}"
            )
        except InvalidTransitionError:
            pass

    # Not implemented yet:
    #   stop()    -> set the session's stop event; the capture loop then moves the
    #                session to STOPPING and interrupts Perfetto immediately
    #                instead of waiting for its duration to elapse.

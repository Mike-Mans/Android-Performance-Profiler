"""In-memory store for profiling sessions.

All reads and writes go through SessionStore so the storage can later be
swapped for a database without touching the rest of the code. Callers always
receive copies, never the stored objects, which keeps them safe to use while
background threads keep updating the store.
"""

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum


class SessionState(str, Enum):
    PENDING = "pending"      # created, capture not started yet
    CAPTURING = "capturing"  # Perfetto is recording
    STOPPING = "stopping"    # stop requested, trace is being finalized
    ANALYZING = "analyzing"  # trace processor is running on the phone
    DONE = "done"
    FAILED = "failed"


# Allowed state changes. DONE and FAILED are terminal.
TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.PENDING: {SessionState.CAPTURING, SessionState.FAILED},
    SessionState.CAPTURING: {SessionState.STOPPING, SessionState.ANALYZING, SessionState.FAILED},
    SessionState.STOPPING: {SessionState.ANALYZING, SessionState.FAILED},
    SessionState.ANALYZING: {SessionState.DONE, SessionState.FAILED},
    SessionState.DONE: set(),
    SessionState.FAILED: set(),
}

TERMINAL_STATES = {SessionState.DONE, SessionState.FAILED}

# Fields that may be changed through SessionStore.update().
UPDATABLE_FIELDS = {
    "package", "uid", "trace_path", "perfetto_pid", "results", "battery", "trace_config_sha256",
}


class SessionNotFoundError(KeyError):
    """Raised when a session id is unknown."""


class DeviceBusyError(RuntimeError):
    """Raised when a device already has an unfinished session."""


class InvalidTransitionError(RuntimeError):
    """Raised when a state change is not allowed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Session:
    id: str
    serial: str
    duration_s: int
    package: str | None = None   # optional target app; None for idle baselines
    uid: int | None = None       # UID of the target app, used to filter the trace
    state: SessionState = SessionState.PENDING
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    trace_path: str | None = None                       # trace file path on the phone
    perfetto_pid: int | None = None                     # PID of the Perfetto process on the phone
    results: dict[str, str] = field(default_factory=dict)  # result name -> CSV text
    # dumpsys meminfo samples taken while capturing, oldest first. Each is a dict
    # with boottime_start_ns/boottime_end_ns (device CLOCK_BOOTTIME, the clock of
    # the trace), captured_at (computer UTC, ISO 8601), stdout (raw dumpsys output) and error.
    meminfo: list[dict] = field(default_factory=list)
    # Raw battery snapshots by name (battery_before, batterystats_reset, battery_after,
    # batterystats), each shaped like a meminfo sample. Unparsed: the APK parses them.
    battery: dict[str, dict] = field(default_factory=dict)
    # SHA-256 of on_device/cpu_mem.pbtxt as sent (before duration_ms is appended), so
    # runs recorded with different trace configs are never compared as equals.
    trace_config_sha256: str | None = None

    def to_dict(self, include_results: bool = False) -> dict:
        """JSON-friendly view. Results are omitted unless asked for."""
        data = {
            "id": self.id,
            "serial": self.serial,
            "package": self.package,
            "uid": self.uid,
            "duration_s": self.duration_s,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "trace_path": self.trace_path,
            "perfetto_pid": self.perfetto_pid,
            "trace_config_sha256": self.trace_config_sha256,
        }
        if include_results:
            data["results"] = dict(self.results)
        return data


def _copy(session: Session) -> Session:
    return replace(
        session,
        results=dict(session.results),
        meminfo=[dict(sample) for sample in session.meminfo],
        battery={name: dict(sample) for name, sample in session.battery.items()},
    )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(
        self,
        serial: str,
        duration_s: int,
        package: str | None = None,
        uid: int | None = None,
    ) -> Session:
        """Create a session. Only one unfinished session is allowed per device."""
        with self._lock:
            for s in self._sessions.values():
                if s.serial == serial and s.state not in TERMINAL_STATES:
                    raise DeviceBusyError(f"device {serial} is busy with session {s.id}")
            session = Session(
                id=uuid.uuid4().hex, serial=serial, duration_s=duration_s, package=package, uid=uid
            )
            self._sessions[session.id] = session
            return _copy(session)

    def get(self, session_id: str) -> Session:
        with self._lock:
            return _copy(self._get(session_id))

    def list(self) -> list[Session]:
        """All sessions, newest first."""
        with self._lock:
            ordered = sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)
            return [_copy(s) for s in ordered]

    def transition(
        self, session_id: str, new_state: SessionState, error: str | None = None
    ) -> Session:
        """Move a session to a new state, enforcing the allowed transitions."""
        with self._lock:
            session = self._get(session_id)
            if new_state not in TRANSITIONS[session.state]:
                raise InvalidTransitionError(
                    f"session {session_id}: {session.state.value} -> {new_state.value} is not allowed"
                )
            session.state = new_state
            if new_state == SessionState.CAPTURING:
                session.started_at = _now()
            if new_state in TERMINAL_STATES:
                session.finished_at = _now()
            if error is not None:
                session.error = error
            return _copy(session)

    def update(self, session_id: str, **fields) -> Session:
        """Set data fields (package, uid, trace_path, perfetto_pid, results) on a session."""
        unknown = set(fields) - UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"fields cannot be updated: {sorted(unknown)}")
        with self._lock:
            session = self._get(session_id)
            for key, value in fields.items():
                setattr(session, key, value)
            return _copy(session)

    def append_meminfo(self, session_id: str, sample: dict) -> None:
        """Add one meminfo sample to a session, in any state."""
        with self._lock:
            self._get(session_id).meminfo.append(dict(sample))

    def _get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(session_id) from None

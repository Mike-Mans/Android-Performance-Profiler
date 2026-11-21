"""Entry point of the profiler backend service."""

import tomllib
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

import adb
import sessions
from session_service import (
    InvalidDurationError,
    PackageNotFoundError,
    ResultsNotReadyError,
    ServiceSettings,
    SessionService,
)

CONFIG_PATH = Path(__file__).parent / "config.toml"


def load_config() -> dict:
    """Read the TOML configuration file next to this script."""
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


config = load_config()
ADB_PATH = os.environ.get("PROFILER_ADB") or config.get("adb", {}).get("path", "adb")

service = SessionService(
    store=sessions.SessionStore(),
    adb_path=ADB_PATH,
    settings=ServiceSettings(**config["sessions"]),
)

app = FastAPI(title="Android Performance Profiler Backend")

# Map service-level exceptions to HTTP status codes in one place. The handler
# for the most specific exception class wins, so DeviceNotFoundError (404) takes
# precedence over its parent AdbError (502).
ERROR_STATUS: dict[type[Exception], int] = {
    adb.DeviceNotFoundError: 404,
    sessions.SessionNotFoundError: 404,
    PackageNotFoundError: 404,
    sessions.DeviceBusyError: 409,
    InvalidDurationError: 422,
    ResultsNotReadyError: 409,
    adb.InvalidPackageNameError: 422,
    adb.InvalidAddressError: 422,
    adb.InvalidPairingCodeError: 422,
    adb.AdbError: 502,
}


def _error_handler(status_code: int):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        # KeyError subclasses quote their message with str(); use the raw argument.
        detail = exc.args[0] if exc.args else str(exc)
        return JSONResponse(status_code=status_code, content={"detail": str(detail)})

    return handler


for _exc_type, _status in ERROR_STATUS.items():
    app.add_exception_handler(_exc_type, _error_handler(_status))


class StartSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject unknown fields

    serial: str
    duration_s: int | None = None  # defaults to the configured value
    package: str | None = None     # optional target app; omit for an idle baseline


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject unknown fields

    host: str                          # phone IP shown on the Wireless debugging screen
    connect_port: int                  # port shown on the main Wireless debugging screen
    # Pairing is needed once per computer; give both fields, or neither.
    pairing_port: int | None = None    # port shown on the "pair with code" screen
    pairing_code: str | None = None    # six-digit code shown on the same screen

    @model_validator(mode="after")
    def pairing_fields_go_together(self):
        if (self.pairing_port is None) != (self.pairing_code is None):
            raise ValueError("pairing_port and pairing_code must be given together")
        return self


@app.get("/health")
def health() -> dict:
    """Liveness check used by the APK to verify the backend is reachable."""
    return {"status": "ok"}


@app.get("/devices")
def devices() -> dict:
    """List devices currently visible to adb, with basic device properties."""
    try:
        return {"devices": adb.list_devices(ADB_PATH)}
    except adb.AdbError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/devices/connect")
def connect_device(body: ConnectRequest):
    """Connect to a phone over wireless debugging, pairing first when asked to.

    Returns {"serial", "paired", "connected"}; `serial` is what the other
    endpoints take. `paired` is true only when this call performed the pairing.
    If pairing succeeds but connecting fails, the response is HTTP 502 with
    paired=true: the one-time pairing code is used up, so the client should retry
    with the host and connect_port only.
    """
    connect_address = f"{body.host}:{body.connect_port}"
    pairing_address = None if body.pairing_port is None else f"{body.host}:{body.pairing_port}"

    # Validate everything before acting, so a typo in the connect address cannot
    # waste a one-time pairing code.
    adb.validate_address(connect_address)
    if pairing_address is not None:
        adb.validate_address(pairing_address)
        adb.validate_pairing_code(body.pairing_code)

    paired = False
    try:
        if pairing_address is not None:
            adb.pair(pairing_address, body.pairing_code, ADB_PATH)
            paired = True
        serial = adb.connect(connect_address, ADB_PATH)
    except adb.AdbError as e:
        return JSONResponse(
            status_code=502, content={"detail": str(e), "paired": paired, "connected": False}
        )
    return {"serial": serial, "paired": paired, "connected": True}


@app.get("/packages")
def packages(serial: str) -> dict:
    """List installed packages (with UIDs) on the device given by `serial`.

    `serial` is a required query parameter; a missing value yields HTTP 422.
    """
    try:
        return {"serial": serial, "packages": adb.list_packages(serial, ADB_PATH)}
    except adb.DeviceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except adb.AdbError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/sessions", status_code=201)
def start_session(body: StartSessionRequest) -> dict:
    """Start a profiling session on a device and return it immediately.

    The capture runs in the background; poll GET /sessions/{id} for its state.
    """
    session = service.start(body.serial, body.duration_s, body.package)
    return session.to_dict()


@app.get("/sessions")
def list_sessions() -> dict:
    """List all sessions, newest first."""
    return {"sessions": [s.to_dict() for s in service.list()]}


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    """Return the current state of one session."""
    return service.get(session_id).to_dict()


@app.post("/sessions/{session_id}/stop")
def stop_session(session_id: str) -> dict:
    """End a capture early. The trace recorded so far is finalized and analyzed as
    usual. Idempotent; returns the session without waiting for it to finish."""
    return service.stop(session_id).to_dict()


@app.get("/sessions/{session_id}/results")
def session_results(session_id: str) -> dict:
    return {"analysis_location": "android_shell", "results": service.get_results(session_id)}


@app.get("/sessions/{session_id}/meminfo")
def session_meminfo(session_id: str) -> dict:
    """Return the dumpsys meminfo samples taken while the session was capturing.

    Can be polled during a capture. Each sample's boottime_start_ns and
    boottime_end_ns are device CLOCK_BOOTTIME readings taken just before and
    after dumpsys ran; they are on the same clock as the timestamps (`ts`) in
    the session's Perfetto trace. `stdout` is the raw dumpsys output, unparsed. A failed sample has `error` set.
    """
    session = service.get(session_id)
    return {
        "session_id": session.id,
        "package": session.package,  # null: no samples are taken
        "interval_s": service.meminfo_interval_s,
        "clock": "boottime",
        "samples": service.get_meminfo(session_id),
    }


if __name__ == "__main__":
    server_cfg = config["server"]
    uvicorn.run(app, host=server_cfg["host"], port=server_cfg["port"])

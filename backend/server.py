"""Entry point of the profiler backend service."""

import tomllib
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

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


@app.get("/sessions/{session_id}/results")
def session_results(session_id: str) -> dict:
    return {"analysis_location": "android_shell", "results": service.get_results(session_id)}


if __name__ == "__main__":
    server_cfg = config["server"]
    uvicorn.run(app, host=server_cfg["host"], port=server_cfg["port"])

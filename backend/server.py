"""Entry point of the profiler backend service."""

import tomllib
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

import adb

CONFIG_PATH = Path(__file__).parent / "config.toml"


def load_config() -> dict:
    """Read the TOML configuration file next to this script."""
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


config = load_config()
ADB_PATH = config.get("adb", {}).get("path", "adb")

app = FastAPI(title="Android Performance Profiler Backend")


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


if __name__ == "__main__":
    server_cfg = config["server"]
    uvicorn.run(app, host=server_cfg["host"], port=server_cfg["port"])

"""Thin wrapper around the adb command line.

This is the only module that spawns subprocesses. Arguments are always passed
as a list (never through a shell), so no input can be interpreted as shell code.
"""

import subprocess

# Properties read from each ready device. Maps output key -> Android property.
DEVICE_PROPS = {
    "manufacturer": "ro.product.manufacturer",
    "model": "ro.product.model",
    "android_version": "ro.build.version.release",
    "sdk": "ro.build.version.sdk",
    "abi": "ro.product.cpu.abi",
    "build_type": "ro.build.type",
}

DEFAULT_TIMEOUT_S = 10


class AdbError(RuntimeError):
    """Raised when adb is missing, times out, or exits with an error."""


class DeviceNotFoundError(AdbError):
    """Raised when the requested device serial is not connected."""


def run_adb(args: list[str], adb_path: str = "adb", timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """Run one adb command and return its stdout as text."""
    try:
        result = subprocess.run(
            [adb_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise AdbError(f"adb executable not found: {adb_path}") from e
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb timed out: {' '.join(args)}") from e
    if result.returncode != 0:
        message = result.stderr.strip() or f"adb failed: {' '.join(args)}"
        if "not found" in message:
            raise DeviceNotFoundError(message)
        raise AdbError(message)
    return result.stdout


def parse_devices(output: str) -> list[dict]:
    """Parse the output of `adb devices -l`.

    Each device line looks like:
        <serial>  <state>  key:value key:value ...
    Header lines and daemon start-up messages are skipped.
    """
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        info = {"serial": parts[0], "state": parts[1]}
        for token in parts[2:]:
            key, sep, value = token.partition(":")
            if sep:
                info[key] = value
        devices.append(info)
    return devices


def read_props(serial: str, adb_path: str = "adb") -> dict:
    """Read selected system properties from one device.

    A property that is missing or empty is returned as None, because the
    available properties differ between vendors and Android versions.
    """
    props = {}
    for key, prop in DEVICE_PROPS.items():
        try:
            value = run_adb(["-s", serial, "shell", "getprop", prop], adb_path).strip()
        except AdbError:
            value = ""
        props[key] = value or None
    return props


def list_devices(adb_path: str = "adb") -> list[dict]:
    """Return all devices known to adb.

    Properties are only read from devices in the "device" state; offline or
    unauthorized devices cannot answer shell commands.
    """
    devices = parse_devices(run_adb(["devices", "-l"], adb_path))
    for device in devices:
        if device["state"] == "device":
            device.update(read_props(device["serial"], adb_path))
    return devices


def parse_packages(output: str) -> list[dict]:
    """Parse the output of `adb shell pm list packages -U`.

    Each line looks like `package:<name> uid:<uid>`. The uid part may be
    missing on some Android versions, in which case uid is None.
    """
    packages = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        parts = line.split()
        name = parts[0][len("package:"):]
        uid = None
        for token in parts[1:]:
            if token.startswith("uid:"):
                # With multiple users the value may be a comma-separated list.
                first = token[len("uid:"):].split(",")[0]
                uid = int(first) if first.isdigit() else None
        packages.append({"package": name, "uid": uid})
    return packages


def list_packages(serial: str, adb_path: str = "adb") -> list[dict]:
    """Return all installed packages on one device.

    Each entry has `package`, `uid` and `third_party` (True for user-installed
    apps, False for system apps). Two adb calls are needed because `-3` only
    filters and does not mark the entries.
    """
    base = ["-s", serial, "shell", "pm", "list", "packages", "-U"]
    all_packages = parse_packages(run_adb(base, adb_path, timeout=30))
    third_party = {p["package"] for p in parse_packages(run_adb([*base, "-3"], adb_path, timeout=30))}
    for p in all_packages:
        p["third_party"] = p["package"] in third_party
    return all_packages

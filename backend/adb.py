"""Thin wrapper around the adb command line.

This is the only module that spawns subprocesses. Arguments are always passed
as a list (never through a shell), so no input can be interpreted as shell code.
"""

import re
import subprocess
from pathlib import Path

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

# Android package names: dot-separated segments, each starting with a letter.
# `adb shell` joins its arguments and runs them through the shell on the device,
# so a package name must be validated before it is passed to a shell command.
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")

# Trace files must live in the directory the shell user may write to.
TRACE_PATH_RE = re.compile(r"/data/misc/perfetto-traces/[A-Za-z0-9_.-]+\.pftrace")
PROCESSOR = "/data/local/tmp/pp_week1_trace_processor"


class AdbError(RuntimeError):
    """Raised when adb is missing, times out, or exits with an error."""


class DeviceNotFoundError(AdbError):
    """Raised when the requested device serial is not connected."""


class InvalidPackageNameError(ValueError):
    """Raised when a package name is not a well-formed Android package name."""


def run_adb(
    args: list[str],
    adb_path: str = "adb",
    timeout: float = DEFAULT_TIMEOUT_S,
    stdin: str | None = None,
) -> str:
    """Run one adb command and return its stdout as text.

    `stdin`, if given, is sent to the command's standard input.
    """
    try:
        result = subprocess.run(
            [adb_path, *args],
            input=stdin,
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


def get_device(serial: str, adb_path: str = "adb") -> dict | None:
    """Return one device as reported by `adb devices -l`, or None if absent.

    Only the adb-level fields are returned (serial, state, model, ...). System
    properties are not read; call read_props() when they are needed.
    """
    for device in parse_devices(run_adb(["devices", "-l"], adb_path)):
        if device["serial"] == serial:
            return device
    return None


def find_package(serial: str, package: str, adb_path: str = "adb") -> dict | None:
    """Look up one installed package on a device. Returns {"package", "uid"} or None.

    `pm list packages` filters by substring, so the result is matched exactly here.
    Raises InvalidPackageNameError if the name is malformed.
    """
    if not PACKAGE_NAME_RE.fullmatch(package):
        raise InvalidPackageNameError(f"invalid package name: {package!r}")
    output = run_adb(["-s", serial, "shell", "pm", "list", "packages", "-U", package], adb_path, timeout=30)
    for entry in parse_packages(output):
        if entry["package"] == package:
            return entry
    return None


def start_perfetto(serial: str, config: str, output_path: str, adb_path: str = "adb") -> int:
    """Start Perfetto in background mode on the device and return its PID.

    The config is passed through stdin because SELinux may block reading config
    files from shell-writable directories. In background mode Perfetto keeps
    recording independently of the adb connection. Raises AdbError if it does
    not report a PID.
    """
    if not TRACE_PATH_RE.fullmatch(output_path):
        raise AdbError(f"unsafe trace output path: {output_path!r}")
    output = run_adb(
        ["-s", serial, "shell", "perfetto", "--background", "--txt", "-c", "-", "-o", output_path],
        adb_path,
        stdin=config,
    )
    lines = output.split()
    if not lines or not lines[-1].isdigit():
        raise AdbError(f"perfetto did not report a PID: {output.strip()!r}")
    return int(lines[-1])


def is_perfetto_running(serial: str, pid: int, adb_path: str = "adb") -> bool:
    """Return True if a process named perfetto with this PID exists on the device."""
    output = run_adb(["-s", serial, "shell", "ps", "-A", "-o", "PID,NAME"], adb_path)
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(int(pid)) and parts[1] == "perfetto":
            return True
    return False


def interrupt_perfetto(serial: str, pid: int, adb_path: str = "adb") -> None:
    """Send SIGINT to Perfetto, which makes it finalize the trace file and exit."""
    run_adb(["-s", serial, "shell", "kill", "-INT", str(int(pid))], adb_path)


def trace_file_size(serial: str, path: str, adb_path: str = "adb") -> int | None:
    """Return the size in bytes of a trace file on the device, or None if it does not exist."""
    if not TRACE_PATH_RE.fullmatch(path):
        raise AdbError(f"unsafe trace path: {path!r}")
    output = run_adb(
        ["-s", serial, "shell", f"stat -c %s {path} 2>/dev/null || echo missing"], adb_path
    ).strip()
    return int(output) if output.isdigit() else None


def query_trace(serial: str, path: str, sql: str, adb_path: str = "adb") -> str:
    """Send SQL as stdin data and relay the CSV generated by Android."""
    if not TRACE_PATH_RE.fullmatch(path):
        raise AdbError("invalid trace path")
    # Unique file per run; no SQL text is interpolated into the device shell command.
    query_path = f"/data/local/tmp/pp_query_{Path(path).stem}.sql"
    prefix = ["-s", serial, "shell"]
    run_adb([*prefix, f"cat > {query_path}"], adb_path, stdin=sql)
    try:
        return run_adb([*prefix, PROCESSOR, "query", "-f", query_path, path],
                       adb_path, timeout=45)
    finally:
        try:
            run_adb([*prefix, "rm", "-f", query_path], adb_path)
        except AdbError:
            pass

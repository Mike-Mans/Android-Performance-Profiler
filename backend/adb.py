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

# Wireless debugging: an IPv4 address or "localhost", then a port.
ADDRESS_RE = re.compile(r"(?P<host>\d{1,3}(?:\.\d{1,3}){3}|localhost):(?P<port>\d{1,5})")
PAIRING_CODE_RE = re.compile(r"\d{6}")
# Pairing and connecting talk to the network, so they get a longer timeout.
WIRELESS_TIMEOUT_S = 20


class AdbError(RuntimeError):
    """Raised when adb is missing, times out, or exits with an error."""


class DeviceNotFoundError(AdbError):
    """Raised when the requested device serial is not connected."""


class InvalidPackageNameError(ValueError):
    """Raised when a package name is not a well-formed Android package name."""


class InvalidAddressError(ValueError):
    """Raised when a wireless debugging address is not a valid HOST:PORT."""


class InvalidPairingCodeError(ValueError):
    """Raised when a pairing code is not exactly six digits."""


class PairingError(AdbError):
    """Raised when `adb pair` does not report success."""


class ConnectionFailedError(AdbError):
    """Raised when `adb connect` does not report a connection."""


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


# A package-scoped summary is small; a slow answer would just delay the next sample.
MEMINFO_TIMEOUT_S = 10


def parse_uptime_ns(text: str) -> int:
    """Convert `/proc/uptime` output to nanoseconds of CLOCK_BOOTTIME.

    The file holds two numbers, e.g. "12345.67 54321.00"; the first is the time
    since boot including suspend, which is the clock Perfetto timestamps use.
    Raises AdbError if the text is not in that format.
    """
    first = text.split()[0] if text.split() else ""
    match = re.fullmatch(r"(\d+)\.(\d{1,9})", first)
    if match is None:
        raise AdbError(f"unexpected /proc/uptime output: {text.strip()!r}")
    whole, fraction = match.groups()
    return int(whole) * 1_000_000_000 + int(fraction.ljust(9, "0"))


def boottime_ns(serial: str, adb_path: str = "adb") -> int:
    """Read the device's CLOCK_BOOTTIME, the clock of Perfetto trace timestamps."""
    return parse_uptime_ns(run_adb(["-s", serial, "shell", "cat", "/proc/uptime"], adb_path))


def dumpsys_meminfo(serial: str, package: str, adb_path: str = "adb") -> str:
    """Run a package-scoped `dumpsys meminfo` summary and return its stdout unchanged.

    `--package` covers every process of the app (including `pkg:service` ones).
    The text is not parsed here: the APK parses it.
    Raises InvalidPackageNameError if the package name is malformed.
    """
    if not PACKAGE_NAME_RE.fullmatch(package):
        raise InvalidPackageNameError(f"invalid package name: {package!r}")
    return run_adb(
        ["-s", serial, "shell", "dumpsys", "meminfo", "-s", "--package", package],
        adb_path,
        timeout=MEMINFO_TIMEOUT_S,
    )


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


def validate_address(address: str) -> str:
    """Check that `address` is HOST:PORT with an IPv4 host or "localhost".

    Returns the address unchanged. Raises InvalidAddressError otherwise.
    """
    match = ADDRESS_RE.fullmatch(address)
    if match is None:
        raise InvalidAddressError(f"invalid address (expected HOST:PORT): {address!r}")
    host, port = match["host"], int(match["port"])
    if host != "localhost" and any(int(octet) > 255 for octet in host.split(".")):
        raise InvalidAddressError(f"invalid IPv4 address: {address!r}")
    if not 1 <= port <= 65535:
        raise InvalidAddressError(f"port out of range: {address!r}")
    return address


def validate_pairing_code(code: str) -> str:
    """Check that `code` is exactly six digits. Returns it unchanged."""
    if PAIRING_CODE_RE.fullmatch(code) is None:
        raise InvalidPairingCodeError("pairing code must be exactly six digits")
    return code


def parse_pair_output(output: str) -> str | None:
    """Return the guid reported by a successful `adb pair`, or None if it did not succeed.

    A successful run prints e.g.
        Successfully paired to 192.168.0.5:37123 [guid=adb-ABC123-xyz]
    The guid is returned as an empty string if the line has none.
    """
    match = re.search(r"Successfully paired to \S+(?: \[guid=([^\]]*)\])?", output)
    if match is None:
        return None
    return match[1] or ""


def parse_connect_output(output: str) -> str | None:
    """Return the serial reported by a successful `adb connect`, or None on failure.

    `adb connect` exits with status 0 even when it fails, so the text decides:
        connected to 192.168.0.5:38271           -> success
        already connected to 192.168.0.5:38271   -> success
        failed to connect to '...': ...          -> failure
        cannot connect to ...: ...               -> failure
    """
    match = re.search(r"^(?:already )?connected to (\S+)\s*$", output, re.MULTILINE)
    return match[1] if match else None


def pair(address: str, code: str, adb_path: str = "adb") -> str:
    """Pair this computer with a phone's wireless debugging service.

    `address` is the pairing HOST:PORT shown on the phone's "Pair device with
    pairing code" screen, and `code` the six-digit code shown with it. Pairing is
    needed once per computer; it does not create a connection. Returns the guid.
    Raises InvalidAddressError, InvalidPairingCodeError or PairingError.
    """
    validate_address(address)
    validate_pairing_code(code)
    try:
        output = run_adb(["pair", address, code], adb_path, timeout=WIRELESS_TIMEOUT_S)
    except AdbError as e:
        raise PairingError(str(e)) from e
    guid = parse_pair_output(output)
    if guid is None:
        raise PairingError(output.strip() or "adb pair reported no result")
    return guid


def connect(address: str, adb_path: str = "adb") -> str:
    """Connect to a paired phone's wireless debugging port and return its serial.

    `address` is the HOST:PORT shown on the phone's main "Wireless debugging"
    screen. The returned serial is what the other functions take as `serial`.
    Raises InvalidAddressError or ConnectionFailedError.
    """
    validate_address(address)
    try:
        output = run_adb(["connect", address], adb_path, timeout=WIRELESS_TIMEOUT_S)
    except AdbError as e:
        raise ConnectionFailedError(str(e)) from e
    serial = parse_connect_output(output)
    if serial is None:
        raise ConnectionFailedError(output.strip() or "adb connect reported no result")
    return serial


def disconnect(address: str, adb_path: str = "adb") -> bool:
    """Disconnect a wireless device. Returns False if it was not connected."""
    validate_address(address)
    try:
        output = run_adb(["disconnect", address], adb_path)
    except AdbError as e:
        if "no such device" in str(e):
            return False
        raise
    return "no such device" not in output

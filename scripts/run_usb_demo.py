"""Exercise the installed debug APK on one authorized, unlocked USB phone."""
import argparse
import csv
import io
import json
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVITY = "edu.cs211.performanceprofiler/com.example.profiler.MainActivity"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--processor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    def adb(*cmd, binary=False, timeout=45):
        data = subprocess.run([args.adb, "-s", args.serial, *cmd], check=True,
                              capture_output=True, timeout=timeout).stdout
        return data if binary else data.decode("utf-8").strip()

    def api(path):
        with urllib.request.urlopen("http://127.0.0.1:9095" + path, timeout=15) as reply:
            return json.load(reply)

    def screen():
        adb("shell", "uiautomator", "dump", "/data/local/tmp/pp_demo_view.xml")
        return ET.fromstring(adb("shell", "cat", "/data/local/tmp/pp_demo_view.xml"))

    def wait_text(value, seconds=30):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            tree = screen()
            for node in tree.iter("node"):
                if value in node.attrib.get("text", ""):
                    return node
            time.sleep(.25)
        raise RuntimeError(f"App did not show {value!r}")

    def tap(node):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
        adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))

    def save_json(name, data):
        (out/name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def screenshot(name):
        (out/name).write_bytes(adb("exec-out", "screencap", "-p", binary=True))

    prior_stay_on = adb("shell", "settings", "get", "global", "stay_on_while_plugged_in")
    prior_reverse = adb("reverse", "--list")
    added_reverse = not any(line.split()[-2:] == ["tcp:9095", "tcp:9095"]
                            for line in prior_reverse.splitlines())
    if added_reverse and any(line.split()[-2:-1] == ["tcp:9095"]
                             for line in prior_reverse.splitlines()):
        raise RuntimeError("The phone already forwards port 9095 to a different service")

    states = []
    reverse_created = False
    try:
        adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", "7")
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        if added_reverse:
            adb("reverse", "tcp:9095", "tcp:9095")
            reverse_created = True
        save_json("device.json", {
            "model": adb("shell", "getprop", "ro.product.model"),
            "android": adb("shell", "getprop", "ro.build.version.release"),
            "abi": adb("shell", "getprop", "ro.product.cpu.abi"),
            "identity": adb("shell", "id").split()[0],
        })
        adb("push", str(args.processor.resolve()), "/data/local/tmp/pp_week1_trace_processor")
        adb("shell", "chmod", "755", "/data/local/tmp/pp_week1_trace_processor")
        (out/"processor-version.txt").write_text(
            adb("shell", "/data/local/tmp/pp_week1_trace_processor", "--version"), encoding="utf-8")
        apk = ROOT/"app/build/outputs/apk/debug/app-debug.apk"
        adb("install", "-r", str(apk), timeout=120)
        previous = {s["id"] for s in api("/sessions")["sessions"]}
        adb("shell", "am", "start", "-W", "-n", ACTIVITY)
        wait_text("USB bridge")
        tap(wait_text("Start 15-second run"))

        def latest():
            return next((s for s in api("/sessions")["sessions"]
                         if s["id"] not in previous and s["serial"] == args.serial), None)

        session = None
        for _ in range(35):
            session = latest()
            if session and session["state"] not in {"pending"}:
                break
            time.sleep(.2)
        if not session or session["state"] != "capturing":
            raise RuntimeError(f"APK did not start recording: {session}")
        states.append(session["state"])
        screenshot("recording.png")
        launch = adb("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                     "-c", "android.intent.category.LAUNCHER", "-p", session["package"])
        if "Status: ok" not in launch:
            raise RuntimeError(f"Target app failed to launch: {launch}")
        (out/"calculator-launch.txt").write_text(launch, encoding="utf-8")
        screenshot("target-workload.png")
        for _ in range(120):
            session = api("/sessions/" + session["id"])
            if session["state"] != states[-1]:
                states.append(session["state"])
            if session["state"] in {"done", "failed"}:
                break
            time.sleep(.25)
        save_json("session.json", session)
        save_json("observed-states.json", states)
        if session["state"] != "done":
            raise RuntimeError(f"Capture was not completed: {session}")
        result = api("/sessions/" + session["id"] + "/results")
        save_json("result.json", result)
        csv_text = result["results"]["cpu"]
        row = next(csv.DictReader(io.StringIO(csv_text)))
        if result["analysis_location"] != "android_shell" or int(row["cpu_ns"]) <= 0:
            raise RuntimeError(f"Target did not produce a positive on-device CPU result: {result}")
        (out/"cpu.csv").write_text(csv_text, encoding="utf-8")
        adb("pull", session["trace_path"], str(out/"demo.pftrace"))
        adb("shell", "am", "start", "-W", "-n", ACTIVITY)
        wait_text(f'{int(row["cpu_ns"]) / 1e9:.9f} CPU seconds')
        screenshot("apk-result.png")
        print(json.dumps({"session": session["id"], "cpu": row, "states": states,
                          "trace_bytes": (out/"demo.pftrace").stat().st_size}))
    finally:
        try:
            if prior_stay_on == "null":
                adb("shell", "settings", "delete", "global", "stay_on_while_plugged_in")
            else:
                adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", prior_stay_on)
            save_json("power-setting.json", {
                "before": prior_stay_on,
                "after": adb("shell", "settings", "get", "global", "stay_on_while_plugged_in"),
            })
        finally:
            if reverse_created:
                adb("reverse", "--remove", "tcp:9095")


if __name__ == "__main__":
    main()

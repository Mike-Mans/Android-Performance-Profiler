# Android-Performance-Profiler
## Week 2 USB demonstration

The APK starts a fixed 15-second run. The laptop starts Perfetto and relays
the CSV returned by the Android Trace Processor. The APK displays the target
CPU total, number of matching processes and slices, and trace span.
The summary covers processes with the package name or a colon-suffixed
process name; it does not assign OS work or isolated processes to the app.

1. On Windows, use Python 3.11+ and install `backend/requirements.txt` in a virtual environment.
2. Connect and unlock one authorized USB Android phone. Build the official
   ARM64 release Trace Processor using `scripts/build_trace_processor.sh`, or
   reuse the pinned Week 1 binary (commit `936f1383538f2858707a1c35faff6c6809f68503`).
3. In PowerShell, set the ADB path and start the loopback-only service:

   ```powershell
   $env:PROFILER_ADB = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
   python backend\server.py
   ```

4. In a second terminal, set JDK 17 and Android SDK with API 34, then run
   `./gradlew.bat assembleDebug` from the `app` directory (the Gradle project
   lives in `app/`). To exercise the actual Start button,
   foreground Calculator, and verify the APK result automatically, run:

   ```powershell
   python scripts\run_usb_demo.py --adb "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" --serial DEVICE_SERIAL --processor PATH_TO_ANDROID_ARM64_PROCESSOR --output PATH_TO_EVIDENCE
   ```

The script temporarily enables stay-awake while plugged in and creates an
ADB reverse tunnel, then restores the original power setting and removes
the tunnel it added. For a manual replay, deploy the binary to
`/data/local/tmp/pp_week1_trace_processor` (mode 755), run
`adb reverse tcp:9095 tcp:9095`, install the APK, and tap Start. Return to the
profiler after using the target app. Wireless transport, persistent history,
early Stop, and behavior inference are outside this fixed-duration demo.
Do not expose this unauthenticated demo service on the LAN.

## Using the backend API (`/docs`)

While the backend is running, open <http://127.0.0.1:9095/docs> in a browser.
FastAPI generates an interactive page from the code, so every endpoint can be
tried without writing any client:

1. Click an endpoint to expand it, then click **Try it out**.
2. Fill in the parameters. For `POST` endpoints, edit the JSON request body.
3. Click **Execute**. The page shows the response status and body, and the
   equivalent `curl` command.

Main endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Check that the backend is running |
| `GET /devices` | List devices visible to adb |
| `POST /devices/connect` | Connect to a phone over wireless debugging, pairing first if asked |
| `GET /packages?serial=...` | List installed packages and their UIDs on one device |
| `POST /sessions` | Start a profiling run; returns a session id immediately |
| `GET /sessions/{id}` | State of one session (pending, capturing, analyzing, done, failed) |
| `GET /sessions/{id}/results` | Result tables of a finished session |

A session run from `/docs`: call `GET /devices` and copy a `serial`, call
`POST /sessions` with `{"serial": "<serial>", "duration_s": 15}` (add
`"package"` to measure one app), copy the returned `id`, then call
`GET /sessions/{id}` repeatedly until `state` is `done`.

## Wireless ADB

`POST /devices/connect` pairs with a phone (once per computer) and connects to it.
On a phone, enable **Developer options > Wireless debugging**. Two screens give
the values to send:

- The main Wireless debugging screen shows the IP address and the **connect port**.
- **Pair device with pairing code** shows a **pairing port** and a six-digit code.
  Keep this dialog open until pairing is done; reopening it changes both values.

```json
{
  "host": "192.168.0.5",
  "connect_port": 38271,
  "pairing_port": 39709,
  "pairing_code": "924792"
}
```

Pairing is needed only once. After that, send just `host` and `connect_port`;
the connect port changes whenever Wireless debugging is switched off and on.
The pairing code works once: if the response has `"paired": true` but
`"connected": false`, retry without the two pairing fields.

Unplug the USB cable before measuring. A plugged-in phone is charging, which
invalidates battery measurements, and the same phone would also appear twice in
`adb devices`.

### Testing on the Android emulator

The emulator's Wi-Fi sits behind NAT (`10.0.2.x`), so the laptop cannot reach
its IP address directly. Forward the two ports through the emulator's existing
adb connection first, using the same port number on both sides.
Replace the ports below with the ones shown in the emulator.

1. In the emulator, enable Wireless debugging and open **Pair device with pairing code**.
2. Forward the pairing port and the connect port. `-s emulator-5554` is required
   once more than one device is listed:

   ```bash
   adb -s emulator-5554 forward tcp:39709 tcp:39709
   adb -s emulator-5554 forward tcp:38271 tcp:38271
   ```

3. Call `POST /devices/connect` (for example from `/docs`) with `localhost` as host:

   ```json
   {
     "host": "localhost",
     "connect_port": 38271,
     "pairing_port": 39709,
     "pairing_code": "924792"
   }
   ```

4. Check that `adb devices -l` lists `localhost:38271`, and use that as the `serial`.

Clean up after testing. The emulator otherwise stays listed twice
(`emulator-5554` and `localhost:38271`), and the USB demo app refuses to run
unless exactly one device is connected:

```bash
adb disconnect localhost:38271
adb forward --remove-all
adb devices
```

Optionally switch Wireless debugging off in the emulator, and remove this
computer from the paired devices list on that screen.

Known limits of the emulator test: it exercises pairing, connecting and the
backend logic, but not a real Wi-Fi link (latency, dropped connections, a
changing IP or port, mDNS discovery).

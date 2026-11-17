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

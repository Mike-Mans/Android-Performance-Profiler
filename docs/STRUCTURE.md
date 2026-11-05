# Repo structure

```
android-profiler/
├── README.md
├── docs/
├── schema/
│   └── run.schema.json
├── on_device/
│   ├── trace.pbtxt
│   └── cpu.sql
├── scripts/
│   └── build_trace_processor.sh
├── bridge/
│   ├── requirements.txt
│   ├── main.py
│   └── adb.py
└── app/
    └── .../profiler/
        ├── MainActivity.kt
        ├── Api.kt
        ├── Db.kt
        └── Analysis.kt
```

## Where each part runs

| Folder | Runs on | As |
|---|---|---|
| `app/` | Phone | Normal sandboxed app |
| `on_device/` | Phone | Shell user, pushed by bridge |
| `bridge/` | Laptop | ADB host |
| `scripts/` | Laptop | One-time build/setup |
| `schema/` | Both | Shared contract |
| `docs/` | Neither | Reports and notes |

## Files

### `README.md`
Setup and run steps. How to pair the phone, build and push the Trace Processor binary, start the bridge, install the APK, and run one experiment.

### `docs/`
Checkpoint reports (Week 1, 3, 5), the final report, and notes on which metrics work on which phone.

### `schema/run.schema.json`
JSON Schema for one run record: device, target package, labels, trace config version, measurements, missing metrics, inference. The bridge must send data that matches it. The APK must parse data that matches it. Exports from the APK follow it too. Change this file first whenever the data shape changes.

### `on_device/trace.pbtxt`
Perfetto trace config. Declares which data sources to record (starting with `linux.ftrace` `sched_switch`, later `linux.process_stats`, `android.power`) and buffer size. The bridge pipes it to `perfetto` on the phone over stdin, because SELinux may block reading configs from `/data/local/tmp`.

### `on_device/cpu.sql`
PerfettoSQL query. Joins `sched`, `thread` and `process` and sums `dur` per process to get CPU-seconds. The bridge pushes it to the phone, and `trace_processor_shell` runs it there against the trace. Output is a small CSV. Add one `.sql` file per new metric.

### `scripts/build_trace_processor.sh`
Clones Perfetto at a pinned commit and cross-compiles `trace_processor_shell` for ARM64 in release mode. Runs on the laptop. The output binary is gitignored. Pinning the commit keeps query results reproducible.

### `bridge/requirements.txt`
Python dependencies: FastAPI, Uvicorn, and a JSON Schema validator.

### `bridge/main.py`
FastAPI app. Defines the routes the APK calls: `/health`, `/devices`, `/packages`, `/sessions`, `/sessions/{id}/results`. Checks the API token. Tracks active sessions in memory. Validates outgoing results against `schema/run.schema.json`. Never computes metrics itself.

### `bridge/adb.py`
The only file that calls `subprocess`. Wraps a fixed set of ADB commands: list packages, push files, start `perfetto`, run `trace_processor_shell`, run `dumpsys`. Validates package names before use. Never passes raw input from the APK into a shell command.

### `app/.../MainActivity.kt`
Compose UI. Connect to bridge, pick target app, set labels, start/stop a run, add workload markers, view history and charts. Split into separate screen files once it gets too long.

### `app/.../Api.kt`
Retrofit/OkHttp client for the bridge routes. Holds the bridge address and token. Defines Kotlin types matching `schema/run.schema.json`.

### `app/.../Db.kt`
Room database. Tables for runs, metrics (with provenance and "unsupported" state) and workload markers, plus the DAO queries. The database file lives in the app's private storage on the phone, not in the repo.

### `app/.../Analysis.kt`
Parses the CSV results, computes features (CPU intensity, bursts, memory change), compares runs against baselines, and applies rule-based behavior inference. Keeps measured values separate from inferred labels.

## Added later
- `fixtures/`: small known-good trace and expected CSVs for offline tests (around Week 3).
- `experiments/`: workload protocols and exported runs (Week 4).
- More configs and `.sql` files, one per metric, as each is implemented.

-- Trace start and end on the trace clock (CLOCK_BOOTTIME), so the APK can place
-- the backend's meminfo samples, which are stamped with the same clock.
SELECT start_ts, end_ts FROM trace_bounds;

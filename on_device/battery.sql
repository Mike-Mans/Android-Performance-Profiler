-- First, last, delta and mean of each battery counter and power rail in the trace.
-- batt.* (android.power): charge_uah, capacity_pct, current_ua, voltage_uv; device-wide.
-- power.* : cumulative energy per power rail in uWs; device-wide, split by subsystem.
-- Delta is meaningful for cumulative counters (charge, rails), mean for current.
-- No rows means the phone recorded none of them.
SELECT t.name,
       COUNT(*) AS sample_count,
       MIN(c.ts) - b.start_ts AS first_offset_ns,
       MAX(c.ts) - b.start_ts AS last_offset_ns,
       (SELECT value FROM counter WHERE track_id = t.id ORDER BY ts LIMIT 1) AS first_value,
       (SELECT value FROM counter WHERE track_id = t.id ORDER BY ts DESC LIMIT 1) AS last_value,
       (SELECT value FROM counter WHERE track_id = t.id ORDER BY ts DESC LIMIT 1)
         - (SELECT value FROM counter WHERE track_id = t.id ORDER BY ts LIMIT 1) AS delta,
       AVG(c.value) AS mean_value
FROM counter_track t
JOIN counter c ON c.track_id = t.id
CROSS JOIN trace_bounds b
WHERE t.name GLOB 'batt.*' OR t.name GLOB 'power.*'
GROUP BY t.id
ORDER BY t.name;

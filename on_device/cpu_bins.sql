-- One-second bins. Split CPU slices at bin boundaries before summing.
WITH RECURSIVE
bounds AS (SELECT start_ts, end_ts FROM trace_bounds),
bins(start_ns) AS (
  SELECT 0 FROM bounds WHERE end_ts > start_ts
  UNION ALL
  SELECT start_ns + 1000000000 FROM bins, bounds
  WHERE start_ns + 1000000000 < end_ts - start_ts
),
slices AS (
  SELECT MAX(s.ts, b.start_ts) - b.start_ts AS start_ns,
         MIN(s.ts + s.dur, b.end_ts) - b.start_ts AS end_ns
  FROM sched s
  JOIN thread t USING (utid)
  JOIN process p USING (upid)
  CROSS JOIN bounds b
  WHERE s.dur > 0 AND t.is_idle = 0
    AND s.ts < b.end_ts AND s.ts + s.dur > b.start_ts
    AND (__PACKAGE__ IS NULL OR p.name = __PACKAGE__
         OR substr(p.name, 1, length(__PACKAGE__) + 1) = __PACKAGE__ || ':')
),
pieces(bin_start_ns, start_ns, end_ns) AS (
  SELECT (start_ns / 1000000000) * 1000000000, start_ns, end_ns FROM slices
  UNION ALL
  SELECT bin_start_ns + 1000000000, bin_start_ns + 1000000000, end_ns
  FROM pieces WHERE bin_start_ns + 1000000000 < end_ns
),
totals AS (
  SELECT bin_start_ns, SUM(MIN(end_ns, bin_start_ns + 1000000000) - start_ns) AS cpu_ns
  FROM pieces GROUP BY bin_start_ns
)
SELECT bins.start_ns, MIN(bins.start_ns + 1000000000, end_ts - start_ts) AS end_ns,
       COALESCE(totals.cpu_ns, 0) AS cpu_ns
FROM bins CROSS JOIN bounds
LEFT JOIN totals ON bins.start_ns = totals.bin_start_ns
ORDER BY bins.start_ns;

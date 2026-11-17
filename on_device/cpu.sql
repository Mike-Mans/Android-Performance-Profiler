-- The bridge substitutes a validated package string or NULL (whole-device run).
-- Include the exact package process name and its colon-suffixed subprocesses.
WITH target(package) AS (VALUES (__PACKAGE__))
SELECT COALESCE(SUM(s.dur), 0) AS cpu_ns,
       COUNT(DISTINCT p.upid) AS process_count,
       COUNT(*) AS slice_count,
       (SELECT end_ts - start_ts FROM trace_bounds) AS trace_span_ns
FROM sched s
JOIN thread t ON s.utid = t.utid
JOIN process p ON t.upid = p.upid
CROSS JOIN target
WHERE s.dur > 0 AND t.is_idle = 0
  AND (target.package IS NULL OR p.name = target.package
       OR substr(p.name, 1, length(target.package) + 1) = target.package || ':');

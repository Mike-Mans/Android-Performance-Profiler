-- Instances are grouped by Perfetto's trace-local IDs, not reusable Linux IDs.
WITH thread_cpu AS (
  SELECT p.upid, p.pid, p.name AS process_name, t.utid, t.tid,
         COALESCE(t.name, '') AS thread_name,
         SUM(s.dur) AS cpu_ns, COUNT(*) AS slice_count
  FROM sched s
  JOIN thread t USING (utid)
  JOIN process p USING (upid)
  WHERE s.dur > 0 AND t.is_idle = 0
    AND (__PACKAGE__ IS NULL OR p.name = __PACKAGE__
         OR substr(p.name, 1, length(__PACKAGE__) + 1) = __PACKAGE__ || ':')
  GROUP BY p.upid, t.utid
)
SELECT 'process' AS kind, upid, pid, process_name, -1 AS utid, -1 AS tid,
       '' AS thread_name, SUM(cpu_ns) AS cpu_ns, SUM(slice_count) AS slice_count,
       COUNT(*) AS thread_count
FROM thread_cpu
GROUP BY upid
UNION ALL
SELECT 'thread', upid, pid, process_name, utid, tid, thread_name, cpu_ns, slice_count, 0
FROM thread_cpu
ORDER BY upid, kind, cpu_ns DESC, utid;

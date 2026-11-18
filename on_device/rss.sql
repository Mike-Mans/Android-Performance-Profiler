-- Total RSS from linux.process_stats, in bytes. No samples means unavailable.
SELECT p.upid, p.pid, p.name AS process_name, c.ts - b.start_ts AS offset_ns,
       CAST(c.value AS INTEGER) AS rss_bytes
FROM process_counter_track t
JOIN process p USING (upid)
JOIN counter c ON c.track_id = t.id
CROSS JOIN trace_bounds b
WHERE t.name = 'mem.rss' AND c.value >= 0
  AND c.ts BETWEEN b.start_ts AND b.end_ts
  AND (p.name = __PACKAGE__ OR substr(p.name, 1, length(__PACKAGE__) + 1) = __PACKAGE__ || ':')
ORDER BY p.upid, c.ts;

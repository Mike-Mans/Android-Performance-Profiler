package com.example.profiler

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import org.json.JSONObject
import java.util.Locale

private const val MIB = 1048576.0

internal data class ProcessPss(val pid: String, val name: String, val pssKib: Long?)

/** One meminfo sample, placed on the trace timeline (ns since the trace start). */
internal data class PssSample(val offsetNs: Long, val processes: List<ProcessPss>)

internal data class PssSeries(val samples: List<PssSample>, val unreadable: Int)

private data class ProcessKey(val pid: String, val name: String)

private val MEMINFO_SECTION =
    Regex("""^\*\* MEMINFO in pid (\d+) \[([^\]\n]+)\] \*\*[ \t]*$""", RegexOption.MULTILINE)
private val TOTAL_PSS = Regex("""^\s*TOTAL PSS:\s*(\d+)\b""", RegexOption.MULTILINE)
private val SUMMARY_TOTAL = Regex("""^\s*TOTAL:\s*(\d+)\b""", RegexOption.MULTILINE)
private val NO_PROCESS = Regex("""^No process found for:""", RegexOption.MULTILINE)

/**
 * Per-process PSS (KiB) from the stdout of `dumpsys meminfo -s --package <pkg>`.
 * Returns an empty list when the app was not running, and null when the text is
 * not recognized. A process whose total cannot be found has pssKib = null.
 */
internal fun parseMeminfo(stdout: String): List<ProcessPss>? {
    if (NO_PROCESS.containsMatchIn(stdout)) return emptyList()
    val sections = MEMINFO_SECTION.findAll(stdout).toList()
    if (sections.isEmpty()) return null
    return sections.mapIndexed { index, header ->
        val end = sections.getOrNull(index + 1)?.range?.first ?: stdout.length
        val body = stdout.substring(header.range.last + 1, end)
        // Newer Android prints "TOTAL PSS:"; older versions only an App Summary "TOTAL:".
        val total = TOTAL_PSS.find(body)
            ?: body.substringAfter("App Summary", "").takeIf { it.isNotEmpty() }?.let { SUMMARY_TOTAL.find(it) }
        ProcessPss(header.groupValues[1], header.groupValues[2], total?.groupValues?.get(1)?.toLongOrNull())
    }
}

/**
 * Parse the payload of GET /sessions/{id}/meminfo. Each sample is placed at the
 * midpoint of its two device clock readings, minus the trace start; both are on
 * CLOCK_BOOTTIME. Returns null if the payload has no sample list.
 */
internal fun readPssSeries(payload: JSONObject, traceStartNs: Long): PssSeries? {
    val array = payload.optJSONArray("samples") ?: return null
    val samples = mutableListOf<PssSample>()
    var unreadable = 0
    for (index in 0 until array.length()) {
        val sample = array.optJSONObject(index)
        val processes = sample?.takeUnless { it.isNull("stdout") }?.let { parseMeminfo(it.getString("stdout")) }
        val start = sample?.optLong("boottime_start_ns", -1L) ?: -1L
        val end = sample?.optLong("boottime_end_ns", -1L) ?: -1L
        if (processes == null || start < 0 || end < start) {
            unreadable++
            continue
        }
        samples += PssSample(start + (end - start) / 2 - traceStartNs, processes)
    }
    return PssSeries(samples, unreadable)
}

/**
 * Memory of the target's processes over the trace window: RSS sampled by Perfetto
 * every second, and PSS parsed from the bridge's periodic dumpsys meminfo samples.
 */
@Composable
internal fun MemoryTimeline(
    rss: MetricTable,
    bounds: MetricTable,
    meminfo: JSONObject?,
    meminfoError: String?,
    spanNs: Long,
) {
    val traceStart = bounds.rows.singleOrNull()?.get("start_ts")?.toLongOrNull()
    val pss = remember(meminfo, traceStart) {
        if (meminfo != null && traceStart != null) readPssSeries(meminfo, traceStart) else null
    }
    val rssPoints = remember(rss, spanNs) {
        rss.rows.mapNotNull { row ->
            val time = row["offset_ns"]?.toLongOrNull() ?: return@mapNotNull null
            val bytes = row["rss_bytes"]?.toLongOrNull() ?: return@mapNotNull null
            if (time !in 0..spanNs || bytes < 0) return@mapNotNull null
            ProcessKey(row["pid"].orEmpty(), row["process_name"].orEmpty()) to ChartPoint(time, bytes / MIB)
        }.groupBy({ it.first }, { it.second })
    }
    val pssInWindow = pss?.samples.orEmpty().filter { it.offsetNs in 0..spanNs }
    val outsideWindow = pss?.samples.orEmpty().size - pssInWindow.size
    val pssPoints = remember(pss, spanNs) {
        pssInWindow.flatMap { sample ->
            sample.processes.mapNotNull { process ->
                process.pssKib?.let { ProcessKey(process.pid, process.name) to ChartPoint(sample.offsetNs, it * 1024 / MIB) }
            }
        }.groupBy({ it.first }, { it.second })
    }
    val rssColor = MaterialTheme.colorScheme.tertiary
    val pssColor = MaterialTheme.colorScheme.primary

    ChartCard("Memory over time") {
        if (rss.error != null) Text("RSS · ${rss.error}")
        else if (rssPoints.values.sumOf { it.size } != rss.rows.size) Text("RSS · some samples were invalid")
        when {
            meminfoError != null -> Text("PSS · $meminfoError")
            meminfo == null -> Text("PSS · unavailable")
            traceStart == null -> Text("PSS · trace start unavailable, samples cannot be placed")
            pss == null -> Text("PSS · invalid meminfo returned by the bridge")
            pss.samples.isEmpty() && pss.unreadable == 0 -> Text("PSS · no samples taken")
        }
        if (pss != null && pss.unreadable > 0) Text("PSS · ${pss.unreadable} samples could not be read")
        if (outsideWindow > 0) Text("PSS · $outsideWindow samples fell outside the trace window")

        val processes = (rssPoints.keys + pssPoints.keys).sortedWith(compareBy({ it.name }, { it.pid }))
        if (processes.isEmpty()) Text("Unavailable · no memory samples for the target")
        for (process in processes) {
            Text("${process.name} · PID ${process.pid}", style = MaterialTheme.typography.labelLarge)
            val series = listOfNotNull(
                rssPoints[process]?.let { ChartSeries("RSS · Perfetto", rssColor, it) },
                pssPoints[process]?.let { ChartSeries("PSS · dumpsys meminfo", pssColor, it, showMarkers = true) },
            )
            val description = series.joinToString(" · ") { line ->
                String.format(Locale.US, "%s peak %.1f MiB", line.label, line.points.maxOf { it.value })
            }
            Text(description)
            LineChart(series, spanNs, "MiB", "${process.name}: $description")
        }
    }
}

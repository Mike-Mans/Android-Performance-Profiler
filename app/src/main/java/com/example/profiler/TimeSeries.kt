package com.example.profiler

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import java.util.Locale

private data class CpuBin(val start: Long, val end: Long, val cpu: Long)
private data class RssPoint(val time: Long, val bytes: Long)

@Composable
internal fun CpuTimeline(table: MetricTable, spanNs: Long) {
    val bins = table.rows.mapNotNull { row ->
        val start = row["start_ns"]?.toLongOrNull() ?: return@mapNotNull null
        val end = row["end_ns"]?.toLongOrNull() ?: return@mapNotNull null
        val cpu = row["cpu_ns"]?.toLongOrNull() ?: return@mapNotNull null
        if (start < 0 || end <= start || end > spanNs || cpu < 0) return@mapNotNull null
        CpuBin(start, end, cpu)
    }
    ChartCard("CPU activity over time") {
        when {
            table.error != null -> Text(table.error)
            bins.size != table.rows.size -> Text("Invalid CPU activity data")
            bins.isEmpty() -> Text("No CPU time bins available")
            else -> {
                val peak = bins.maxOf { it.cpu }
                val description = "${bins.size} time bins · peak ${formatCpuTime(peak)} in one bin"
                Text(description)
                val color = MaterialTheme.colorScheme.primary
                Canvas(Modifier.fillMaxWidth().height(90.dp).semantics {
                    contentDescription = description
                }) {
                    for (bin in bins) {
                        val x = size.width * fraction(bin.start, spanNs)
                        val width = size.width * fraction(bin.end - bin.start, spanNs)
                        val height = size.height * fraction(bin.cpu, peak)
                        drawRect(color, Offset(x, size.height - height), Size(width * .88f, height))
                    }
                }
                TimeAxis(spanNs)
            }
        }
    }
}

@Composable
internal fun RssTimeline(table: MetricTable, spanNs: Long) {
    val processes = table.rows.groupBy { it["upid"] }
    ChartCard("RSS over time · Perfetto samples") {
        when {
            table.error != null -> Text(table.error)
            processes.isEmpty() -> Text("Unavailable · no target RSS samples in this trace")
            else -> for ((upid, rows) in processes) {
                val samples = rows.mapNotNull { row ->
                    val time = row["offset_ns"]?.toLongOrNull() ?: return@mapNotNull null
                    val bytes = row["rss_bytes"]?.toLongOrNull() ?: return@mapNotNull null
                    if (time !in 0..spanNs || bytes < 0) return@mapNotNull null
                    RssPoint(time, bytes)
                }
                val process = rows.first()
                Text("${process["process_name"]} · PID ${process["pid"]} · UPID $upid")
                if (samples.size != rows.size || upid == null) {
                    Text("Invalid RSS samples")
                    continue
                }
                val peak = samples.maxOf { it.bytes }
                val peakMiB = String.format(Locale.US, "%.1f MiB", peak / 1048576.0)
                val description = "${samples.size} samples · peak $peakMiB"
                Text(description)
                val color = MaterialTheme.colorScheme.tertiary
                Canvas(Modifier.fillMaxWidth().height(72.dp).semantics {
                    contentDescription = description
                }) {
                    for (sample in samples) {
                        val point = Offset(size.width * fraction(sample.time, spanNs),
                            size.height - 3.dp.toPx() -
                                (size.height - 6.dp.toPx()) * fraction(sample.bytes, peak))
                        drawCircle(color, radius = 3.dp.toPx(), center = point)
                    }
                }
                TimeAxis(spanNs)
            }
        }
    }
}

@Composable
internal fun ChartCard(title: String, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun TimeAxis(spanNs: Long) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text("0 s")
        Text(String.format(Locale.US, "%.2f s", spanNs / 1e9))
    }
}

private fun fraction(value: Long, maximum: Long): Float =
    (value.toDouble() / maximum.coerceAtLeast(1)).toFloat()

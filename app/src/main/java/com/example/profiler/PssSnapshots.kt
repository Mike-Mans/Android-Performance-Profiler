package com.example.profiler

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import java.util.Locale

@Composable
internal fun PssSnapshots(before: MetricTable, after: MetricTable) {
    ChartCard("PSS snapshots · dumpsys meminfo") {
        PssSnapshot("Before recording", before)
        PssSnapshot("After recording", after)
    }
}

@Composable
private fun PssSnapshot(label: String, snapshot: MetricTable) {
    Text(label, style = MaterialTheme.typography.labelLarge)
    val message = snapshot.error ?: "Unavailable · target not running".takeIf { snapshot.rows.isEmpty() }
    if (message != null) Text(message)
    for (row in snapshot.rows) {
        val size = row["pss_kib"]?.toLongOrNull()?.let { String.format(Locale.US, "%,d KiB", it) }
        Text("${row["process_name"]} · PID ${row["pid"]} · ${size ?: "PSS unavailable"}")
    }
}

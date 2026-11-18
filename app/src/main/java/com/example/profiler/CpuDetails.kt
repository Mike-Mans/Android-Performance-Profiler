package com.example.profiler

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
internal fun CpuDetails(table: MetricTable) {
    val processes = table.rows.filter { it["kind"] == "process" }
    val threads = table.rows.filter { it["kind"] == "thread" }.groupBy { it["upid"] }
    var expandedProcess by remember(table) { mutableStateOf<String?>(null) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("CPU by process and thread", style = MaterialTheme.typography.titleMedium)
            val message = table.error ?: "No target processes ran during this trace.".takeIf { processes.isEmpty() }
            if (message != null) Text(message)
            if (processes.isNotEmpty()) {
                Text("${processes.size} process instances · ${threads.values.sumOf { it.size }} scheduled threads")
            }
            for (process in processes) {
                val upid = process["upid"]
                Text("${process["process_name"] ?: "Unknown process"} · PID ${process["pid"]} · UPID $upid")
                Text("${process["cpu_ns"]?.toLongOrNull()?.let(::formatCpuTime) ?: "Unavailable"} · ${process["slice_count"]} slices")
                val expanded = expandedProcess == upid
                TextButton(onClick = { expandedProcess = if (expanded) null else upid }) {
                    Text(if (expanded) "Hide threads" else "Show ${process["thread_count"]} threads")
                }
                if (expanded) {
                    for (thread in threads[upid].orEmpty()) {
                        val name = thread["thread_name"].orEmpty().ifEmpty { "Unnamed thread" }
                        Text("$name · TID ${thread["tid"]} · UTID ${thread["utid"]}")
                        Text("${thread["cpu_ns"]?.toLongOrNull()?.let(::formatCpuTime) ?: "Unavailable"} · ${thread["slice_count"]} slices",
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}

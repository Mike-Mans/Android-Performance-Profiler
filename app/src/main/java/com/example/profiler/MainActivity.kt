package com.example.profiler

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.Locale

private const val DURATION = 15

private enum class Workload(val label: String, val instructions: String) {
    IDLE("Idle", "Open the target and leave it untouched until recording ends."),
    ACTIVE("Active", "Open the target and interact with it until recording ends."),
}

private fun runKey(serial: String, target: String, workload: Workload): String =
    "$serial/$target/${workload.name}"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { Profiler() } } }
    }

    @Composable
    private fun Profiler() {
        val scope = rememberCoroutineScope()
        var target by rememberSaveable { mutableStateOf("com.google.android.calculator") }
        var workload by rememberSaveable { mutableStateOf(Workload.IDLE) }
        var submittedWorkload by rememberSaveable { mutableStateOf(Workload.IDLE) }
        var runs by rememberSaveable { mutableStateOf(Bundle()) }
        var device by remember { mutableStateOf("Checking USB bridge…") }
        var serial by remember { mutableStateOf<String?>(null) }
        var sessionId by rememberSaveable { mutableStateOf<String?>(null) }
        var starting by remember { mutableStateOf(false) }
        var status by remember { mutableStateOf("Ready") }
        var errorMessage by remember { mutableStateOf<String?>(null) }
        var latestMetrics by remember { mutableStateOf<JSONObject?>(null) }
        var resultCsv by rememberSaveable { mutableStateOf<String?>(null) }
        val result = remember(resultCsv) { resultCsv?.let(::readCpuSummary) }
        var submittedTarget by rememberSaveable { mutableStateOf("") }
        var refresh by remember { mutableIntStateOf(0) }

        LaunchedEffect(refresh) {
            try {
                val devices = Api.request("/devices").getJSONArray("devices")
                val online = (0 until devices.length()).map { devices.getJSONObject(it) }
                    .filter { it.getString("state") == "device" }
                val connected = online.singleOrNull()
                serial = connected?.getString("serial")
                device = when {
                    connected != null -> connected.optString("model", "Android phone") + " • USB bridge"
                    online.isNotEmpty() -> "Connect only one authorized device for this demo"
                    else -> "No authorized device"
                }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { serial = null; device = "USB bridge unavailable" }
        }

        LaunchedEffect(sessionId) {
            val id = sessionId ?: return@LaunchedEffect
            try {
                repeat(50) {
                    val session = Api.request("/sessions/$id")
                    status = when (session.getString("state")) {
                        "pending" -> "Preparing"
                        "capturing" -> "Recording on phone"
                        "analyzing" -> "Analyzing on phone"
                        "done" -> {
                            val payload = Api.request("/sessions/$id/results")
                            require(payload.getString("analysis_location") == "android_shell")
                            val metrics = payload.getJSONObject("results")
                            val csv = metrics.getString("cpu")
                            readCpuSummary(csv)
                            val key = runKey(session.getString("serial"), submittedTarget, submittedWorkload)
                            runs = Bundle(runs).apply { putString(key, csv) }
                            latestMetrics = metrics
                            resultCsv = csv
                            "Complete"
                        }
                        "failed" -> error(session.optString("error", "Recording failed"))
                        else -> "Finalizing"
                    }
                    if (status == "Complete") { sessionId = null; return@LaunchedEffect }
                    delay(900)
                }
                error("Timed out waiting for the result")
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { status = "Failed"; errorMessage = e.message; sessionId = null }
        }

        val busy = starting || sessionId != null
        val cpuByWorkload = remember(runs, serial, target) {
            val connected = serial
            Workload.entries.associateWith { condition ->
                connected?.let { runs.getString(runKey(it, target.trim(), condition)) }
                    ?.let(::readCpuSummary)
            }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("Performance Profiler", style = MaterialTheme.typography.headlineMedium)
            Text(device)
            if (serial == null) TextButton(onClick = { refresh++ }) { Text("Reconnect") }
            OutlinedTextField(target, { target = it }, label = { Text("Target package") },
                enabled = !busy, singleLine = true, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Workload.entries.forEach { condition ->
                    FilterChip(
                        selected = workload == condition,
                        onClick = { workload = condition },
                        label = { Text(condition.label) },
                        enabled = !busy,
                    )
                }
            }
            Text(workload.instructions, style = MaterialTheme.typography.bodyMedium)
            Button(enabled = serial != null && !busy && target.isNotBlank(), onClick = {
                val selected = serial ?: return@Button
                val requestedTarget = target.trim()
                val requestedWorkload = workload
                starting = true; status = "Starting"; errorMessage = null; resultCsv = null
                latestMetrics = null
                scope.launch {
                    try {
                        val response = Api.request("/sessions", JSONObject()
                            .put("serial", selected).put("package", requestedTarget).put("duration_s", DURATION))
                        submittedTarget = requestedTarget
                        submittedWorkload = requestedWorkload
                        sessionId = response.getString("id")
                    } catch (e: CancellationException) { throw e }
                    catch (e: Exception) { status = "Failed"; errorMessage = e.message }
                    finally { starting = false }
                }
            }) { Text("Start 15-second run") }
            if (busy && status == "Recording on phone") {
                OutlinedButton(onClick = {
                    val intent = packageManager.getLaunchIntentForPackage(submittedTarget)
                    if (intent != null) startActivity(intent)
                    else errorMessage = "Open the target from your Android launcher."
                }) { Text("Open target app") }
            }
            Text(status, style = MaterialTheme.typography.titleLarge)
            errorMessage?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (cpuByWorkload.values.any { it != null }) {
                CpuComparison(cpuByWorkload)
            }
            result?.let { cpu ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("${submittedWorkload.label} run", style = MaterialTheme.typography.titleMedium)
                        Text(submittedTarget)
                        if (cpu.slices == 0L) Text("No target CPU activity observed")
                        else {
                            Text(String.format(Locale.US, "%.9f CPU seconds", cpu.ns / 1e9),
                                style = MaterialTheme.typography.titleLarge)
                            Text(String.format(Locale.US, "%,d ns • %,d slices • %d processes",
                                cpu.ns, cpu.slices, cpu.processes))
                        }
                        Text(String.format(Locale.US, "Trace span: %.3f seconds", cpu.spanNs / 1e9))
                        Text("Computed on Android")
                    }
                }
            }
            latestMetrics?.let { metrics ->
                val spanNs = result?.spanNs ?: return@let
                CpuTimeline(remember(metrics) { readMetricTable(metrics, "cpu_bins") }, spanNs)
                RssTimeline(remember(metrics) { readMetricTable(metrics, "rss") }, spanNs)
                CpuDetails(remember(metrics) { readMetricTable(metrics, "cpu_details") })
            }
        }
    }
}

@Composable
private fun CpuComparison(results: Map<Workload, CpuResult?>) {
    val idle = results[Workload.IDLE]
    val active = results[Workload.ACTIVE]
    val maxNs = maxOf(idle?.ns ?: 0L, active?.ns ?: 0L, 1L)
    val colors = MaterialTheme.colorScheme

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("CPU time by workload", style = MaterialTheme.typography.titleMedium)
            Workload.entries.forEach { workload ->
                val cpu = results[workload]
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(workload.label)
                    Text(cpu?.let { formatCpuTime(it.ns) } ?: "Awaiting run")
                }
                if (cpu != null) {
                    val shape = RoundedCornerShape(4.dp)
                    val barColor = if (workload == Workload.IDLE) colors.secondary else colors.primary
                    Box(Modifier.fillMaxWidth().height(12.dp).background(colors.surfaceVariant, shape)) {
                        Box(Modifier.fillMaxWidth((cpu.ns.toDouble() / maxNs).toFloat())
                            .fillMaxHeight().background(barColor, shape))
                    }
                }
            }
            if (idle != null && active != null) {
                val delta = String.format(Locale.US, "%+.6f CPU s", (active.ns - idle.ns) / 1e9)
                Text("Active minus idle: $delta", style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

internal fun formatCpuTime(ns: Long): String = String.format(Locale.US, "%.6f CPU s", ns / 1e9)

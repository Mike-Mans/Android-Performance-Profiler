package com.example.profiler

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.Locale

private const val DURATION = 15

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
        var device by remember { mutableStateOf("Checking USB bridge…") }
        var serial by remember { mutableStateOf<String?>(null) }
        var sessionId by rememberSaveable { mutableStateOf<String?>(null) }
        var starting by remember { mutableStateOf(false) }
        var status by remember { mutableStateOf("Ready") }
        var errorMessage by remember { mutableStateOf<String?>(null) }
        var result by remember { mutableStateOf<CpuResult?>(null) }
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
                            result = readCpuSummary(payload.getJSONObject("results").getString("cpu"))
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
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("Performance Profiler", style = MaterialTheme.typography.headlineMedium)
            Text(device)
            if (serial == null) TextButton(onClick = { refresh++ }) { Text("Reconnect") }
            OutlinedTextField(target, { target = it }, label = { Text("Target package") },
                enabled = !busy, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(enabled = serial != null && !busy && target.isNotBlank(), onClick = {
                val selected = serial ?: return@Button
                val requestedTarget = target.trim()
                starting = true; status = "Starting"; errorMessage = null; result = null
                scope.launch {
                    try {
                        val response = Api.request("/sessions", JSONObject()
                            .put("serial", selected).put("package", requestedTarget).put("duration_s", DURATION))
                        submittedTarget = requestedTarget
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
            result?.let { cpu ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(submittedTarget, style = MaterialTheme.typography.titleMedium)
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
        }
    }
}

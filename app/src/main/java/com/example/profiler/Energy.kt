package com.example.profiler

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import org.json.JSONObject
import java.util.Locale

/** A value, or the reason it cannot be reported. Never a stand-in zero (acceptance test T5). */
internal data class Reading(val value: Double? = null, val unsupported: String? = null)

/** Device-wide context from `dumpsys battery`. A field is null when its line is missing. */
internal data class BatteryStatus(val plugged: Boolean?, val levelPct: Int?, val temperatureC: Double?)

/** The target UID's batterystats estimate, with its per-component split (screen, cpu, ...) in mAh. */
internal data class AppEstimate(val reading: Reading, val components: List<Pair<String, Double>> = emptyList())

private fun intField(stdout: String, name: String): Int? =
    Regex("""^\s*$name:\s*(-?\d+)\s*$""", RegexOption.MULTILINE)
        .find(stdout)?.groupValues?.get(1)?.toIntOrNull()

/** Parse `dumpsys battery`. Android caches these values, so they give context, not deltas. */
internal fun parseDumpsysBattery(stdout: String): BatteryStatus {
    // "AC powered", "USB powered", "Wireless powered", "Dock powered": any true means plugged in.
    val powered = Regex("""^\s*\w+ powered:\s*(true|false)\s*$""", RegexOption.MULTILINE)
        .findAll(stdout).map { it.groupValues[1] == "true" }.toList()
    return BatteryStatus(
        plugged = if (powered.isEmpty()) null else powered.any { it },
        levelPct = intField(stdout, "level"),
        temperatureC = intField(stdout, "temperature")?.div(10.0),  // tenths of a degree
    )
}

/** How batterystats prints a UID (Android's UserHandle.formatUid): 10205 -> "u0a205", 1000 -> "1000". */
internal fun batteryStatsUid(uid: Int): String {
    val user = uid / 100_000
    val appId = uid % 100_000
    return when {
        appId >= 10_000 -> "u${user}a${appId - 10_000}"
        user == 0 -> "$appId"
        else -> "u${user}s$appId"
    }
}

// Component totals such as "screen=2.70 cpu=3.89"; skips the per-state splits like "cpu:fg=3.77".
private val COMPONENT = Regex("""(?<![:\w])(\w+)=([0-9.eE+-]+)""")

/**
 * The target UID's line in the "Estimated power use (mAh)" section of
 * `dumpsys batterystats --charged <pkg>`, e.g.
 *     UID u0a305: 6.80 fg: 3.97 (1m 58s 531ms) bg: 0.0685 ...
 *         screen=2.70 cpu=3.89 cpu:fg=3.77 ... GPU=0.184 ...
 * The section lists several UIDs even with a package filter, so the line is matched
 * by UID. The section ends at the first blank line.
 */
internal fun parseAppEstimate(stdout: String, uid: Int): AppEstimate {
    val start = stdout.indexOf("Estimated power use (mAh):")
    if (start < 0) return AppEstimate(Reading(unsupported = "batterystats printed no power estimates"))
    val end = stdout.indexOf("\n\n", start).let { if (it < 0) stdout.length else it }
    val section = stdout.substring(start, end)
    val label = batteryStatsUid(uid)
    val match = Regex("""^\s*UID ${Regex.escape(label)}:\s*([0-9.eE+-]+).*$""", RegexOption.MULTILINE).find(section)
    val mah = match?.groupValues?.get(1)?.toDoubleOrNull()
        ?: return AppEstimate(Reading(unsupported = "no estimate listed for UID $label"))
    val nextLine = section.substring(match.range.last + 1).trimStart('\n').substringBefore('\n')
    val components = COMPONENT.findAll(nextLine)
        .mapNotNull { m -> m.groupValues[2].toDoubleOrNull()?.let { m.groupValues[1] to it } }.toList()
    return AppEstimate(Reading(mah), components)
}

/**
 * Why a device-wide counter from on_device/battery.sql cannot be reported for this run,
 * or null if it can.
 *
 * row:        that counter's CSV row, with the columns name, sample_count, first_offset_ns,
 *             last_offset_ns, first_value, last_value, delta and mean_value (all strings),
 *             or null if the phone recorded no such track.
 * charging:   true if dumpsys battery showed the phone plugged in before or after the run;
 *             null if unknown.
 * cumulative: true for batt.charge_uah and the power rails (the delta is reported),
 *             false for batt.current_ua and batt.power_mw (the mean is reported).
 */
internal fun counterUnsupportedReason(row: Map<String, String>?, charging: Boolean?, cumulative: Boolean): String? {
    if (row == null) return "not recorded by this phone"
    if (charging == true) return "phone was charging"
    if (charging == null) return "charging state unknown"

    val samples = row["sample_count"]?.toLongOrNull()
        ?: return "invalid sample count returned by the phone"
    if (samples < 1) return "no samples recorded"

    val value = row[if (cumulative) "delta" else "mean_value"]?.toDoubleOrNull()
    if (value == null || !value.isFinite()) return "invalid data returned by the phone"

    if (cumulative) {
        if (samples < 2) return "not enough samples to measure a change"
        val first = row["first_offset_ns"]?.toLongOrNull()
        val last = row["last_offset_ns"]?.toLongOrNull()
        if (first == null || last == null || last <= first) return "no valid sampling interval"
        if (value == 0.0) return "counter did not change during the run"
    }
    return null
}

private fun counterReading(
    rows: Map<String, Map<String, String>>, name: String, charging: Boolean?, cumulative: Boolean, scale: Double,
): Reading {
    val row = rows[name]
    counterUnsupportedReason(row, charging, cumulative)?.let { return Reading(unsupported = it) }
    val raw = row?.get(if (cumulative) "delta" else "mean_value")?.toDoubleOrNull()
        ?: return Reading(unsupported = "invalid data returned by the phone")
    return Reading(raw * scale)
}

/**
 * Energy over the run, from the phone's battery counters and power rails (results "battery")
 * and the raw dumpsys snapshots (GET /sessions/{id}/battery). Every value is labelled with
 * where it came from.
 */
@Composable
internal fun EnergyCard(table: MetricTable, battery: JSONObject?, batteryError: String?, spanNs: Long) {
    val snapshots = battery?.optJSONObject("snapshots")
    fun field(name: String, key: String): String? =
        snapshots?.optJSONObject(name)?.takeUnless { it.isNull(key) }?.getString(key)
    val before = remember(battery) { field("battery_before", "stdout")?.let(::parseDumpsysBattery) }
    val after = remember(battery) { field("battery_after", "stdout")?.let(::parseDumpsysBattery) }
    val charging = listOfNotNull(before?.plugged, after?.plugged).takeIf { it.isNotEmpty() }?.any { it }
    val rows = remember(table) { table.rows.associateBy { it["name"].orEmpty() } }
    val seconds = (spanNs / 1e9).coerceAtLeast(1e-9)

    ChartCard("Energy") {
        Text("Start ${describe(before)} → end ${describe(after)}")

        Text("Device-wide · measured (Perfetto android.power: whole phone, incl. screen and radios)",
            style = MaterialTheme.typography.labelLarge)
        if (table.error != null) Text("Unsupported · ${table.error}")
        else {
            // charge_uah falls while discharging, so charge used = -delta.
            ReadingRow("Charge used", counterReading(rows, "batt.charge_uah", charging, true, -1e-3)) {
                String.format(Locale.US, "%.1f mAh", it)
            }
            ReadingRow("Mean current", counterReading(rows, "batt.current_ua", charging, false, 1e-3)) {
                String.format(Locale.US, "%.0f mA (sign as the phone reports it)", it)
            }
            ReadingRow("Mean power", counterReading(rows, "batt.power_mw", charging, false, 1.0)) {
                String.format(Locale.US, "%.0f mW", it)
            }
            val rails = rows.keys.filter { it.startsWith("power.") }
                .map { it to counterReading(rows, it, charging, true, 1e-6) }  // uWs -> J
            if (rails.isEmpty()) Text("Power rails · Unsupported · not recorded by this phone")
            else {
                val measured = rails.filter { it.second.value != null }.sortedByDescending { it.second.value }
                Text(String.format(Locale.US, "Power rails · %d of %d reported · %.1f J total",
                    measured.size, rails.size, measured.sumOf { it.second.value!! }))
                for ((name, reading) in measured.take(6)) {
                    val joules = reading.value!!
                    Text(String.format(Locale.US, "  %s · %.2f J (%.0f mW)",
                        name.removePrefix("power.rails."), joules, joules * 1000 / seconds),
                        style = MaterialTheme.typography.bodySmall)
                }
                rails.firstOrNull { it.second.unsupported != null }?.let { (name, reading) ->
                    Text("  e.g. $name · Unsupported · ${reading.unsupported}",
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        Text("Per-app · estimated (batterystats power model, since the reset at start)",
            style = MaterialTheme.typography.labelLarge)
        val estimate = remember(battery, charging) {
            val uid = battery?.takeUnless { it.isNull("uid") }?.optInt("uid")
            val stats = field("batterystats", "stdout")
            fun unsupported(reason: String) = AppEstimate(Reading(unsupported = reason))
            when {
                battery == null -> unsupported(batteryError ?: "battery data unavailable")
                charging == true -> unsupported("phone was charging; batterystats counts only time on battery")
                uid == null -> unsupported("target UID unknown")
                stats == null -> unsupported(field("batterystats", "error") ?: "batterystats not collected")
                else -> parseAppEstimate(stats, uid)
            }
        }
        ReadingRow(battery?.optString("package").orEmpty().ifEmpty { "Target app" }, estimate.reading) {
            String.format(Locale.US, "%.3f mAh", it)
        }
        if (estimate.components.isNotEmpty()) {
            Text(estimate.components.joinToString(" · ") { (name, mah) ->
                String.format(Locale.US, "%s %.3g", name, mah)  // significant digits: tiny values stay visible
            }, style = MaterialTheme.typography.bodySmall)
        }
    }
}

private fun describe(status: BatteryStatus?): String = if (status == null) "unknown" else listOfNotNull(
    status.levelPct?.let { "$it%" },
    status.temperatureC?.let { String.format(Locale.US, "%.1f °C", it) },
    when (status.plugged) { true -> "charging"; false -> "on battery"; null -> null },
).joinToString(" · ").ifEmpty { "unknown" }

@Composable
private fun ReadingRow(label: String, reading: Reading, format: (Double) -> String) {
    Text("$label · " + (reading.value?.let(format) ?: "Unsupported · ${reading.unsupported}"))
}

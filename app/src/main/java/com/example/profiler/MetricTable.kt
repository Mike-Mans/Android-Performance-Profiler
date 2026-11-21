package com.example.profiler

import org.json.JSONObject

internal class MetricTable(val rows: List<Map<String, String>>, val error: String? = null)

internal fun readMetricTable(results: JSONObject, name: String): MetricTable {
    val csv = results.optString(name)
    if (csv.isEmpty()) return MetricTable(emptyList(), results.optString("${name}_error")
        .ifEmpty { "Unavailable" })
    return try {
        val records = readCsv(csv)
        val header = records.first()
        require(header.isNotEmpty() && header.distinct().size == header.size)
        MetricTable(records.drop(1).map {
            require(it.size == header.size)
            header.zip(it).toMap()
        })
    } catch (_: IllegalArgumentException) {
        MetricTable(emptyList(), "Invalid data returned by the phone")
    }
}

private fun readCsv(csv: String): List<List<String>> {
    val records = mutableListOf<List<String>>()
    val row = mutableListOf<String>()
    val field = StringBuilder()
    var quoted = false
    var index = 0
    while (index < csv.length) {
        val char = csv[index++]
        when {
            char == '"' && quoted && csv.getOrNull(index) == '"' -> {
                field.append('"')
                index++
            }
            char == '"' -> quoted = !quoted
            quoted -> field.append(char)
            char == ',' || char == '\n' -> {
                row.add(field.toString())
                field.clear()
                if (char == '\n') {
                    if (row != listOf("")) records.add(row.toList())
                    row.clear()
                }
            }
            char != '\r' -> field.append(char)
        }
    }
    require(!quoted)
    if (field.isNotEmpty() || row.isNotEmpty()) records.add(row + field.toString())
    require(records.isNotEmpty())
    return records
}

package com.example.profiler

internal data class CpuResult(val ns: Long, val processes: Long, val slices: Long, val spanNs: Long)

internal fun readCpuSummary(csv: String): CpuResult {
    // This endpoint returns one row of nonnegative integers; no generic CSV parsing is needed.
    val lines = csv.trim().lines()
    require(lines.size == 2) { "The phone returned an invalid CPU summary" }
    val headers = lines[0].split(',').map { it.trim('"') }
    val numbers = lines[1].split(',')
    require(headers.size == numbers.size)
    val fields = headers.zip(numbers).toMap()
    fun value(key: String) = fields.getValue(key).toLong().also { require(it >= 0) }
    return CpuResult(value("cpu_ns"), value("process_count"), value("slice_count"), value("trace_span_ns"))
}

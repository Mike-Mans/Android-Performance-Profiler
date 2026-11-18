package com.example.profiler

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import java.util.Locale
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.pow

internal data class ChartPoint(val timeNs: Long, val value: Double)

internal data class ChartSeries(
    val label: String,
    val color: Color,
    val points: List<ChartPoint>,
    val showMarkers: Boolean = false,
)

/**
 * Line chart over the trace window (0..spanNs) with a labelled grid on both axes.
 * The y axis starts at zero and ends at a round value above the highest point.
 */
@Composable
internal fun LineChart(series: List<ChartSeries>, spanNs: Long, unit: String, description: String) {
    val measurer = rememberTextMeasurer()
    val labelStyle = MaterialTheme.typography.labelSmall.copy(
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    val gridColor = MaterialTheme.colorScheme.outlineVariant
    val axisColor = MaterialTheme.colorScheme.outline
    val span = spanNs.coerceAtLeast(1)
    val peak = series.flatMap { it.points }.maxOfOrNull { it.value } ?: 0.0
    val yStep = niceStep(peak / 4)
    val yTop = yStep * max(1.0, ceil(peak / yStep))
    val spanS = span / 1e9
    val xStep = niceStep(spanS / 5)

    Text(unit, style = labelStyle)
    Canvas(Modifier.fillMaxWidth().height(180.dp).semantics { contentDescription = description }) {
        val yTicks = ticks(yTop, yStep)
        val xTicks = ticks(spanS, xStep)
        val yLabels = yTicks.map { measurer.measure(AnnotatedString(formatTick(it, yStep)), style = labelStyle) }
        val xLabels = xTicks.map {
            measurer.measure(AnnotatedString(formatTick(it, xStep) + " s"), style = labelStyle)
        }
        val gap = 4.dp.toPx()
        val left = yLabels.maxOf { it.size.width } + gap * 2
        val right = xLabels.last().size.width / 2f
        val top = yLabels.last().size.height / 2f
        val bottom = xLabels.first().size.height + gap
        val plotWidth = size.width - left - right
        val plotHeight = size.height - top - bottom
        if (plotWidth <= 0f || plotHeight <= 0f) return@Canvas
        val plotBottom = top + plotHeight

        fun x(timeNs: Long) = left + plotWidth * (timeNs.toDouble() / span).toFloat()
        fun y(value: Double) = top + plotHeight * (1 - value / yTop).toFloat()

        // Horizontal grid lines with value labels on the left.
        yTicks.forEachIndexed { index, value ->
            val py = y(value)
            drawLine(gridColor, Offset(left, py), Offset(left + plotWidth, py), 1.dp.toPx())
            val label = yLabels[index]
            drawText(label, topLeft = Offset(left - gap - label.size.width, py - label.size.height / 2f))
        }
        // Vertical grid lines with time labels below.
        xTicks.forEachIndexed { index, seconds ->
            val px = x((seconds * 1e9).toLong())
            drawLine(gridColor, Offset(px, top), Offset(px, plotBottom), 1.dp.toPx())
            val label = xLabels[index]
            drawText(label, topLeft = Offset(px - label.size.width / 2f, plotBottom + gap))
        }
        drawLine(axisColor, Offset(left, top), Offset(left, plotBottom), 1.dp.toPx())
        drawLine(axisColor, Offset(left, plotBottom), Offset(left + plotWidth, plotBottom), 1.dp.toPx())

        for (line in series) {
            val points = line.points.sortedBy { it.timeNs }.map { Offset(x(it.timeNs), y(it.value)) }
            if (points.isEmpty()) continue
            val path = Path().apply {
                moveTo(points.first().x, points.first().y)
                points.drop(1).forEach { lineTo(it.x, it.y) }
            }
            drawPath(path, line.color, style = Stroke(width = 2.dp.toPx()))
            if (line.showMarkers || points.size == 1) {
                points.forEach { drawCircle(line.color, radius = 3.5.dp.toPx(), center = it) }
            }
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        series.forEach { line ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Box(Modifier.size(10.dp).background(line.color))
                Text(line.label, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

/** A round step (1, 2 or 5 times a power of ten) no smaller than `rough`. */
private fun niceStep(rough: Double): Double {
    if (!rough.isFinite() || rough <= 0.0) return 1.0
    val magnitude = 10.0.pow(floor(log10(rough)))
    val normalized = rough / magnitude
    return magnitude * when {
        normalized <= 1.0 -> 1.0
        normalized <= 2.0 -> 2.0
        normalized <= 5.0 -> 5.0
        else -> 10.0
    }
}

/** Multiples of `step` from 0 up to `maximum`; multiplied rather than summed to avoid drift. */
private fun ticks(maximum: Double, step: Double): List<Double> =
    (0..floor(maximum / step + 1e-9).toInt()).map { it * step }

private fun formatTick(value: Double, step: Double): String {
    val decimals = if (step >= 1.0) 0 else ceil(-log10(step)).toInt()
    return String.format(Locale.US, "%.${decimals}f", value)
}

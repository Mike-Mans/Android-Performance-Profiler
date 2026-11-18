package com.example.profiler

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Most matches shown at once; typing narrows the list further. */
private const val MAX_SHOWN = 50

internal data class InstalledPackage(val name: String, val thirdParty: Boolean)

/** Parse the payload of GET /packages. */
internal fun readPackages(payload: JSONObject): List<InstalledPackage> {
    val array = payload.getJSONArray("packages")
    return (0 until array.length()).map { index ->
        val entry = array.getJSONObject(index)
        InstalledPackage(entry.getString("package"), entry.optBoolean("third_party", false))
    }.sortedWith(compareBy({ !it.thirdParty }, { it.name }))
}

/**
 * Target package field backed by the device's installed packages. Typing filters
 * the list; picking an entry fills the field. When the list could not be loaded,
 * the field still accepts a typed package name.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun PackagePicker(
    value: String,
    onValueChange: (String) -> Unit,
    packages: List<InstalledPackage>?,
    loadError: String?,
    enabled: Boolean,
    onRetry: () -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    var includeSystem by rememberSaveable { mutableStateOf(false) }
    val installed = packages?.any { it.name == value.trim() }
    // An exact, already-selected name shows the whole list instead of only itself.
    val query = if (installed == true) "" else value.trim()
    val matches = packages.orEmpty().filter {
        (includeSystem || it.thirdParty || query.isNotEmpty()) && it.name.contains(query, ignoreCase = true)
    }

    ExposedDropdownMenuBox(
        expanded = expanded && enabled && packages != null,
        onExpandedChange = { expanded = it },
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = { onValueChange(it); expanded = true },
            label = { Text("Target package") },
            enabled = enabled,
            singleLine = true,
            trailingIcon = { if (packages != null) ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            supportingText = {
                when {
                    loadError != null -> Text("Package list unavailable · type a package name")
                    packages == null -> Text("Loading installed packages…")
                    installed == false && value.isNotBlank() -> Text("Not installed on this device")
                    else -> Text("${packages.size} packages installed")
                }
            },
            isError = installed == false && value.isNotBlank(),
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(
            expanded = expanded && enabled && packages != null,
            onDismissRequest = { expanded = false },
        ) {
            if (matches.isEmpty()) {
                DropdownMenuItem(text = { Text("No matching packages") }, onClick = {}, enabled = false)
            }
            matches.take(MAX_SHOWN).forEach { entry ->
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(entry.name)
                            if (!entry.thirdParty) Text("System app", style = MaterialTheme.typography.labelSmall)
                        }
                    },
                    onClick = { onValueChange(entry.name); expanded = false },
                )
            }
            if (matches.size > MAX_SHOWN) {
                DropdownMenuItem(
                    text = { Text("${matches.size - MAX_SHOWN} more · keep typing to narrow down") },
                    onClick = {},
                    enabled = false,
                )
            }
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(
            selected = includeSystem,
            onClick = { includeSystem = !includeSystem },
            label = { Text("Show system apps") },
            enabled = enabled && packages != null,
        )
        if (loadError != null) TextButton(onClick = onRetry, enabled = enabled) { Text("Retry") }
    }
}

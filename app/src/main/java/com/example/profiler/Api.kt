package com.example.profiler

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal object Api {
    suspend fun request(path: String, body: JSONObject? = null): JSONObject =
        withContext(Dispatchers.IO) {
            val connection = URL("http://127.0.0.1:9095$path").openConnection() as HttpURLConnection
            try {
                connection.connectTimeout = 5000
                connection.readTimeout = 15000
                connection.instanceFollowRedirects = false
                if (body != null) {
                    connection.requestMethod = "POST"
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
                }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val content = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    val detail = runCatching { JSONObject(content).optString("detail") }.getOrNull()
                    error(detail?.takeIf { it.isNotBlank() } ?: "Bridge request failed (HTTP $code)")
                }
                JSONObject(content)
            } finally {
                connection.disconnect()
            }
        }
}

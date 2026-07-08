package com.roba.adblock

import android.content.Context
import java.io.BufferedReader

/**
 * Set of blocked domains loaded from assets/blocklist.txt.
 * A host is blocked if it or any of its parent domains is listed,
 * so "doubleclick.net" also blocks "ad.doubleclick.net".
 */
class BlockList(private val domains: Set<String>) {

    val size: Int get() = domains.size

    fun isBlocked(host: String): Boolean {
        var h = host
        while (true) {
            if (h in domains) return true
            val dot = h.indexOf('.')
            if (dot < 0) return false
            h = h.substring(dot + 1)
        }
    }

    companion object {
        /** Accepts bare domains and hosts-file lines ("0.0.0.0 example.com"). */
        fun load(context: Context): BlockList {
            val domains = HashSet<String>()
            context.assets.open("blocklist.txt").bufferedReader().use { reader: BufferedReader ->
                reader.forEachLine { raw ->
                    var line = raw.trim()
                    val comment = line.indexOf('#')
                    if (comment >= 0) line = line.substring(0, comment).trim()
                    if (line.isEmpty()) return@forEachLine
                    val parts = line.split(Regex("\\s+"))
                    val domain = if (parts.size >= 2) parts[1] else parts[0]
                    if (domain.contains('.')) domains.add(domain.lowercase())
                }
            }
            return BlockList(domains)
        }
    }
}

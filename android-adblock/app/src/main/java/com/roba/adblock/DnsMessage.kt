package com.roba.adblock

/**
 * Minimal DNS wire-format helpers: extract the question name from a query
 * and build an NXDOMAIN response for blocked domains.
 */
object DnsMessage {

    private const val HEADER_SIZE = 12

    /** Returns the lowercase question name of the first question, or null if unparsable. */
    fun parseQueryName(dns: ByteArray): String? {
        if (dns.size < HEADER_SIZE + 5) return null
        val qdCount = readU16(dns, 4)
        if (qdCount < 1) return null

        var pos = HEADER_SIZE
        val sb = StringBuilder()
        while (pos < dns.size) {
            val len = dns[pos].toInt() and 0xFF
            if (len == 0) break
            // Compression pointers never appear in a normal question name.
            if (len >= 0xC0) return null
            if (pos + 1 + len > dns.size) return null
            if (sb.isNotEmpty()) sb.append('.')
            sb.append(String(dns, pos + 1, len, Charsets.US_ASCII))
            pos += len + 1
            if (sb.length > 255) return null
        }
        if (sb.isEmpty()) return null
        return sb.toString().lowercase()
    }

    /**
     * Builds an NXDOMAIN response from a query: same ID and question,
     * QR/RA set, RCODE=3, all other sections dropped.
     */
    fun buildBlockedResponse(query: ByteArray): ByteArray? {
        return buildErrorResponse(query, 3)
    }

    /** Builds a SERVFAIL response when every upstream resolver is unavailable. */
    fun buildServerFailureResponse(query: ByteArray): ByteArray? {
        return buildErrorResponse(query, 2)
    }

    private fun buildErrorResponse(query: ByteArray, responseCode: Int): ByteArray? {
        val qEnd = questionEnd(query) ?: return null
        val resp = query.copyOfRange(0, qEnd)
        // Byte 2: QR=1, opcode=0, AA=0, TC=0, keep RD.
        resp[2] = ((resp[2].toInt() and 0x01) or 0x80).toByte()
        // Byte 3: RA=1 plus the requested DNS response code.
        resp[3] = (0x80 or (responseCode and 0x0F)).toByte()
        // QDCOUNT stays 1; zero out AN/NS/AR counts.
        for (i in 6..11) resp[i] = 0
        return resp
    }

    /** Offset just past the first question (name + qtype + qclass), or null. */
    private fun questionEnd(dns: ByteArray): Int? {
        var pos = HEADER_SIZE
        while (pos < dns.size) {
            val len = dns[pos].toInt() and 0xFF
            if (len == 0) {
                pos += 1
                break
            }
            if (len >= 0xC0) {
                pos += 2
                break
            }
            pos += len + 1
        }
        val end = pos + 4
        return if (end <= dns.size) end else null
    }

    fun readU16(buf: ByteArray, offset: Int): Int =
        ((buf[offset].toInt() and 0xFF) shl 8) or (buf[offset + 1].toInt() and 0xFF)
}

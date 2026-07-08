package com.roba.adblock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DnsMessageTest {
    @Test
    fun parsesQuestionName() {
        assertEquals("ads.example.com", DnsMessage.parseQueryName(query()))
    }

    @Test
    fun rejectsTruncatedQuestion() {
        assertNull(DnsMessage.parseQueryName(query().copyOf(14)))
    }

    @Test
    fun blockedResponseIsNxdomainAndKeepsTransactionId() {
        val response = requireNotNull(DnsMessage.buildBlockedResponse(query()))
        assertEquals(0x1234, DnsMessage.readU16(response, 0))
        assertEquals(0x81, response[2].toInt() and 0xFF)
        assertEquals(3, response[3].toInt() and 0x0F)
        assertEquals(1, DnsMessage.readU16(response, 4))
        assertEquals(0, DnsMessage.readU16(response, 6))
        assertEquals(0, DnsMessage.readU16(response, 8))
        assertEquals(0, DnsMessage.readU16(response, 10))
    }

    @Test
    fun upstreamFailureReturnsServfail() {
        val response = requireNotNull(DnsMessage.buildServerFailureResponse(query()))
        assertEquals(2, response[3].toInt() and 0x0F)
    }

    private fun query(): ByteArray = byteArrayOf(
        0x12, 0x34, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x03, 'a'.code.toByte(), 'd'.code.toByte(), 's'.code.toByte(),
        0x07, 'e'.code.toByte(), 'x'.code.toByte(), 'a'.code.toByte(),
        'm'.code.toByte(), 'p'.code.toByte(), 'l'.code.toByte(), 'e'.code.toByte(),
        0x03, 'c'.code.toByte(), 'o'.code.toByte(), 'm'.code.toByte(),
        0x00, 0x00, 0x01, 0x00, 0x01,
    )
}

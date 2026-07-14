package com.roba.adblock

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockListTest {
    private val list = BlockList(
        domains = setOf("doubleclick.net", "ads.example.com", "googlevideo.com", "twimg.com"),
        allowedDomains = setOf("googlevideo.com", "twimg.com"),
    )

    @Test
    fun blocksExactDomain() {
        assertTrue(list.isBlocked("doubleclick.net"))
    }

    @Test
    fun blocksSubdomain() {
        assertTrue(list.isBlocked("pagead.doubleclick.net"))
    }

    @Test
    fun doesNotBlockUnlistedParentOrSibling() {
        assertFalse(list.isBlocked("example.com"))
        assertFalse(list.isBlocked("www.example.com"))
    }

    @Test
    fun allowlistOverridesBlocklistForYouTubeAndXContent() {
        assertFalse(list.isBlocked("rr1---sn.example.googlevideo.com"))
        assertFalse(list.isBlocked("video.twimg.com"))
    }

    @Test
    fun matchingIsCaseInsensitiveAndAcceptsTrailingDot() {
        assertTrue(list.isBlocked("PAGEAD.DOUBLECLICK.NET."))
    }
}

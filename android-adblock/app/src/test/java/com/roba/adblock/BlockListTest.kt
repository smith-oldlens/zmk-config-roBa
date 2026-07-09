package com.roba.adblock

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockListTest {
    private val list = BlockList(setOf("doubleclick.net", "ads.example.com"))

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
}

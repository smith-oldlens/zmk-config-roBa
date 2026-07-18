package com.roba.adblock

import android.content.Context

/**
 * User-chosen set of app package names that should be routed OUTSIDE the VPN,
 * i.e. left unfiltered. Some apps (notably Netflix's ad-supported tier) gate
 * their own service on shared ad/measurement domains that the blocklist blocks;
 * excluding just those apps keeps them working while every other app stays
 * filtered.
 *
 * Persisted in SharedPreferences. On first run the set is seeded with a small
 * default so the common breakage (Netflix) works out of the box.
 */
object ExcludedApps {
    private const val PREFS = "adblock_prefs"
    private const val KEY = "excluded_apps"

    /** Apps excluded by default until the user edits the list. */
    val DEFAULT: Set<String> = setOf("com.netflix.mediaclient")

    fun get(context: Context): Set<String> {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        // A missing key means "never edited" -> use the default seed. An empty
        // stored set is a deliberate choice and is respected.
        return prefs.getStringSet(KEY, null)?.toSet() ?: DEFAULT
    }

    fun set(context: Context, packages: Set<String>) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putStringSet(KEY, packages)
            .apply()
    }
}

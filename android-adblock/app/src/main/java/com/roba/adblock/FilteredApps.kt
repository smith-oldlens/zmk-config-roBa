package com.roba.adblock

import android.content.Context

/**
 * User-chosen set of app package names whose traffic is routed THROUGH the VPN
 * and therefore ad-filtered. This is an opt-in model: only the apps the user
 * checks are affected; every other app bypasses the VPN entirely and behaves
 * exactly as if the blocker were off. That makes accidental breakage impossible
 * for apps the user did not pick.
 *
 * Persisted in SharedPreferences. On first run the set is seeded with the common
 * browsers, where DNS-based ad blocking is most effective and least risky, so
 * browser ad blocking works out of the box.
 */
object FilteredApps {
    private const val PREFS = "adblock_prefs"
    private const val KEY = "filtered_apps"

    /** Apps filtered by default until the user edits the list. */
    val DEFAULT: Set<String> = setOf(
        "com.android.chrome",            // Google Chrome
        "com.sec.android.app.sbrowser",  // Samsung Internet
    )

    fun get(context: Context): Set<String> {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        // A missing key means "never edited" -> use the default seed. An empty
        // stored set is a deliberate choice (filter nothing) and is respected.
        return prefs.getStringSet(KEY, null)?.toSet() ?: DEFAULT
    }

    fun set(context: Context, packages: Set<String>) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putStringSet(KEY, packages)
            .apply()
    }
}

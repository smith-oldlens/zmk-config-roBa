package com.roba.adblock

import android.app.Activity
import android.content.Intent
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.CheckBox
import android.widget.ImageView
import android.widget.ListView
import android.widget.TextView

/**
 * Lets the user pick which installed apps are ad-filtered (routed through the
 * VPN). Checked = blocked; everything unchecked is left completely untouched.
 * The selection is persisted via [FilteredApps] and takes effect the next time
 * the VPN starts; [MainActivity] restarts it on return when already running.
 */
class AppPickerActivity : Activity() {

    private data class AppEntry(val pkg: String, val label: String, val icon: Drawable)

    private val selected = HashSet<String>()
    private var savedSelection: Set<String> = emptySet()
    private val entries = ArrayList<AppEntry>()
    private lateinit var adapter: AppAdapter
    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_app_picker)
        savedSelection = FilteredApps.get(this)
        selected.addAll(savedSelection)
        adapter = AppAdapter()
        findViewById<ListView>(R.id.app_list).adapter = adapter
        loadAppsAsync()
    }

    private fun loadAppsAsync() {
        Thread {
            val pm = packageManager
            val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            val seen = HashSet<String>()
            val loaded = ArrayList<AppEntry>()
            for (ri in pm.queryIntentActivities(launcher, 0)) {
                val pkg = ri.activityInfo.packageName
                if (pkg == packageName || !seen.add(pkg)) continue
                loaded.add(AppEntry(pkg, ri.loadLabel(pm).toString(), ri.loadIcon(pm)))
            }
            loaded.sortBy { it.label.lowercase() }
            mainHandler.post {
                entries.clear()
                entries.addAll(loaded)
                adapter.notifyDataSetChanged()
            }
        }.start()
    }

    override fun onPause() {
        super.onPause()
        // Persist only real changes; signal MainActivity so it can re-apply.
        if (selected != savedSelection) {
            FilteredApps.set(this, HashSet(selected))
            savedSelection = HashSet(selected)
            setResult(RESULT_OK)
        }
    }

    private inner class AppAdapter : BaseAdapter() {
        override fun getCount() = entries.size
        override fun getItem(position: Int) = entries[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val view = convertView
                ?: layoutInflater.inflate(R.layout.row_app, parent, false)
            val entry = entries[position]
            view.findViewById<ImageView>(R.id.app_icon).setImageDrawable(entry.icon)
            view.findViewById<TextView>(R.id.app_label).text = entry.label
            val check = view.findViewById<CheckBox>(R.id.app_check)
            check.isChecked = selected.contains(entry.pkg)
            view.setOnClickListener {
                val nowFiltered = !selected.contains(entry.pkg)
                if (nowFiltered) selected.add(entry.pkg) else selected.remove(entry.pkg)
                check.isChecked = nowFiltered
            }
            return view
        }
    }
}

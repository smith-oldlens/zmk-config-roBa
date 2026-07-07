package com.roba.adblock

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.TextView

class MainActivity : Activity() {

    private lateinit var statusText: TextView
    private lateinit var toggleButton: Button
    private val handler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusText = findViewById(R.id.status_text)
        toggleButton = findViewById(R.id.toggle_button)

        toggleButton.setOnClickListener {
            if (AdBlockVpnService.isRunning) stopVpn() else prepareAndStart()
        }

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQUEST_NOTIFICATION)
        }
    }

    override fun onResume() {
        super.onResume()
        updateUi()
    }

    private fun prepareAndStart() {
        val consentIntent = VpnService.prepare(this)
        if (consentIntent != null) {
            startActivityForResult(consentIntent, REQUEST_VPN_CONSENT)
        } else {
            startVpn()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_VPN_CONSENT && resultCode == RESULT_OK) {
            startVpn()
        }
    }

    private fun startVpn() {
        startForegroundService(
            Intent(this, AdBlockVpnService::class.java).setAction(AdBlockVpnService.ACTION_START)
        )
        updateUiSoon()
    }

    private fun stopVpn() {
        startService(
            Intent(this, AdBlockVpnService::class.java).setAction(AdBlockVpnService.ACTION_STOP)
        )
        updateUiSoon()
    }

    private fun updateUiSoon() {
        updateUi()
        handler.postDelayed({ updateUi() }, 500)
    }

    private fun updateUi() {
        if (AdBlockVpnService.isRunning) {
            statusText.setText(R.string.status_on)
            toggleButton.setText(R.string.button_stop)
        } else {
            statusText.setText(R.string.status_off)
            toggleButton.setText(R.string.button_start)
        }
    }

    companion object {
        private const val REQUEST_VPN_CONSENT = 1
        private const val REQUEST_NOTIFICATION = 2
    }
}

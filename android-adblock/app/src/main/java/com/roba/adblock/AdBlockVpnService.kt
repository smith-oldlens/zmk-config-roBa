package com.roba.adblock

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.VpnService
import android.os.ParcelFileDescriptor
import android.util.Log
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * DNS-filtering VPN in the style of DNS66 / personalDNSfilter.
 *
 * Only the virtual DNS server address is routed into the TUN device, so all
 * ordinary traffic (web pages, video, games, ...) flows over the normal
 * network untouched. DNS queries for blocked ad domains get an NXDOMAIN
 * answer; everything else is forwarded to a real resolver.
 */
class AdBlockVpnService : VpnService() {

    companion object {
        const val ACTION_START = "com.roba.adblock.START"
        const val ACTION_STOP = "com.roba.adblock.STOP"

        private const val TAG = "AdBlockVpn"
        private const val VPN_ADDRESS = "10.111.222.1"
        private const val VIRTUAL_DNS = "10.111.222.53"
        private const val UPSTREAM_DNS = "1.1.1.1"
        private const val CHANNEL_ID = "adblock_vpn"
        private const val NOTIFICATION_ID = 1

        @Volatile
        var isRunning = false
            private set
    }

    private var tun: ParcelFileDescriptor? = null
    private var workerThread: Thread? = null
    private var executor: ExecutorService? = null
    private var blockList = BlockList(emptySet())

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopVpn()
            stopSelf()
            return START_NOT_STICKY
        }
        if (isRunning) return START_STICKY
        startVpn()
        return START_STICKY
    }

    private fun startVpn() {
        blockList = try {
            BlockList.load(this)
        } catch (e: IOException) {
            Log.e(TAG, "failed to load blocklist", e)
            BlockList(emptySet())
        }

        val builder = Builder()
            .setSession(getString(R.string.app_name))
            .setMtu(1500)
            .addAddress(VPN_ADDRESS, 24)
            .addDnsServer(VIRTUAL_DNS)
            .addRoute(VIRTUAL_DNS, 32)
            .setBlocking(true)
        try {
            builder.addDisallowedApplication(packageName)
        } catch (e: Exception) {
            Log.w(TAG, "could not exclude self from VPN", e)
        }

        val fd = builder.establish()
        if (fd == null) {
            Log.e(TAG, "VpnService.establish() returned null")
            stopSelf()
            return
        }
        tun = fd
        executor = Executors.newFixedThreadPool(4)
        isRunning = true
        startForeground(NOTIFICATION_ID, buildNotification())

        workerThread = Thread({ packetLoop(fd) }, "adblock-tun-reader").also { it.start() }
        Log.i(TAG, "VPN started, ${blockList.size} blocked domains")
    }

    private fun packetLoop(fd: ParcelFileDescriptor) {
        val input = FileInputStream(fd.fileDescriptor)
        val output = FileOutputStream(fd.fileDescriptor)
        val buffer = ByteArray(32767)
        try {
            while (isRunning) {
                val len = input.read(buffer)
                if (len < 0) break
                if (len > 0) handlePacket(buffer.copyOf(len), output)
            }
        } catch (e: IOException) {
            if (isRunning) Log.w(TAG, "tun read loop ended", e)
        }
    }

    /** Handles one IP packet from the TUN device. Only IPv4/UDP/53 is expected. */
    private fun handlePacket(packet: ByteArray, output: FileOutputStream) {
        if (packet.size < 28) return
        val version = (packet[0].toInt() shr 4) and 0xF
        if (version != 4) return
        val ihl = (packet[0].toInt() and 0xF) * 4
        if (ihl < 20 || packet.size < ihl + 8) return
        if ((packet[9].toInt() and 0xFF) != 17) return // UDP only

        val srcIp = packet.copyOfRange(12, 16)
        val dstIp = packet.copyOfRange(16, 20)
        val srcPort = DnsMessage.readU16(packet, ihl)
        val dstPort = DnsMessage.readU16(packet, ihl + 2)
        if (dstPort != 53) return

        val dns = packet.copyOfRange(ihl + 8, packet.size)
        val name = DnsMessage.parseQueryName(dns)

        if (name != null && blockList.isBlocked(name)) {
            val response = DnsMessage.buildBlockedResponse(dns) ?: return
            writeUdpPacket(output, dstIp, dstPort, srcIp, srcPort, response)
        } else {
            executor?.execute { forwardQuery(dns, srcIp, srcPort, dstIp, dstPort, output) }
        }
    }

    /** Sends the query to the real resolver outside the VPN and relays the answer back. */
    private fun forwardQuery(
        dns: ByteArray,
        srcIp: ByteArray,
        srcPort: Int,
        dstIp: ByteArray,
        dstPort: Int,
        output: FileOutputStream,
    ) {
        try {
            DatagramSocket().use { socket ->
                protect(socket)
                socket.soTimeout = 5000
                socket.send(DatagramPacket(dns, dns.size, InetAddress.getByName(UPSTREAM_DNS), 53))
                val buf = ByteArray(4096)
                val reply = DatagramPacket(buf, buf.size)
                socket.receive(reply)
                writeUdpPacket(output, dstIp, dstPort, srcIp, srcPort, buf.copyOf(reply.length))
            }
        } catch (e: IOException) {
            // Timeouts and network hiccups: drop the query, the client will retry.
        }
    }

    /** Writes an IPv4/UDP packet carrying [payload] back into the TUN device. */
    private fun writeUdpPacket(
        output: FileOutputStream,
        srcIp: ByteArray,
        srcPort: Int,
        dstIp: ByteArray,
        dstPort: Int,
        payload: ByteArray,
    ) {
        val udpLen = 8 + payload.size
        val totalLen = 20 + udpLen
        val p = ByteArray(totalLen)

        p[0] = 0x45 // IPv4, IHL=5
        p[2] = (totalLen shr 8).toByte()
        p[3] = totalLen.toByte()
        p[6] = 0x40 // don't fragment
        p[8] = 64   // TTL
        p[9] = 17   // UDP
        System.arraycopy(srcIp, 0, p, 12, 4)
        System.arraycopy(dstIp, 0, p, 16, 4)
        val ipCk = checksum(p, 0, 20)
        p[10] = (ipCk shr 8).toByte()
        p[11] = ipCk.toByte()

        p[20] = (srcPort shr 8).toByte()
        p[21] = srcPort.toByte()
        p[22] = (dstPort shr 8).toByte()
        p[23] = dstPort.toByte()
        p[24] = (udpLen shr 8).toByte()
        p[25] = udpLen.toByte()
        System.arraycopy(payload, 0, p, 28, payload.size)
        val udpCk = udpChecksum(p, srcIp, dstIp, udpLen)
        p[26] = (udpCk shr 8).toByte()
        p[27] = udpCk.toByte()

        try {
            synchronized(output) { output.write(p) }
        } catch (e: IOException) {
            // TUN already closed; nothing to do.
        }
    }

    private fun checksum(data: ByteArray, offset: Int, length: Int, initial: Long = 0): Int {
        var sum = initial
        var i = offset
        val end = offset + length
        while (i + 1 < end) {
            sum += ((data[i].toInt() and 0xFF) shl 8 or (data[i + 1].toInt() and 0xFF)).toLong()
            i += 2
        }
        if (i < end) sum += ((data[i].toInt() and 0xFF) shl 8).toLong()
        while (sum > 0xFFFF) sum = (sum and 0xFFFF) + (sum shr 16)
        return sum.inv().toInt() and 0xFFFF
    }

    private fun udpChecksum(packet: ByteArray, srcIp: ByteArray, dstIp: ByteArray, udpLen: Int): Int {
        var pseudo = 0L
        pseudo += ((srcIp[0].toInt() and 0xFF) shl 8 or (srcIp[1].toInt() and 0xFF)).toLong()
        pseudo += ((srcIp[2].toInt() and 0xFF) shl 8 or (srcIp[3].toInt() and 0xFF)).toLong()
        pseudo += ((dstIp[0].toInt() and 0xFF) shl 8 or (dstIp[1].toInt() and 0xFF)).toLong()
        pseudo += ((dstIp[2].toInt() and 0xFF) shl 8 or (dstIp[3].toInt() and 0xFF)).toLong()
        pseudo += 17L
        pseudo += udpLen.toLong()
        val ck = checksum(packet, 20, udpLen, pseudo)
        return if (ck == 0) 0xFFFF else ck
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel),
                NotificationManager.IMPORTANCE_LOW
            )
        )
        val contentIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notification_running))
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .build()
    }

    private fun stopVpn() {
        isRunning = false
        workerThread?.interrupt()
        workerThread = null
        executor?.shutdownNow()
        executor = null
        try {
            tun?.close()
        } catch (e: IOException) {
            // ignore
        }
        tun = null
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    override fun onRevoke() {
        stopVpn()
        stopSelf()
    }

    override fun onDestroy() {
        stopVpn()
        super.onDestroy()
    }
}

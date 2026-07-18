package com.roba.adblock

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.VpnService
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.URL
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.HttpsURLConnection

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
        private val UPSTREAM_DOH_URLS = listOf(
            "https://cloudflare-dns.com/dns-query",
            "https://dns.google/dns-query",
        )
        // Plain-UDP resolvers used only when every DoH endpoint fails, so name
        // resolution keeps working instead of returning SERVFAIL to the app.
        private val UPSTREAM_PLAIN_DNS = listOf("1.1.1.1", "8.8.8.8")

        // Apps that break under DNS ad-filtering because the service itself
        // depends on shared ad/measurement domains (which we cannot allowlist
        // without unblocking them everywhere). They are routed outside the VPN
        // entirely, so they work normally while every other app stays filtered.
        // Netflix's ad-supported tier gates playback on Xandr (adnxs.com) and
        // Comscore (scorecardresearch.com).
        private val EXCLUDED_APPS = listOf(
            "com.netflix.mediaclient",
        )
        private const val CHANNEL_ID = "adblock_vpn"
        private const val NOTIFICATION_ID = 1
        private const val VPN_MTU = 9000
        // DNS forwarding is network-I/O bound, so more workers than CPUs is fine
        // and lets bursty apps (YouTube, Play Store, ...) resolve in parallel
        // instead of queueing up behind a handful of slow DoH round-trips.
        private const val DNS_WORKERS = 12
        private const val DNS_QUEUE_CAPACITY = 512
        private const val DNS_CONNECT_TIMEOUT_MS = 2_000
        private const val DNS_TIMEOUT_MS = 5_000
        private const val PLAIN_DNS_TIMEOUT_MS = 3_000
        private const val MAX_DNS_MESSAGE_SIZE = VPN_MTU - 28

        // If DoH fails this many times in a row it is probably blocked or
        // throttled on this network, so we stop trying it for a while and go
        // straight to plain DNS. Otherwise every single query would waste
        // seconds on dead DoH endpoints, stalling the worker pool and making
        // bursty apps (X, YouTube, ...) time out even though DNS "works".
        private const val DOH_FAILURE_THRESHOLD = 2
        private const val DOH_SUSPEND_MS = 60_000L

        @Volatile
        var isRunning = false
            private set
    }

    private var tun: ParcelFileDescriptor? = null
    private var workerThread: Thread? = null
    private var executor: ThreadPoolExecutor? = null
    private var blockList = BlockList(emptySet())
    private lateinit var connectivityManager: ConnectivityManager

    // DoH health tracking (see DOH_FAILURE_THRESHOLD).
    private val dohConsecutiveFailures = AtomicInteger(0)

    @Volatile
    private var dohSuspendedUntilMs = 0L

    @Volatile
    private var underlyingNetwork: Network? = null

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            underlyingNetwork = network
            if (isRunning) setUnderlyingNetworks(arrayOf(network))
        }

        override fun onLost(network: Network) {
            if (underlyingNetwork == network) underlyingNetwork = null
        }
    }

    override fun onCreate() {
        super.onCreate()
        connectivityManager = getSystemService(ConnectivityManager::class.java)
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
            .build()
        connectivityManager.registerNetworkCallback(request, networkCallback)
        underlyingNetwork = connectivityManager.activeNetwork?.takeIf(::isUsableUnderlyingNetwork)
    }

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
            .setMtu(VPN_MTU)
            .addAddress(VPN_ADDRESS, 24)
            .addDnsServer(VIRTUAL_DNS)
            .addRoute(VIRTUAL_DNS, 32)
            .setBlocking(true)

        val network = currentUnderlyingNetwork()
        try {
            builder.addDisallowedApplication(packageName)
        } catch (e: Exception) {
            Log.w(TAG, "could not exclude self from VPN", e)
        }
        // Route known-incompatible apps outside the VPN. addDisallowedApplication
        // throws if the package is not installed, so guard each one individually.
        for (pkg in EXCLUDED_APPS) {
            try {
                builder.addDisallowedApplication(pkg)
                Log.i(TAG, "excluded $pkg from VPN")
            } catch (e: Exception) {
                Log.d(TAG, "excluded app $pkg not installed; skipping")
            }
        }

        val fd = builder.establish()
        if (fd == null) {
            Log.e(TAG, "VpnService.establish() returned null")
            stopSelf()
            return
        }
        tun = fd
        network?.let { setUnderlyingNetworks(arrayOf(it)) }
        executor = ThreadPoolExecutor(
            DNS_WORKERS,
            DNS_WORKERS,
            0L,
            TimeUnit.MILLISECONDS,
            ArrayBlockingQueue(DNS_QUEUE_CAPACITY),
        )
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
            try {
                executor?.execute { forwardQuery(dns, srcIp, srcPort, dstIp, dstPort, output) }
            } catch (_: RejectedExecutionException) {
                // Under a heavy burst we drop the query instead of answering
                // SERVFAIL: the client's resolver simply retries once the queue
                // drains, whereas a SERVFAIL makes apps like YouTube conclude
                // the network is offline.
                Log.w(TAG, "DNS queue full; dropping query so the client retries")
            }
        }
    }

    /** Sends the query to an encrypted DNS resolver and relays the answer back. */
    private fun forwardQuery(
        dns: ByteArray,
        srcIp: ByteArray,
        srcPort: Int,
        dstIp: ByteArray,
        dstPort: Int,
        output: FileOutputStream,
    ) {
        // Prefer encrypted DoH for privacy, but skip it while it is suspended
        // (see below) so a blocked/throttled DoH endpoint does not add seconds
        // of latency to every query.
        if (SystemClock.elapsedRealtime() >= dohSuspendedUntilMs) {
            for (resolverUrl in UPSTREAM_DOH_URLS) {
                try {
                    val reply = queryDoh(resolverUrl, dns)
                    dohConsecutiveFailures.set(0)
                    writeUdpPacket(output, dstIp, dstPort, srcIp, srcPort, reply)
                    return
                } catch (e: IOException) {
                    Log.w(TAG, "DoH resolver $resolverUrl unavailable; trying next", e)
                }
            }
            // Every DoH endpoint failed. After a few consecutive failures assume
            // DoH is blocked on this network and stop using it for a while; it is
            // re-probed automatically once the suspension expires.
            if (dohConsecutiveFailures.incrementAndGet() >= DOH_FAILURE_THRESHOLD) {
                dohSuspendedUntilMs = SystemClock.elapsedRealtime() + DOH_SUSPEND_MS
                Log.w(TAG, "DoH looks blocked; using plain DNS for ${DOH_SUSPEND_MS / 1000}s")
            }
        }
        // Fall back to plain UDP DNS so resolution still succeeds rather than
        // reporting the name as unreachable.
        for (server in UPSTREAM_PLAIN_DNS) {
            try {
                val reply = queryPlainDns(server, dns)
                writeUdpPacket(output, dstIp, dstPort, srcIp, srcPort, reply)
                return
            } catch (e: IOException) {
                Log.w(TAG, "plain DNS resolver $server unavailable; trying next", e)
            }
        }
        writeErrorResponse(dns, srcIp, srcPort, dstIp, dstPort, output)
    }

    private fun queryDoh(resolverUrl: String, dns: ByteArray): ByteArray {
        val network = currentUnderlyingNetwork() ?: throw IOException("No underlying network")
        val connection = network.openConnection(URL(resolverUrl)) as HttpsURLConnection
        connection.requestMethod = "POST"
        connection.connectTimeout = DNS_CONNECT_TIMEOUT_MS
        connection.readTimeout = DNS_TIMEOUT_MS
        connection.doOutput = true
        connection.instanceFollowRedirects = false
        connection.setRequestProperty("Accept", "application/dns-message")
        connection.setRequestProperty("Content-Type", "application/dns-message")
        connection.setFixedLengthStreamingMode(dns.size)

        try {
            connection.outputStream.use { it.write(dns) }
            if (connection.responseCode != HttpsURLConnection.HTTP_OK) {
                throw IOException("DoH returned HTTP ${connection.responseCode}")
            }
            // Read fully and close (but do NOT disconnect) so the keep-alive
            // connection returns to the pool and the next query reuses the TLS
            // session instead of paying for a fresh handshake every time.
            return connection.inputStream.use { input -> readDnsMessage(input, dns) }
        } catch (e: IOException) {
            // A failed connection is poisoned; drop it rather than pooling it.
            connection.disconnect()
            throw e
        }
    }

    /** Forwards the query over an unencrypted UDP socket kept off the VPN. */
    private fun queryPlainDns(server: String, dns: ByteArray): ByteArray {
        val network = currentUnderlyingNetwork() ?: throw IOException("No underlying network")
        DatagramSocket().use { socket ->
            protect(socket)
            network.bindSocket(socket)
            socket.soTimeout = PLAIN_DNS_TIMEOUT_MS
            // Server is an IP literal, so getByName does not itself hit DNS.
            socket.send(DatagramPacket(dns, dns.size, InetAddress.getByName(server), 53))
            val buffer = ByteArray(4096)
            val reply = DatagramPacket(buffer, buffer.size)
            socket.receive(reply)
            val response = buffer.copyOf(reply.length)
            if (response.size < 12) throw IOException("DNS response too short")
            if (DnsMessage.readU16(response, 0) != DnsMessage.readU16(dns, 0)) {
                throw IOException("DNS transaction ID mismatch")
            }
            return response
        }
    }

    private fun readDnsMessage(input: InputStream, query: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val read = input.read(buffer)
            if (read < 0) break
            if (out.size() + read > MAX_DNS_MESSAGE_SIZE) {
                throw IOException("DNS response too large")
            }
            out.write(buffer, 0, read)
        }
        return out.toByteArray().also {
            if (it.size < 12) throw IOException("DNS response too short")
            if (DnsMessage.readU16(it, 0) != DnsMessage.readU16(query, 0)) {
                throw IOException("DNS transaction ID mismatch")
            }
        }
    }

    private fun currentUnderlyingNetwork(): Network? {
        return underlyingNetwork
            ?: connectivityManager.activeNetwork?.takeIf(::isUsableUnderlyingNetwork)
    }

    private fun isUsableUnderlyingNetwork(network: Network): Boolean {
        val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
    }

    private fun writeErrorResponse(
        dns: ByteArray,
        srcIp: ByteArray,
        srcPort: Int,
        dstIp: ByteArray,
        dstPort: Int,
        output: FileOutputStream,
    ) {
        val response = DnsMessage.buildServerFailureResponse(dns) ?: return
        writeUdpPacket(output, dstIp, dstPort, srcIp, srcPort, response)
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
        try {
            connectivityManager.unregisterNetworkCallback(networkCallback)
        } catch (_: IllegalArgumentException) {
            // Callback was already unregistered.
        }
        super.onDestroy()
    }
}

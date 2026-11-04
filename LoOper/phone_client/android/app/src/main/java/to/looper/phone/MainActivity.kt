package to.looper.phone

import android.Manifest
import android.app.Activity
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import com.google.zxing.integration.android.IntentIntegrator
import to.holepunch.bare.kit.IPC
import to.holepunch.bare.kit.Worklet
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets

/**
 * Android shell for the cross-platform LoOper phone client.
 *
 * Scans the host's `pear://<pubkey>` QR code, starts the Bare worklet
 * (core/entry.js, packed to assets/app.bundle) with that key, waits for its
 * `{"type":"ready","port":N}` IPC line, then points a WebView at the loopback
 * port the worklet exposes. The host serves its own chat page through the
 * hyperdht pipe, so there is no chat UI to build here.
 */
class MainActivity : Activity() {
  private var worklet: Worklet? = null
  private var ipc: IPC? = null
  private lateinit var webView: WebView
  private lateinit var status: TextView
  private lateinit var scanButton: Button
  private lateinit var assetLoader: WebViewAssetLoader
  private val pending = StringBuilder()
  private var isForeground = false
  // A getUserMedia request that arrived before the OS mic grant; WebView
  // rejects a grant made without it, so the answer is replayed afterwards.
  private var pendingMicRequest: PermissionRequest? = null
  private var micAsked = false

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    buildUi()
    requestRuntimePermissions()

    val pubkey = loadPubkey()
    if (pubkey != null) startClient(pubkey) else launchScanner()
  }

  // The web UI records voice via getUserMedia and raises per-message
  // notifications, so the OS-level RECORD_AUDIO (and, on API 33+,
  // POST_NOTIFICATIONS) permissions must be granted.  WebView's
  // onPermissionRequest only grants the web-level microphone resource — the
  // OS grant is what WebView actually checks, so both halves must line up.
  private fun hasMic(): Boolean =
    checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

  private fun hasNotifPermission(): Boolean =
    Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
      checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

  // WebView cannot start AudioRecord unless the app may take the audio path:
  // MODIFY_AUDIO_SETTINGS (manifest) plus, on some devices, communication mode
  // while capturing.  Without it the mic is refused with NotReadableError
  // ("Could not start audio source") even though RECORD_AUDIO is granted.
  private var previousAudioMode = -1

  private fun beginAudioCapture(mode: Int = AudioManager.MODE_IN_COMMUNICATION) {
    try {
      val am = getSystemService(AudioManager::class.java) ?: return
      if (previousAudioMode < 0) previousAudioMode = am.mode
      am.mode = mode
      Log.i("LoOperWeb", "[mic] audio mode $previousAudioMode -> $mode")
    } catch (e: Exception) {
      Log.w("LoOperWeb", "[mic] audio mode failed: ${e.message}")
    }
  }

  private fun endAudioCapture() {
    try {
      if (previousAudioMode < 0) return
      val am = getSystemService(AudioManager::class.java) ?: return
      am.mode = previousAudioMode
      Log.i("LoOperWeb", "[mic] audio mode restored to $previousAudioMode")
      previousAudioMode = -1
    } catch (e: Exception) {
      Log.w("LoOperWeb", "[mic] audio mode restore failed: ${e.message}")
    }
  }

  private fun audioModeName(): String = try {
    when (getSystemService(AudioManager::class.java)?.mode) {
      AudioManager.MODE_NORMAL -> "NORMAL"
      AudioManager.MODE_IN_COMMUNICATION -> "IN_COMMUNICATION"
      AudioManager.MODE_IN_CALL -> "IN_CALL"
      AudioManager.MODE_RINGTONE -> "RINGTONE"
      else -> "OTHER"
    }
  } catch (e: Exception) {
    "unknown"
  }

  private fun requestRuntimePermissions() {
    val needed = mutableListOf<String>()
    if (!hasMic()) {
      needed.add(Manifest.permission.RECORD_AUDIO)
      micAsked = true
    }
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
      needed.add(Manifest.permission.POST_NOTIFICATIONS)
    }
    if (needed.isNotEmpty()) requestPermissions(needed.toTypedArray(), REQ_PERMS)
  }

  override fun onRequestPermissionsResult(
    requestCode: Int, permissions: Array<out String>, grantResults: IntArray
  ) {
    super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    if (requestCode != REQ_PERMS) return
    val idx = permissions.indexOf(Manifest.permission.RECORD_AUDIO)
    if (idx < 0) return
    val granted = (idx < grantResults.size &&
      grantResults[idx] == PackageManager.PERMISSION_GRANTED) || hasMic()
    // Replay the held getUserMedia request with the real answer.
    val held = pendingMicRequest
    pendingMicRequest = null
    if (held != null) {
      try {
        if (granted) held.grant(held.resources) else held.deny()
      } catch (e: IllegalStateException) {
        Log.w("LoOperWeb", "[perm] replay failed: ${e.message}")
      }
    }
    Log.i("LoOperWeb", "[perm] RECORD_AUDIO granted=$granted")
    if (!granted) {
      Toast.makeText(
        this, "Microphone is off for Arrow - enable it in Settings", Toast.LENGTH_LONG
      ).show()
    }
  }

  // -- UI --------------------------------------------------------------------

  private fun buildUi() {
    val frame = FrameLayout(this)

    assetLoader = WebViewAssetLoader.Builder()
      .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
      .build()

    webView = WebView(this).apply {
      settings.javaScriptEnabled = true
      settings.domStorageEnabled = true
      // Let the bundled page's TTS auto-play and its mic (getUserMedia) record.
      settings.mediaPlaybackRequiresUserGesture = false
      // The page is served from an https asset origin and talks to the host
      // over the loopback pipe (ws://127.0.0.1:8081); loopback is a trustworthy
      // origin, but allow mixed content so ws:// is never blocked.
      settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
      // JS bridge so the page can raise one notification per agent message.
      addJavascriptInterface(Notifier(), "Android")
      webViewClient = object : WebViewClient() {
        // Serve the bundled UI assets from the secure asset origin.
        override fun shouldInterceptRequest(
          view: WebView?, request: WebResourceRequest?
        ): WebResourceResponse? {
          val r = request ?: return super.shouldInterceptRequest(view, request)
          return assetLoader.shouldInterceptRequest(r.url)
        }
      }
      webChromeClient = object : WebChromeClient() {
        // The UI records voice via getUserMedia; WebView denies it unless the
        // app grants the resource here (plus RECORD_AUDIO at the OS level).
        override fun onPermissionRequest(request: PermissionRequest) {
          val wantsAudio = request.resources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)
          Log.i("LoOperWeb", "[perm] web request audio=$wantsAudio osGranted=${hasMic()}")
          if (wantsAudio && !hasMic()) {
            // Granting now is silently rejected by WebView (it re-checks the
            // OS permission), so ask for it and answer in the callback.
            try { pendingMicRequest?.deny() } catch (_: IllegalStateException) {}
            pendingMicRequest = request
            if (!micAsked) requestRuntimePermissions()
            return
          }
          try {
            request.grant(request.resources)
          } catch (e: IllegalStateException) {
            Log.w("LoOperWeb", "[perm] grant failed: ${e.message}")
          }
        }
        // Surface page console output in logcat for field diagnosis.
        override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
          Log.i("LoOperWeb", "${msg.message()} @${msg.lineNumber()}")
          return true
        }
      }
    }
    frame.addView(webView, FrameLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))

    status = TextView(this).apply {
      setBackgroundColor(Color.parseColor("#CC000000"))
      setTextColor(Color.WHITE)
      setPadding(28, 28, 28, 28)
      setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
      visibility = View.GONE
    }
    frame.addView(status, FrameLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT, Gravity.TOP))

    scanButton = Button(this).apply {
      text = "Scan QR code"
      setOnClickListener { launchScanner() }
    }
    frame.addView(scanButton, FrameLayout.LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT, Gravity.CENTER))

    setContentView(frame)

    // targetSdk 35 forces edge-to-edge: pad the UI out from under the status
    // and navigation bars instead of letting the page draw beneath them.
    WindowCompat.setDecorFitsSystemWindows(window, false)
    ViewCompat.setOnApplyWindowInsetsListener(frame) { v, insets ->
      val bars = insets.getInsets(
        WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
      )
      v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
      insets
    }
  }

  private fun showStatus(message: String, retry: Boolean = false) {
    status.text = message
    status.visibility = View.VISIBLE
    // Only offer a retry (scan again) when the connection is not progressing.
    scanButton.visibility = if (retry) View.VISIBLE else View.GONE
  }

  // -- Scanning --------------------------------------------------------------

  private fun launchScanner() {
    try {
      IntentIntegrator(this)
        .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
        .setPrompt("Scan the pear:// key shown in LoOper")
        .setBeepEnabled(false)
        .setOrientationLocked(false)
        .initiateScan()
    } catch (e: Exception) {
      showStatus("QR scanner unavailable: ${e.message}", retry = true)
    }
  }

  override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
    val result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
    if (result == null) {
      super.onActivityResult(requestCode, resultCode, data)
      return
    }
    val contents = result.contents
    if (contents.isNullOrBlank()) {
      showStatus("Scan cancelled - tap the button to try again", retry = true)
      return
    }
    val key = extractKey(contents)
    if (key == null) {
      showStatus("That QR code is not a LoOper key:\n$contents", retry = true)
      return
    }
    savePubkey(key)
    startClient(key)
  }

  // Accept `pear://<64-hex>` or a bare 64-hex key.
  private fun extractKey(raw: String): String? {
    var s = raw.trim()
    if (s.startsWith("pear://")) s = s.substring("pear://".length)
    s = s.substringBefore('?').substringBefore('/').trim()
    return if (KEY_RE.matches(s)) s.lowercase() else null
  }

  // -- Client ----------------------------------------------------------------

  private fun startClient(pubkey: String) {
    scanButton.visibility = View.GONE
    showStatus("Connecting to host…")

    val options = Worklet.Options().memoryLimit(128 * 1024 * 1024)
    val w = Worklet(options)
    worklet = w

    // Start BEFORE creating the IPC: the Android channel comes from the
    // running worklet (creating it first throws / yields a dead channel).
    try {
      w.start("/app.bundle", assets.open("app.bundle"), arrayOf(pubkey))
    } catch (e: Exception) {
      showStatus("Failed to start client: ${e.message}")
      return
    }

    val channel = IPC(w)
    ipc = channel
    channel.readable {
      val data: ByteBuffer = channel.read() ?: return@readable
      pending.append(String(bytesOf(data), StandardCharsets.UTF_8))
      var idx = pending.indexOf("\n")
      while (idx >= 0) {
        val line = pending.substring(0, idx).trim()
        pending.delete(0, idx + 1)
        if (line.isNotEmpty()) handleLine(line)
        idx = pending.indexOf("\n")
      }
    }
  }

  private fun handleLine(line: String) {
    when {
      line.contains("\"type\":\"ready\"") -> {
        runOnUiThread {
          status.visibility = View.GONE
          scanButton.visibility = View.GONE
          // Keep the pipe alive in the background so messages — and their
          // notifications — still arrive while the UI is hidden.
          ContextCompat.startForegroundService(
            this, Intent(this, NotifyService::class.java)
          )
          // The UI is bundled in the APK, served from the secure asset origin.
          webView.loadUrl(ASSET_URL)
        }
      }
      line.contains("\"type\":\"error\"") -> {
        val msg = MSG_RE.find(line)?.groupValues?.get(1) ?: line
        runOnUiThread { showStatus("Client error:\n$msg", retry = true) }
      }
    }
  }

  private fun bytesOf(buf: ByteBuffer): ByteArray {
    val bytes = ByteArray(buf.remaining())
    buf.get(bytes)
    return bytes
  }

  /**
   * JS bridge: the bundled page calls Android.notify(title, body) for every
   * agent message or ask prompt, so the user gets a system notification without
   * having to keep the app open.  Suppressed while the UI is visible.
   */
  inner class Notifier {
    @JavascriptInterface
    fun notify(title: String?, body: String?) {
      Log.i("LoOperWeb", "[bridge] notify fg=$isForeground title=$title")
      if (isForeground) return
      NotifyService.postMessage(applicationContext, title ?: "Arrow", body ?: "")
    }

    /** True when the OS-level microphone grant is in place. */
    @JavascriptInterface
    fun micGranted(): Boolean = hasMic()

    /** Microphone diagnostics: grant, audio-settings permission, audio mode. */
    @JavascriptInterface
    fun micState(): String {
      val granted = hasMic()
      val modifyAudio = checkSelfPermission(Manifest.permission.MODIFY_AUDIO_SETTINGS) ==
        PackageManager.PERMISSION_GRANTED
      val mode = audioModeName()
      Log.i("LoOperWeb", "[selftest] mic granted=$granted modifyAudio=$modifyAudio mode=$mode")
      return "{\"granted\":$granted,\"modifyAudioSettings\":$modifyAudio," +
        "\"mode\":\"$mode\"}"
    }

    /** Take the audio path before capturing (AudioManager calls are thread safe). */
    @JavascriptInterface
    fun micBegin(mode: Int) = beginAudioCapture(mode)

    /** Give the audio path back so TTS/media routing returns to normal. */
    @JavascriptInterface
    fun micEnd() = endAudioCapture()

    /**
     * Record 500 ms with a plain AudioRecord, outside WebView.  If this gets
     * signal while the page gets zeros the fault is in WebView/Chromium; if this
     * is silent too the mic is muted, busy or has no route.  Blocks the bridge
     * thread for half a second, which is fine for a one-shot self-test.
     */
    @JavascriptInterface
    fun nativeMicProbe(): String {
      var rec: AudioRecord? = null
      return try {
        val rate = 16000
        val min = AudioRecord.getMinBufferSize(
          rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val bufSize = if (min > 0) maxOf(min, rate) else rate * 2
        rec = AudioRecord(
          MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO,
          AudioFormat.ENCODING_PCM_16BIT, bufSize
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
          Log.w("LoOperWeb", "[selftest] native probe: AudioRecord not initialized")
          return "{\"started\":false,\"error\":\"AudioRecord not initialized\"}"
        }
        rec.startRecording()
        val buf = ShortArray(rate / 2)
        var total = 0
        var peak = 0
        while (total < buf.size) {
          val n = rec.read(buf, total, buf.size - total)
          if (n <= 0) break
          total += n
        }
        rec.stop()
        for (i in 0 until total) {
          val v = if (buf[i] < 0) -buf[i].toInt() else buf[i].toInt()
          if (v > peak) peak = v
        }
        val peakF = peak / 32768.0
        Log.i("LoOperWeb", "[selftest] native probe samples=$total peak=$peakF")
        "{\"started\":true,\"samples\":$total,\"peak\":${String.format("%.4f", peakF)}}"
      } catch (e: Exception) {
        Log.w("LoOperWeb", "[selftest] native probe failed: ${e.message}")
        val msg = (e.message ?: "error").replace("\"", "'")
        "{\"started\":false,\"error\":\"$msg\"}"
      } finally {
        try { rec?.release() } catch (_: Exception) {}
      }
    }

    /**
     * Ask for the microphone grant from the page.  The answer arrives through
     * onRequestPermissionsResult, which also replays any held getUserMedia
     * request, so the page only has to re-try recording afterwards.
     */
    @JavascriptInterface
    fun requestMic() {
      runOnUiThread {
        if (hasMic()) return@runOnUiThread
        micAsked = false
        requestRuntimePermissions()
      }
    }

    /** Deep link to this app's permission screen (mic denied for good). */
    @JavascriptInterface
    fun openAppSettings() {
      runOnUiThread {
        try {
          startActivity(Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")
          ))
        } catch (e: Exception) {
          Log.w("LoOperWeb", "[perm] settings intent failed: ${e.message}")
        }
      }
    }

    /**
     * Startup self-test: the raw notification state, so the page can report a
     * precise PASS/FAIL instead of a silent "nothing happened".
     */
    @JavascriptInterface
    fun notificationState(): String {
      val nm = getSystemService(NotificationManager::class.java)
      val ch = nm?.getNotificationChannel(NotifyService.CH_MESSAGES)
      val enabled = nm?.areNotificationsEnabled() == true
      val blocked = ch?.importance == NotificationManager.IMPORTANCE_NONE
      Log.i("LoOperWeb", "[selftest] notif perm=${hasNotifPermission()} enabled=$enabled " +
        "channel=${ch != null} blocked=$blocked")
      return "{\"permission\":${hasNotifPermission()}," +
        "\"enabled\":$enabled," +
        "\"channel\":\"${NotifyService.CH_MESSAGES}\"," +
        "\"channelExists\":${ch != null}," +
        "\"channelBlocked\":$blocked}"
    }

    /** Post one real system notification; false when the OS would drop it. */
    @JavascriptInterface
    fun postTestNotification(): Boolean {
      NotifyService.ensureChannels(applicationContext)
      val nm = getSystemService(NotificationManager::class.java)
      if (!hasNotifPermission() || nm?.areNotificationsEnabled() != true) return false
      NotifyService.postMessage(
        applicationContext, "Arrow self-test", "Notifications work \u2014 test message"
      )
      Log.i("LoOperWeb", "[selftest] test notification posted")
      return true
    }
  }

  // -- Key persistence -------------------------------------------------------

  private fun loadPubkey(): String? =
    getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)

  private fun savePubkey(key: String) {
    getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, key).apply()
  }

  // -- Lifecycle -------------------------------------------------------------

  override fun onResume() {
    super.onResume()
    isForeground = true
    worklet?.resume()
    // A grant made while away (Settings, or a dialog the user dismissed) is
    // only picked up by re-reading the OS permission.
    if (!hasMic() && !micAsked) requestRuntimePermissions()
  }

  override fun onPause() {
    super.onPause()
    isForeground = false
    // Deliberately NOT suspending the worklet: the foreground service keeps the
    // pipe alive so agent messages (and notifications) still arrive in the
    // background instead of only while the app is open.
  }

  override fun onDestroy() {
    super.onDestroy()
    ipc?.close()
    ipc = null
    worklet?.terminate()
    worklet = null
  }

  private companion object {
    const val PREFS = "looper"
    const val KEY = "host_key"
    const val REQ_PERMS = 1
    const val ASSET_URL = "https://appassets.androidplatform.net/assets/web/index.html"
    val KEY_RE = Regex("^[0-9a-fA-F]{64}$")
    val PORT_RE = Regex("\"port\"\\s*:\\s*(\\d+)")
    val MSG_RE = Regex("\"msg\"\\s*:\\s*\"([^\"]*)\"")
  }
}

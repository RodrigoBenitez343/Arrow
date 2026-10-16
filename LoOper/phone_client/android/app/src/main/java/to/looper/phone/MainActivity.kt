package to.looper.phone

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
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
  // onPermissionRequest only grants the web-level microphone resource.
  private fun requestRuntimePermissions() {
    val needed = mutableListOf(Manifest.permission.RECORD_AUDIO)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
      needed.add(Manifest.permission.POST_NOTIFICATIONS)
    }
    val missing = needed.filter {
      checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
    }
    if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), REQ_PERMS)
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
          request.grant(request.resources)
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
      NotifyService.postMessage(applicationContext, title ?: "Arrow Agent", body ?: "")
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

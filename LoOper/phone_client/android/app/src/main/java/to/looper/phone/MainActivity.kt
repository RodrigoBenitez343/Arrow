package to.looper.phone

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
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
  private val pending = StringBuilder()

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    buildUi()
    ensureMicPermission()

    val pubkey = loadPubkey()
    if (pubkey != null) startClient(pubkey) else launchScanner()
  }

  // The web UI records voice via getUserMedia, so the OS-level mic permission
  // must be granted (WebView's onPermissionRequest grants the web-level one).
  private fun ensureMicPermission() {
    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
      requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
    }
  }

  // -- UI --------------------------------------------------------------------

  private fun buildUi() {
    val frame = FrameLayout(this)

    webView = WebView(this).apply {
      settings.javaScriptEnabled = true
      settings.domStorageEnabled = true
      // Let the bundled page's TTS auto-play and its mic (getUserMedia) record.
      settings.mediaPlaybackRequiresUserGesture = false
      // The UI is bundled as an asset (file://), so allow it to reach the
      // loopback pipe (ws://127.0.0.1:8081) for /ws, /stt and /tts.
      settings.allowFileAccess = true
      settings.allowFileAccessFromFileURLs = true
      settings.allowUniversalAccessFromFileURLs = true
      webViewClient = WebViewClient()
      webChromeClient = object : WebChromeClient() {
        // The UI records voice via getUserMedia; WebView denies it unless the
        // app grants the resource here (plus RECORD_AUDIO at the OS level).
        override fun onPermissionRequest(request: PermissionRequest) {
          request.grant(request.resources)
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
          // The UI is bundled in the APK; it talks to the host over the pipe.
          webView.loadUrl("file:///android_asset/web/index.html")
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

  // -- Key persistence -------------------------------------------------------

  private fun loadPubkey(): String? =
    getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)

  private fun savePubkey(key: String) {
    getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, key).apply()
  }

  // -- Lifecycle -------------------------------------------------------------

  override fun onResume() {
    super.onResume()
    worklet?.resume()
  }

  override fun onPause() {
    super.onPause()
    worklet?.suspend()
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
    const val REQ_MIC = 1
    val KEY_RE = Regex("^[0-9a-fA-F]{64}$")
    val PORT_RE = Regex("\"port\"\\s*:\\s*(\\d+)")
    val MSG_RE = Regex("\"msg\"\\s*:\\s*\"([^\"]*)\"")
  }
}

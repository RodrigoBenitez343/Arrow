package to.looper.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder

/**
 * Foreground service that keeps the LoOper link (Bare worklet + WebSocket pipe)
 * alive while the app is backgrounded, so agent messages keep arriving and each
 * one can raise a system notification instead of forcing the user to reopen the
 * client.  A P2P link has no push server, so the only way to receive a message
 * with the UI hidden is to keep this process (and therefore the pipe) running.
 *
 * It also owns the notification channels + the per-message poster, so the
 * WebView bridge and the service share one implementation.
 */
class NotifyService : Service() {

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    startForeground(FG_ID, buildForeground())
    // Restart if the OS kills the process while the user is away.
    return START_STICKY
  }

  private fun buildForeground(): Notification {
    ensureChannels(this)
    val open = PendingIntent.getActivity(
      this, 0, Intent(this, MainActivity::class.java),
      PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    return Notification.Builder(this, CH_STATUS)
      .setContentTitle("Arrow Agent")
      .setContentText("Connected — listening for messages")
      .setSmallIcon(android.R.drawable.stat_notify_chat)
      .setContentIntent(open)
      .setOngoing(true)
      .build()
  }

  companion object {
    const val CH_STATUS = "looper_status"
    const val CH_MESSAGES = "looper_messages"
    const val FG_ID = 1001
    private var nextId = 2000

    /** Create both channels (idempotent; safe to call on every post). */
    fun ensureChannels(ctx: Context) {
      val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
      nm.createNotificationChannel(
        NotificationChannel(CH_STATUS, "Connection", NotificationManager.IMPORTANCE_LOW)
          .apply { description = "Keeps the agent link alive in the background" }
      )
      nm.createNotificationChannel(
        NotificationChannel(CH_MESSAGES, "Agent messages", NotificationManager.IMPORTANCE_HIGH)
          .apply { description = "Agent replies and questions" }
      )
    }

    /** Post one notification for a single agent message or ask prompt. */
    fun postMessage(ctx: Context, title: String, body: String) {
      ensureChannels(ctx)
      val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
      val open = PendingIntent.getActivity(
        ctx, 0, Intent(ctx, MainActivity::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
      )
      val n = Notification.Builder(ctx, CH_MESSAGES)
        .setContentTitle(title)
        .setContentText(body)
        .setStyle(Notification.BigTextStyle().bigText(body))
        .setSmallIcon(android.R.drawable.stat_notify_chat)
        .setContentIntent(open)
        .setAutoCancel(true)
        .build()
      try {
        nm.notify(nextId++, n)
      } catch (_: SecurityException) {
        // POST_NOTIFICATIONS not granted — nothing to do.
      }
    }
  }
}

'use strict'

/*
 * LoOper phone client — entry point.
 *
 * Runs on Bare (inside the bare-kit worklet) or on Node.js (desktop testing).
 * Reads the host public key, starts the core pipe, and reports readiness over
 * BareKit.IPC / stdout.
 *
 * IMPORTANT: error handlers are registered BEFORE any require that can fail.
 * A worklet runs inside the host process, so an uncaught JS error aborts the
 * HOST (the whole Android app) unless a listener is attached first. A module
 * that fails to load would otherwise take the app down on connect.
 */

// Report to the native shell (IPC) and to stdout/logcat.  Self-contained so it
// still works when a dependency failed to load.
function emit (obj) {
  const line = JSON.stringify(obj) + '\n'
  try { process.stdout.write(line) } catch (_) {}
  try {
    const ipc = (typeof BareKit !== 'undefined' && BareKit.IPC) ||
                (typeof Bare !== 'undefined' && Bare.IPC)
    if (ipc && typeof ipc.write === 'function') {
      let payload = line
      try { payload = require('b4a').from(line) } catch (_) {}
      ipc.write(payload)
    }
  } catch (_) {}
}

// Never let a JS error/refusal abort the host app.
if (typeof Bare !== 'undefined') {
  Bare.on('uncaughtException', (err) => {
    emit({ type: 'error', msg: String((err && err.message) || err) })
  })
  Bare.on('unhandledRejection', (reason) => {
    emit({ type: 'error', msg: String(reason) })
  })
}

// The worklet arguments (mobile) or process args (desktop).
function argv () {
  if (typeof Bare !== 'undefined' && Bare.argv) return Bare.argv
  return process.argv.slice(2)
}

let core = null
try {
  core = require('./connect')
} catch (err) {
  // Delay so the native IPC channel exists before the host reads this.
  const msg = 'module load failed: ' + String((err && err.message) || err)
  try { setTimeout(() => emit({ type: 'error', msg }), 300) }
  catch (_) { emit({ type: 'error', msg }) }
}

if (core) {
  const hostKey = core.parseHostKey(argv())
  if (!hostKey) {
    emit({ type: 'error', msg: 'missing host key - pass the 64-char public key as an argument' })
  } else {
    let port = core.DEFAULT_PORT
    for (const a of argv()) {
      const m = /^--port=(\d+)$/.exec(a)
      if (m) port = parseInt(m[1], 10)
    }
    emit({ type: 'connecting', port })
    try {
      core.start({
        hostKey,
        port,
        onReady: ({ port }) => emit({ type: 'ready', port }),
        onError: (err) => emit({ type: 'error', msg: String((err && err.message) || err) })
      })
    } catch (err) {
      emit({ type: 'error', msg: String((err && err.message) || err) })
    }
  }
}

'use strict'

/*
 * LoOper phone client — cross-platform Pear/Bare core.
 *
 * Dials the LoOper host by its Ed25519 public key over hyperdht and exposes it
 * on a loopback TCP port, so ANY HTTP client on the device (an Android/iOS
 * WebView, or a desktop browser) can load the host's existing chat page
 * unchanged.  The host's WebSocket + HTTP responses flow through the pipe
 * verbatim — no protocol parsing happens here.
 *
 * This file is the ONE core shared by every platform shell:
 *   - desktop : `bare core/entry.js <pubkey>` then open the printed URL
 *   - Android : the same entry packed as a worklet inside the native app
 *   - iOS     : the same entry packed as a worklet (bare-kit)
 *
 * Mirror of the host sidecar (LoOper/NGUI/widgets/looper-agent-sidecar.js),
 * which byte-pipes hyperdht peers to the aiohttp server.
 */

const net = require('bare-net')
const DHT = require('hyperdht')
const b4a = require('b4a')

const LOOPBACK = '127.0.0.1'
const DEFAULT_PORT = 8081

// A hyperdht public key is 32 bytes => 64 hex chars.
const KEY_RE = /^[0-9a-fA-F]{64}$/

// The host key may arrive as a worklet argument or a desktop argv entry; scan
// the whole list so the parser does not depend on where it sits.
function parseHostKey (argv) {
  for (const arg of argv || []) {
    if (typeof arg === 'string' && KEY_RE.test(arg)) return b4a.from(arg, 'hex')
  }
  return null
}

function start ({ hostKey, port = DEFAULT_PORT, onReady, onError } = {}) {
  if (!hostKey) throw new Error('host public key is required')

  const dht = new DHT()

  // Each inbound loopback connection dials the host and is spliced to the
  // socket, so the host serves HTTP/WebSocket over the DHT stream transparently.
  const server = net.createServer((sock) => {
    const conn = dht.connect(hostKey)
    conn.on('open', () => {
      conn.pipe(sock)
      sock.pipe(conn)
    })
    // A per-connection error is normal (e.g. a WebView closing its socket).
    // Tear the pair down quietly - never surface it as a fatal app error.
    conn.on('error', () => { try { sock.destroy() } catch (_) {} })
    sock.on('error', () => { try { conn.destroy() } catch (_) {} })
  })

  // Only a failure to bind/serve the local port is fatal.
  server.on('error', (err) => { if (onError) onError(err) })

  server.listen(port, LOOPBACK, () => {
    if (onReady) onReady({ port })
  })

  return {
    dht,
    server,
    close () {
      try { server.close() } catch (_) {}
      try { dht.destroy() } catch (_) {}
    }
  }
}

module.exports = { start, parseHostKey, LOOPBACK, DEFAULT_PORT, KEY_RE }

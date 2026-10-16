'use strict'

/*
 * looper-agent-sidecar.js — Pear transport sidecar for LoOper Agent Mode.
 *
 * Replaces the Tailscale remote-access path in agent_web_server.py.  Instead of
 * publishing an IP, the host publishes an Ed25519 public key: a phone running a
 * Pear/Bare client connects by that key (hole-punched hyperdht) and speaks the
 * SAME WebSocket chat protocol straight through to the existing aiohttp /ws
 * handler.
 *
 * The sidecar is a BYTE PIPE, not a protocol translator:
 *
 *   phone ──hyperdht stream──▶ sidecar ──raw TCP──▶ 127.0.0.1:<port> (aiohttp)
 *
 * Byte-piping means the phone's WebSocket handshake and frames travel through
 * untouched, so /ws and the entire chat protocol stay exactly as they are.
 *
 * Control channel: one JSON object per line on stdout.  Logs go to stderr so
 * stdout stays parseable by the Python parent.
 *
 * Runtime: Node.js (already present with LoOper) or Bare — the module only uses
 * node-compatible APIs.  Dependencies (hyperdht, b4a) install beside this file:
 *   node looper-agent-sidecar.js --port 8081 --identity <path>
 */

const DHT = require('hyperdht')
const b4a = require('b4a')
const net = require('net')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

// ---------------------------------------------------------------------------
// Arguments
// ---------------------------------------------------------------------------
function parseArgs (argv) {
  const out = { port: 8081, identity: null }
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--port') out.port = parseInt(argv[++i], 10)
    else if (argv[i] === '--identity') out.identity = argv[++i]
  }
  return out
}

function log (level, msg) {
  process.stderr.write('[sidecar] ' + level + ': ' + msg + '\n')
}

function emit (obj) {
  process.stdout.write(JSON.stringify(obj) + '\n')
}

// ---------------------------------------------------------------------------
// Persistent identity
// ---------------------------------------------------------------------------
// The keypair IS the phone's address: keeping the same seed across restarts
// keeps the published key (and therefore the QR code) stable.
function loadOrCreateSeed (identityPath) {
  try {
    const hex = fs.readFileSync(identityPath, 'utf8').trim()
    const seed = b4a.from(hex, 'hex')
    if (seed.length === 32) return seed
    log('warn', 'identity file has wrong length, regenerating')
  } catch (err) {
    if (err.code !== 'ENOENT') log('warn', 'identity read failed: ' + err.message)
  }
  const seed = crypto.randomBytes(32)
  try {
    fs.mkdirSync(path.dirname(identityPath), { recursive: true })
    fs.writeFileSync(identityPath, b4a.toString(seed, 'hex'), { mode: 0o600 })
  } catch (err) {
    log('warn', 'identity write failed: ' + err.message)
  }
  return seed
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
const args = parseArgs(process.argv.slice(2))
if (!args.identity) {
  args.identity = path.join(
    process.env.LOCALAPPDATA || process.env.HOME || '.',
    'LoOper', 'agent-key.json'
  )
}

const seed = loadOrCreateSeed(args.identity)
const keyPair = DHT.keyPair(seed)
const dht = new DHT()

// Raw byte pipe: hyperdht stream <-> loopback TCP to the aiohttp server.
// No WebSocket parsing happens here — the phone's WS handshake and frames are
// relayed verbatim, so the existing /ws handler sees an ordinary client.
function pipeToLocal (conn) {
  const sock = net.connect(args.port, '127.0.0.1')
  conn.on('error', (err) => { log('debug', 'peer error: ' + err.message); sock.destroy() })
  sock.on('error', (err) => { log('debug', 'local error: ' + err.message); conn.destroy() })
  conn.pipe(sock)
  sock.pipe(conn)
}

// Accept-by-key: hyperdht resolves the peer from its public key (hole-punching
// NAT), so no IP is ever exposed.
const server = dht.createServer((conn) => {
  const peer = conn.remotePublicKey
  // Auth hook: reject unknown peers here (compare peer against an allow-list).
  emit({ type: 'peer_connected', pubkey: peer ? b4a.toString(peer, 'hex') : null })
  pipeToLocal(conn)
})

// NOTE: hyperswarm peer discovery would slot in HERE if the phone should find
// the host by topic rather than by explicit key.  With connect-by-key (the QR
// carries the pubkey) the swarm is not needed — hyperdht alone resolves the peer.
server.listen(keyPair).then(() => {
  emit({ type: 'ready', pubkey: b4a.toString(keyPair.publicKey, 'hex') })
  log('info', 'listening, piping to 127.0.0.1:' + args.port)
}).catch((err) => {
  emit({ type: 'error', msg: err.message })
  log('error', 'listen failed: ' + err.message)
  process.exit(1)
})

function shutdown () {
  server.close()
    .then(() => dht.destroy())
    .then(() => process.exit(0))
    .catch(() => process.exit(0))
}
process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)
process.stdin.on('end', shutdown) // parent closed -> exit
process.stdin.resume()

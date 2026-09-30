'use strict';
/**
 * server.js — the Arrow compute node.
 *
 * A standalone peer-to-peer compute-sharing node. It
 *   - joins the pool (WebSocket at /llm-network) as a compute peer, and
 *   - serves inference at POST /v1/chat/completions, where a request makes this
 *     node the ORIGIN of a pool task (decompose -> distribute -> retrieve ->
 *     verify -> assemble), then crypto-shreds the task.
 *
 * Storage is SQLite. There is no MongoDB and no platform surface (no UI, no
 * email, no tokenomics, no signup). It connects to a host app (e.g. LoOper) only
 * as an inference source.
 */
const express = require('express');
const { createServer } = require('node:http');
const cors = require('cors');
const { WebSocketServer } = require('ws');

const config = require('./config');
const logger = require('./utils/logger');
const requestLoggerMiddleware = require('./middleware/requestLogger');
const { openDatabase, closeDatabase } = require('./db/database');
const { findApiKey } = require('./db/apiKeys');
const { PoolNode } = require('./pool/node');
const { PoolEngine } = require('./pool/engine');
const { EchoBackend, OpenAIBackend } = require('./pool/backend');

// ---------------------------------------------------------------- storage
openDatabase(config.db_path);

// ----------------------------------------------------------- the pool node
const node = new PoolNode({ capacity: config.capacity });
const backend = config.backend.url
  ? new OpenAIBackend({
      baseUrl: config.backend.url,
      model: config.backend.model,
      apiKey: config.backend.key,
    })
  : new EchoBackend();
const engine = new PoolEngine(node, {
  answerFn: (atom) => backend.generate(atom),
  resultTimeout: config.result_timeout_ms,
});
node.setEngine(engine);

// ------------------------------------------------------------- http + ws
const app = express();
const server = createServer(app);
const wss = new WebSocketServer({ server, path: config.websocket_path });
node.attachSocketServer(wss); // the WS network is the pool transport

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(requestLoggerMiddleware);

function lastUser(messages) {
  if (Array.isArray(messages)) {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const message = messages[i];
      if (message && message.role === 'user') return String(message.content || '');
    }
  }
  return '';
}

function authenticate(req, res, next) {
  const header = String(req.headers.authorization || '');
  const apiKey = req.headers['x-api-key'] || (header.startsWith('Bearer ') ? header.slice(7) : null);
  if (!apiKey) return res.status(401).json({ error: 'API key required' });
  if (!findApiKey(apiKey)) return res.status(401).json({ error: 'Invalid API key' });
  return next();
}

// Inference endpoint: this node is the ORIGIN of a pool task.
app.post('/v1/chat/completions', authenticate, async (req, res) => {
  try {
    const prompt = lastUser(req.body && req.body.messages) || String((req.body && req.body.prompt) || '');
    if (!prompt.trim()) return res.status(400).json({ error: 'no prompt in request' });
    const text = await engine.complete(prompt);
    res.json({
      object: 'chat.completion',
      model: (req.body && req.body.model) || config.backend.model || 'arrow',
      choices: [{ index: 0, message: { role: 'assistant', content: text }, finish_reason: 'stop' }],
    });
  } catch (err) {
    logger.error('inference request failed', { error: err.message, stack: err.stack });
    res.status(500).json({ error: err.message });
  }
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', peer: node.peerId, peers: node.table.size, uptime: process.uptime() });
});

// ------------------------------------------------------------------- start
server.listen(config.port, config.host, async () => {
  logger.info('Arrow compute node online', { port: config.port, host: config.host, peer: node.peerId.slice(0, 8) });
  for (const bootstrap of config.bootstraps) {
    const [host, port] = bootstrap.split(':');
    try {
      await node.connect(host, Number(port));
      logger.info('joined peer', { bootstrap });
    } catch (err) {
      logger.warn('bootstrap unreachable', { bootstrap, error: err.message });
    }
  }
});

function shutdown() {
  logger.info('shutting down');
  try { node.stop(); } catch (err) { /* ignore */ }
  try { closeDatabase(); } catch (err) { /* ignore */ }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

module.exports = { app, server, node, engine };

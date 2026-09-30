'use strict';
/**
 * config/index.js — Arrow compute node configuration (env-driven, no database).
 */
require('dotenv').config();

function list(value) {
  return String(value || '').split(',').map((s) => s.trim()).filter(Boolean);
}

module.exports = {
  // Inference endpoint + WS pool transport
  port: Number(process.env.PORT || 3000),
  host: process.env.HOST || '0.0.0.0',
  websocket_path: '/llm-network',

  // SQLite (no MongoDB)
  db_path: process.env.ARROW_DB || 'arrow.db',

  // What this node contributes to the pool
  capacity: {
    cores: Number(process.env.ARROW_CORES || 0),
    ramMb: Number(process.env.ARROW_RAM_MB || 0),
    gpu: process.env.ARROW_GPU === '1' || process.env.ARROW_GPU === 'true',
    models: list(process.env.ARROW_MODELS),
  },

  // Peers to join at startup ("host:port,host:port")
  bootstraps: list(process.env.ARROW_BOOTSTRAP),

  // Local inference backend used to answer delegated atoms
  backend: {
    url: process.env.ARROW_BACKEND_URL || '',
    model: process.env.ARROW_BACKEND_MODEL || 'default',
    key: process.env.ARROW_BACKEND_KEY || '',
  },

  result_timeout_ms: Number(process.env.ARROW_RESULT_TIMEOUT_MS || 15000),
};

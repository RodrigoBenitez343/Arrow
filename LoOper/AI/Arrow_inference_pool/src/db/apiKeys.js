'use strict';
/**
 * db/apiKeys.js — provider API keys for the inference endpoint (SQLite-backed).
 */
const { getDatabase } = require('./database');

function ensureApiKey(key, label = 'default') {
  const db = getDatabase();
  db.prepare('INSERT OR IGNORE INTO api_keys (key, label, created_at) VALUES (?, ?, ?)')
    .run(key, label, Date.now() / 1000);
  return key;
}

function findApiKey(key) {
  if (!key) return null;
  const db = getDatabase();
  const row = db.prepare('SELECT key, label FROM api_keys WHERE key = ?').get(key);
  return row || null;
}

function countApiKeys() {
  const db = getDatabase();
  return db.prepare('SELECT COUNT(*) AS n FROM api_keys').get().n;
}

module.exports = { ensureApiKey, findApiKey, countApiKeys };

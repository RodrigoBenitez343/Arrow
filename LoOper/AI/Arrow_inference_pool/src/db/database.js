'use strict';
/**
 * db/database.js — the SQLite data layer (node:sqlite, no external dependency).
 *
 * Replaces MongoDB/Mongoose. Only what the standalone compute-sharing app needs
 * is stored: provider API keys (for the inference endpoint) and known peers.
 * Task state is NOT persisted here — it is transient and crypto-shredded.
 */
const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');

let _db = null;

function openDatabase(file) {
  const target = file || process.env.ARROW_DB || path.join(process.cwd(), 'arrow.db');
  const dir = path.dirname(path.resolve(target));
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch (err) {
    /* directory already exists */
  }
  const db = new DatabaseSync(target);
  db.exec('PRAGMA journal_mode=WAL;');
  db.exec('PRAGMA secure_delete=ON;');
  db.exec(`
    CREATE TABLE IF NOT EXISTS api_keys (
      key        TEXT PRIMARY KEY,
      label      TEXT,
      created_at REAL
    );
    CREATE TABLE IF NOT EXISTS peers (
      peer_id    TEXT PRIMARY KEY,
      pubkey     TEXT,
      endpoints  TEXT,
      capacity   TEXT,
      last_seen  REAL
    );
  `);
  _db = db;
  return db;
}

function getDatabase() {
  if (!_db) openDatabase();
  return _db;
}

function closeDatabase() {
  if (_db) {
    _db.close();
    _db = null;
  }
}

module.exports = { openDatabase, getDatabase, closeDatabase };

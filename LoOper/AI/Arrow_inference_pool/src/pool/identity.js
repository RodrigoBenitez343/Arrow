'use strict';
/**
 * pool/identity.js — a peer's Ed25519 identity and its signed membership record.
 *
 * A PeerRecord is the signed, self-describing advertisement a node gossips: who
 * it is, where to reach it, what compute it offers, how loaded it is. Records are
 * signed over a canonical encoding so any peer can verify a record it rebroadcasts.
 */
const crypto = require('./crypto');

class PeerIdentity {
  constructor(keys) {
    this.keys = keys || crypto.generateIdentity();
  }

  get peerId() {
    return crypto.peerIdOf(this.keys.publicDer);
  }

  get publicDer() {
    return this.keys.publicDer;
  }

  sign(message) {
    return crypto.sign(this.keys.privateKey, message);
  }
}

class PeerRecord {
  constructor(fields = {}) {
    this.peerId = fields.peerId || '';
    this.pubkey = fields.pubkey || null; // Buffer (DER)
    this.endpoints = fields.endpoints || [];
    this.capacity = fields.capacity || { cores: 0, ramMb: 0, gpu: false, models: [] };
    this.load = fields.load || 0;
    this.seq = fields.seq || 0;
    this.timestamp = fields.timestamp || 0;
    this.signature = fields.signature || null; // Buffer
  }

  canonical() {
    const body = {
      peer_id: this.peerId,
      pubkey: this.pubkey ? Buffer.from(this.pubkey).toString('hex') : '',
      endpoints: this.endpoints.slice(),
      capacity: this.capacity,
      load: Number(this.load) || 0,
      seq: Number(this.seq) || 0,
      timestamp: Number(this.timestamp) || 0,
    };
    // Deterministic key order.
    return JSON.stringify(body, Object.keys(body).sort());
  }

  sign(identity) {
    this.peerId = identity.peerId;
    this.pubkey = identity.publicDer;
    this.signature = identity.sign(this.canonical());
    return this;
  }

  verify() {
    if (!this.signature || !this.pubkey) return false;
    if (crypto.peerIdOf(this.pubkey) !== this.peerId) return false;
    return crypto.verify(this.pubkey, this.canonical(), this.signature);
  }

  supersedes(other) {
    if (!other) return true;
    return Number(this.seq) > Number(other.seq);
  }

  toJSON() {
    return {
      peer_id: this.peerId,
      pubkey: this.pubkey ? Buffer.from(this.pubkey).toString('hex') : '',
      endpoints: this.endpoints,
      capacity: this.capacity,
      load: Number(this.load),
      seq: Number(this.seq),
      timestamp: Number(this.timestamp),
      signature: this.signature ? Buffer.from(this.signature).toString('hex') : '',
    };
  }

  static fromJSON(data) {
    return new PeerRecord({
      peerId: data.peer_id || '',
      pubkey: data.pubkey ? Buffer.from(data.pubkey, 'hex') : null,
      endpoints: data.endpoints || [],
      capacity: data.capacity || {},
      load: data.load || 0,
      seq: data.seq || 0,
      timestamp: data.timestamp || 0,
      signature: data.signature ? Buffer.from(data.signature, 'hex') : null,
    });
  }
}

function idleScore(capacity) {
  const cores = Number(capacity.cores) || 0;
  const ram = (Number(capacity.ramMb) || 0) / 1024;
  const gpu = capacity.gpu ? 8 : 0;
  return cores + ram + gpu;
}

module.exports = { PeerIdentity, PeerRecord, idleScore };

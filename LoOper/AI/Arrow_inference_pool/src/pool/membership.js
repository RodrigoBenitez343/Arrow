'use strict';
/**
 * pool/membership.js — the signed, gossiped peer table (control plane).
 *
 * The ONLY eventually-consistent state in the system: knowledge is never shared
 * and task data is transient. Merge is last-write-wins by `seq`, and every
 * inbound record must verify, so a peer cannot forge another's identity/capacity.
 */
const { PeerRecord } = require('./identity');

class PeerTable {
  constructor(selfId = '') {
    this.selfId = selfId;
    this._records = new Map();
  }

  get(peerId) {
    return this._records.get(peerId);
  }

  all() {
    return [...this._records.values()];
  }

  others() {
    return this.all().filter((r) => r.peerId !== this.selfId);
  }

  get size() {
    return this._records.size;
  }

  has(peerId) {
    return this._records.has(peerId);
  }

  /** Merge one record; returns true iff the table changed. */
  merge(record) {
    if (!record || !record.peerId || record.peerId === this.selfId) return false;
    if (!record.verify()) return false;
    const current = this._records.get(record.peerId);
    if (record.supersedes(current)) {
      this._records.set(record.peerId, record);
      return true;
    }
    return false;
  }

  mergeMany(records) {
    let changed = 0;
    for (const r of records || []) if (this.merge(r)) changed += 1;
    return changed;
  }

  /** A batch of records to rebroadcast (optionally excluding ids / capped). */
  gossipBatch(exclude = [], limit = 0) {
    const skip = new Set(exclude);
    let items = this.all().filter((r) => !skip.has(r.peerId));
    if (limit && items.length > limit) {
      items = items.sort(() => Math.random() - 0.5).slice(0, limit);
    }
    return items;
  }
}

module.exports = { PeerTable, PeerRecord };

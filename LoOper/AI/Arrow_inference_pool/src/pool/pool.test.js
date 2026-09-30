'use strict';
/**
 * pool/pool.test.js — foundation tests for the compute-sharing pool.
 * Run: node --test src/pool
 */
const test = require('node:test');
const assert = require('node:assert');

const { PeerIdentity, PeerRecord, idleScore } = require('./identity');
const { PeerTable } = require('./membership');
const { Scheduler } = require('./scheduler');
const { TaskCipher } = require('./crypto');

test('identity: record signs and verifies; tamper is detected', () => {
  const id = new PeerIdentity();
  const rec = new PeerRecord({ capacity: { cores: 4 }, load: 0.1, seq: 1 }).sign(id);
  assert.strictEqual(rec.verify(), true);
  rec.load = 99; // mutate after signing
  assert.strictEqual(rec.verify(), false);
});

test('identity: wrong peer id / pubkey mismatch fails', () => {
  const id = new PeerIdentity();
  const rec = new PeerRecord({ seq: 1 }).sign(id);
  rec.peerId = 'deadbeef';
  assert.strictEqual(rec.verify(), false);
});

test('record: JSON round-trip verifies', () => {
  const id = new PeerIdentity();
  const rec = new PeerRecord({ capacity: { cores: 2, models: ['m'] }, seq: 7 }).sign(id);
  const clone = PeerRecord.fromJSON(rec.toJSON());
  assert.strictEqual(clone.verify(), true);
  assert.deepStrictEqual(clone.capacity.models, ['m']);
});

test('membership: LWW by seq; rejects self and forged', () => {
  const a = new PeerIdentity();
  const table = new PeerTable('self');
  assert.strictEqual(table.merge(new PeerRecord({ seq: 1, load: 0.1 }).sign(a)), true);
  assert.strictEqual(table.merge(new PeerRecord({ seq: 2, load: 0.5 }).sign(a)), true);
  assert.strictEqual(table.get(a.peerId).load, 0.5);
  // older seq ignored
  assert.strictEqual(table.merge(new PeerRecord({ seq: 1, load: 0.9 }).sign(a)), false);

  // own record rejected
  const self = new PeerIdentity();
  const selfTable = new PeerTable(self.peerId);
  assert.strictEqual(selfTable.merge(new PeerRecord({ seq: 1 }).sign(self)), false);

  // forged: sign with a, claim b's identity
  const b = new PeerIdentity();
  const forged = new PeerRecord({ seq: 1 }).sign(a);
  forged.peerId = b.peerId;
  forged.pubkey = b.publicDer;
  assert.strictEqual(new PeerTable().merge(forged), false);
});

test('scheduler: spreads atoms and prefers nearer peer first', () => {
  const sched = new Scheduler();
  const cands = [
    { peerId: 'near', capacityScore: 1, rtt: 0.01 },
    { peerId: 'far', capacityScore: 1, rtt: 0.5 },
  ];
  const plan = sched.plan(['a', 'b'], cands);
  assert.strictEqual(plan.length, 2);
  assert.strictEqual(plan[0][1], 'near');
});

test('scheduler: empty inputs', () => {
  const sched = new Scheduler();
  assert.deepStrictEqual(sched.plan([], [{ peerId: 'p', capacityScore: 1 }]), []);
  assert.deepStrictEqual(sched.plan(['a'], []), []);
});

test('crypto: encrypt/decrypt then shred is irreversible', () => {
  const cipher = TaskCipher.generate();
  const env = cipher.encrypt(Buffer.from('secret atom'));
  assert.strictEqual(cipher.decrypt(env).toString(), 'secret atom');
  cipher.shred();
  assert.strictEqual(cipher.shredded, true);
  assert.throws(() => cipher.decrypt(env));
  assert.throws(() => cipher.encrypt(Buffer.from('x')));
});

test('idleScore reflects cores/ram/gpu', () => {
  assert.ok(idleScore({ cores: 8, ramMb: 2048 }) > idleScore({ cores: 2, ramMb: 512 }));
});

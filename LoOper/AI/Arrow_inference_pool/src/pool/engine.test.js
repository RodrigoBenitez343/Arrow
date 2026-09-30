'use strict';
/**
 * pool/engine.test.js — end-to-end pool test over real WS links.
 * Run: node --test src/pool/engine.test.js
 */
const test = require('node:test');
const assert = require('node:assert');

const { PoolNode } = require('./node');
const { PoolEngine } = require('./engine');

function waitFor(predicate, timeout = 3000) {
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (predicate() || Date.now() - started > timeout) {
        clearInterval(timer);
        resolve(predicate());
      }
    }, 20);
  });
}

test('pool: request is divided across peers, assembled, then erased', async () => {
  const origin = new PoolNode({ capacity: { cores: 1 }, heartbeatInterval: 50, gossipInterval: 50 });
  const w1 = new PoolNode({ capacity: { cores: 8 }, heartbeatInterval: 50, gossipInterval: 50 });
  const w2 = new PoolNode({ capacity: { cores: 8 }, heartbeatInterval: 50, gossipInterval: 50 });
  await origin.start();
  await w1.start();
  await w2.start();

  const engine = new PoolEngine(origin, { answerFn: (a) => `local(${a})` });
  const e1 = new PoolEngine(w1, { answerFn: (a) => `W1[${a}]` });
  const e2 = new PoolEngine(w2, { answerFn: (a) => `W2[${a}]` });
  origin.setEngine(engine);
  w1.setEngine(e1);
  w2.setEngine(e2);

  try {
    await origin.connect(w1.host, w1.port);
    await origin.connect(w2.host, w2.port);
    assert.strictEqual(await waitFor(() => origin.peers().length >= 2), true);

    const out = await engine.complete('do alpha and beta and gamma delta');
    assert.ok(out && out.trim().length, 'empty response');
    assert.ok(out.includes('W1[') || out.includes('W2['), 'no peer contributed');

    assert.strictEqual(await waitFor(() => e1.store.taskIds().length === 0 && e2.store.taskIds().length === 0), true);
    assert.strictEqual(e1.store.taskIds().length, 0);
    assert.strictEqual(e2.store.taskIds().length, 0);
  } finally {
    origin.stop();
    w1.stop();
    w2.stop();
  }
});

test('pool: single node falls back to a local answer', async () => {
  const solo = new PoolNode({ capacity: { cores: 1 }, heartbeatInterval: 50, gossipInterval: 50 });
  await solo.start();
  const engine = new PoolEngine(solo, { answerFn: (a) => `solo(${a})` });
  solo.setEngine(engine);
  try {
    const out = await engine.complete('just one action');
    assert.ok(out.includes('solo('));
  } finally {
    solo.stop();
  }
});

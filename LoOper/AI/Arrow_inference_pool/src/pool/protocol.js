'use strict';
/**
 * pool/protocol.js — the wire protocol for the compute-sharing pool.
 *
 * One JSON object per line. Control plane: HELLO/HELLO_ACK, HEARTBEAT,
 * PING/PONG, PEER_GOSSIP. Data plane: TASK_ASSIGN, TASK_RESULT, TASK_CLOSE.
 */
const { randomUUID } = require('node:crypto');

const PROTOCOL_VERSION = 1;

const MessageType = Object.freeze({
  HELLO: 'HELLO',
  HELLO_ACK: 'HELLO_ACK',
  HEARTBEAT: 'HEARTBEAT',
  PING: 'PING',
  PONG: 'PONG',
  PEER_GOSSIP: 'PEER_GOSSIP',
  TASK_ASSIGN: 'TASK_ASSIGN',
  TASK_RESULT: 'TASK_RESULT',
  TASK_CLOSE: 'TASK_CLOSE',
  ERROR: 'ERROR',
});

function make(type, sender = '', payload = {}) {
  return {
    type,
    version: PROTOCOL_VERSION,
    msg_id: randomUUID(),
    sender,
    ts: Date.now() / 1000,
    payload,
  };
}

function encode(message) {
  return JSON.stringify(message) + '\n';
}

function decode(raw) {
  const text = Buffer.isBuffer(raw) ? raw.toString('utf8') : String(raw);
  return JSON.parse(text.trim());
}

function isCompatible(message) {
  return Number(message && message.version) === PROTOCOL_VERSION;
}

module.exports = { PROTOCOL_VERSION, MessageType, make, encode, decode, isCompatible };

'use strict';
/**
 * pool/node.js — PoolNode: identity + transport + membership for the pool.
 *
 * The control plane in one object: opens WS links, performs the signed
 * handshake, sends heartbeats carrying capacity+load, measures RTT, and gossips
 * the peer table. Data-plane messages (TASK_*) are handed to the attached engine.
 */
const { PeerIdentity, PeerRecord } = require('./identity');
const { PeerTable } = require('./membership');
const { PoolLink, WebSocket, WebSocketServer } = require('./transport');
const { MessageType, make, isCompatible } = require('./protocol');

class PoolNode {
  constructor({
    identity, capacity, host = '127.0.0.1', port = 0,
    heartbeatInterval = 5000, gossipInterval = 5000, gossipFanout = 3,
  } = {}) {
    this.identity = identity || new PeerIdentity();
    this.capacity = capacity || { cores: 0, ramMb: 0, gpu: false, models: [] };
    this.host = host;
    this.port = port;
    this.table = new PeerTable(this.identity.peerId);
    this.links = new Map(); // peerId -> PoolLink
    this._seq = 0;
    this._load = 0;
    this._hb = heartbeatInterval;
    this._gossipInterval = gossipInterval;
    this._fanout = gossipFanout;
    this._engine = null;
    this._timers = [];
    this._endpoints = [];
    this._wss = null;
  }

  get peerId() {
    return this.identity.peerId;
  }

  get address() {
    return `${this.host}:${this.port}`;
  }

  peers() {
    return this.table.others();
  }

  setLoad(load) {
    this._load = Math.max(0, Number(load) || 0);
  }

  setEngine(engine) {
    this._engine = engine;
  }

  rttByPeer() {
    const map = new Map();
    for (const [peerId, link] of this.links) map.set(peerId, link.rtt);
    return map;
  }

  buildRecord() {
    this._seq += 1;
    return new PeerRecord({
      endpoints: this._endpoints.slice(),
      capacity: this.capacity,
      load: this._load,
      seq: this._seq,
      timestamp: Date.now() / 1000,
    }).sign(this.identity);
  }

  sendTo(peerId, message) {
    const link = this.links.get(peerId);
    if (link) link.send(message);
    return Boolean(link);
  }

  start() {
    return new Promise((resolve) => {
      this._wss = new WebSocketServer({ host: this.host, port: this.port });
      this._wss.on('connection', (socket) => this._accept(socket));
      this._wss.on('listening', () => {
        const addr = this._wss.address();
        this.host = addr.address;
        this.port = addr.port;
        if (!this._endpoints.includes(this.address)) this._endpoints.push(this.address);
        this._startTimers();
        resolve(this.address);
      });
    });
  }

  /** Attach the pool to an existing 'ws' server (the app's network layer). */
  attachSocketServer(wss) {
    this._wssExternal = wss;
    wss.on('connection', (socket) => this._accept(socket));
    this._startTimers();
  }

  _startTimers() {
    if (this._timers.length) return;
    this._timers.push(setInterval(() => this._heartbeat(), this._hb));
    this._timers.push(setInterval(() => this._gossip(), this._gossipInterval));
  }

  connect(host, port) {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(`ws://${host}:${port}`);
      const link = new PoolLink(socket, {
        onMessage: (msg, l) => this._dispatch(msg, l),
        onClose: (l) => this._drop(l),
      });
      socket.on('open', () => {
        socket.send(JSON.stringify(make(MessageType.HELLO, this.peerId, { record: this.buildRecord().toJSON() })));
        resolve(link);
      });
      socket.on('error', reject);
    });
  }

  stop() {
    for (const timer of this._timers) clearInterval(timer);
    this._timers = [];
    for (const link of this.links.values()) link.close();
    this.links.clear();
    if (this._wss) {
      try {
        this._wss.close();
      } catch (err) {
        /* already closed */
      }
      this._wss = null;
    }
  }

  // ------------------------------------------------------------- internals
  _accept(socket) {
    const link = new PoolLink(socket, {
      onMessage: (msg, l) => this._dispatch(msg, l),
      onClose: (l) => this._drop(l),
    });
    return link;
  }

  _register(link) {
    const existing = this.links.get(link.remoteId);
    if (existing && existing !== link) existing.close();
    this.links.set(link.remoteId, link);
  }

  _drop(link) {
    if (link.remoteId && this.links.get(link.remoteId) === link) {
      this.links.delete(link.remoteId);
    }
  }

  _dispatch(msg, link) {
    const payload = msg.payload || {};
    if (msg.type === MessageType.HELLO) {
      const record = PeerRecord.fromJSON(payload.record || {});
      if (!record.verify()) {
        link.close();
        return;
      }
      link.remoteId = record.peerId;
      this._register(link);
      this.table.merge(record);
      link.send(make(MessageType.HELLO_ACK, this.peerId, { record: this.buildRecord().toJSON() }));
      return;
    }
    if (msg.type === MessageType.HELLO_ACK) {
      const record = PeerRecord.fromJSON(payload.record || {});
      if (!record.verify()) {
        link.close();
        return;
      }
      link.remoteId = record.peerId;
      this._register(link);
      this.table.merge(record);
      return;
    }
    if (!link.remoteId || !isCompatible(msg)) return;

    switch (msg.type) {
      case MessageType.HEARTBEAT: {
        const record = PeerRecord.fromJSON(payload.record || {});
        if (record.verify()) this.table.merge(record);
        break;
      }
      case MessageType.PING:
        link.send(make(MessageType.PONG, this.peerId, { reply_to: msg.msg_id }));
        break;
      case MessageType.PONG:
        link.notePong(payload.reply_to);
        break;
      case MessageType.PEER_GOSSIP: {
        const records = (payload.records || []).map((r) => PeerRecord.fromJSON(r));
        this.table.mergeMany(records);
        break;
      }
      case MessageType.TASK_ASSIGN:
      case MessageType.TASK_RESULT:
      case MessageType.TASK_CLOSE:
        if (this._engine) Promise.resolve(this._engine.onMessage(msg)).catch(() => {});
        break;
      default:
        break;
    }
  }

  _heartbeat() {
    const record = this.buildRecord();
    for (const link of this.links.values()) {
      link.send(make(MessageType.HEARTBEAT, this.peerId, { record: record.toJSON() }));
      const ping = make(MessageType.PING, this.peerId);
      link.ping(ping.msg_id);
      link.send(ping);
    }
  }

  _gossip() {
    const links = [...this.links.values()];
    if (!links.length) return;
    const targets = links.length <= this._fanout
      ? links
      : links.sort(() => Math.random() - 0.5).slice(0, this._fanout);
    const records = [this.buildRecord(), ...this.table.gossipBatch([], 20)];
    const payload = records.map((r) => r.toJSON());
    for (const link of targets) {
      link.send(make(MessageType.PEER_GOSSIP, this.peerId, { records: payload }));
    }
  }
}

module.exports = { PoolNode };

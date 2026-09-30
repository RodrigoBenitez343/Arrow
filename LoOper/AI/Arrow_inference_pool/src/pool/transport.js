'use strict';
/**
 * pool/transport.js — a WebSocket pool link.
 *
 * JSON-lines over a WS socket (the app's existing network layer). Outbound
 * dialing is NAT-friendly. The node owns the control-plane logic; this class
 * just frames messages and tracks RTT pings.
 */
const { WebSocket, WebSocketServer } = require('ws');

class PoolLink {
  constructor(socket, { onMessage, onClose } = {}) {
    this.socket = socket;
    this.remoteId = null;
    this.rtt = null;
    this.lastSeen = Date.now();
    this._pings = new Map();
    this._onMessage = onMessage;
    this._onClose = onClose;

    socket.on('message', (data) => {
      let msg;
      try {
        msg = JSON.parse(data.toString('utf8').trim());
      } catch (err) {
        return;
      }
      this.lastSeen = Date.now();
      if (this._onMessage) this._onMessage(msg, this);
    });
    socket.on('close', () => this._onClose && this._onClose(this));
    socket.on('error', () => {});
  }

  get ready() {
    return this.socket.readyState === WebSocket.OPEN;
  }

  send(message) {
    if (!this.ready) return;
    try {
      this.socket.send(JSON.stringify(message));
    } catch (err) {
      /* socket gone */
    }
  }

  ping(messageId) {
    this._pings.set(messageId, Date.now());
  }

  notePong(replyTo) {
    const sent = this._pings.get(replyTo);
    if (sent) {
      this.rtt = (Date.now() - sent) / 1000;
      this._pings.delete(replyTo);
    }
  }

  close() {
    try {
      this.socket.close();
    } catch (err) {
      /* already closed */
    }
  }
}

module.exports = { PoolLink, WebSocket, WebSocketServer };

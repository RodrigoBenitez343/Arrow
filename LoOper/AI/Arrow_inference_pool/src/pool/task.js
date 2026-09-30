'use strict';
/**
 * pool/task.js — task sessions, the delegation tree, and the erase step.
 *
 * One TaskSession is one assigned piece's transient state on one node: the
 * per-task key, the parent it returns to, the children it delegated to, its own
 * kept-piece answer, and the results collected from children. TaskManager
 * enforces the ephemeral contract: closing a task clears its scratch and shreds
 * every per-task key (the erase step).
 */

class MemoryTaskStore {
  constructor() {
    this._data = new Map();
  }

  put(taskId, key, value) {
    if (!this._data.has(taskId)) this._data.set(taskId, new Map());
    this._data.get(taskId).set(key, value);
  }

  get(taskId, key) {
    const scope = this._data.get(taskId);
    return scope ? scope.get(key) : undefined;
  }

  entries(taskId) {
    const scope = this._data.get(taskId);
    return scope ? [...scope.entries()] : [];
  }

  clear(taskId) {
    const scope = this._data.get(taskId);
    const count = scope ? scope.size : 0;
    this._data.delete(taskId);
    return count;
  }

  close() {
    this._data.clear();
  }

  taskIds() {
    return [...this._data.keys()];
  }
}

class TaskSession {
  constructor({ taskId, assignedId, role, cipher, parent = null }) {
    this.taskId = taskId;
    this.assignedId = assignedId;
    this.role = role; // 'origin' | 'delegate'
    this.cipher = cipher;
    this.parent = parent;
    this.children = [];
    this.results = new Map(); // chunkId -> text
    this.accepted = new Map(); // chunkId -> bool
    this.childPieces = new Map(); // chunkId -> piece
    this.ownResult = '';
    this.expects = 0;
  }

  addChild(peerId) {
    if (peerId && !this.children.includes(peerId)) this.children.push(peerId);
  }

  setChildPiece(chunkId, piece) {
    this.childPieces.set(chunkId, piece);
  }

  addResult(chunkId, text, accepted = true) {
    this.results.set(chunkId, text);
    this.accepted.set(chunkId, accepted);
  }

  complete() {
    return this.expects > 0 && this.results.size >= this.expects;
  }

  /** Accepted child results, in order; falls back to all if everything was rejected. */
  orderedResults() {
    const ids = [...this.results.keys()].sort();
    let accepted = ids.filter((id) => this.accepted.get(id) !== false).map((id) => this.results.get(id));
    if (!accepted.length && ids.length) accepted = ids.map((id) => this.results.get(id));
    return accepted;
  }

  aggregate() {
    return [this.ownResult, ...this.orderedResults()].filter(Boolean).join('\n');
  }

  shred() {
    this.cipher.shred();
  }
}

class TaskManager {
  constructor(store) {
    this.store = store;
    this._sessions = new Map(); // assignedId -> TaskSession
  }

  create(taskId, assignedId, role, cipher, parent = null) {
    const session = new TaskSession({ taskId, assignedId, role, cipher, parent });
    this._sessions.set(assignedId, session);
    return session;
  }

  get(assignedId) {
    return this._sessions.get(assignedId);
  }

  sessions() {
    return [...this._sessions.values()];
  }

  /** Erase a task: clear its scratch, shred every session key, drop the sessions. */
  close(taskId) {
    const cleared = this.store.clear(taskId);
    for (const [assignedId, session] of [...this._sessions.entries()]) {
      if (session.taskId === taskId) {
        session.shred();
        this._sessions.delete(assignedId);
      }
    }
    return cleared;
  }

  closeAll() {
    for (const taskId of new Set(this.sessions().map((s) => s.taskId))) this.close(taskId);
  }
}

module.exports = { MemoryTaskStore, TaskSession, TaskManager };

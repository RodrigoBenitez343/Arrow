'use strict';
/**
 * pool/engine.js — the pool facade: the compute-sharing protocol.
 *
 * A request becomes a task: it is decomposed into atomic directives, each is
 * assigned to a nearby idle peer, a peer that cannot fit its part self-divides
 * (keeps one manageable piece, delegates the rest), results return up the
 * delegation tree, are verified, and are assembled on the origin. TASK_CLOSE
 * then crypto-shreds every scratch. This is what a node runs as an origin (to
 * serve an inference request) and as a peer (to contribute compute).
 */
const { randomUUID } = require('node:crypto');
const { TaskCipher } = require('./crypto');
const { TaskManager, MemoryTaskStore } = require('./task');
const { HeuristicCoordinator } = require('./coordinator');
const { Scheduler } = require('./scheduler');
const { MessageType, make } = require('./protocol');
const { idleScore } = require('./identity');

const SEP = '/';

function defaultAnswer(atom) {
  return String(atom).trim();
}

class PoolEngine {
  constructor(node, {
    coordinator, store, scheduler, answerFn,
    resultTimeout = 15000, maxManageable = 1e9, maxDepth = 8,
    maxPieces = 64, verifyResults = true,
  } = {}) {
    this.node = node;
    this.coordinator = coordinator || new HeuristicCoordinator();
    this.store = store || new MemoryTaskStore();
    this.tasks = new TaskManager(this.store);
    this.scheduler = scheduler || new Scheduler();
    this.answerFn = answerFn || defaultAnswer;
    this.resultTimeout = resultTimeout;
    this.maxManageable = maxManageable;
    this.maxDepth = maxDepth;
    this.maxPieces = maxPieces;
    this.verifyResults = verifyResults;
    this._waiters = new Map();
    this.stats = { assigns: 0, delegations: 0, answers: 0, maxDepth: 0, verified: 0, rejected: 0, unverified: 0 };
  }

  // ------------------------------------------------------------- decisions
  canHandle(atom) {
    return String(atom).length <= this.maxManageable;
  }

  shouldDivide(atom) {
    let verdict = null;
    try {
      verdict = this.coordinator.manageable(atom);
    } catch (err) {
      verdict = null;
    }
    if (verdict === null || verdict === undefined) return !this.canHandle(atom);
    return !verdict;
  }

  chooseKeep(parts, state) {
    let picked = null;
    try {
      picked = this.coordinator.pick(parts, state);
    } catch (err) {
      picked = null;
    }
    return picked && parts.includes(picked) ? picked : parts[0];
  }

  split(atom) {
    const parts = (this.coordinator.decompose(atom) || []).map((s) => String(s).trim()).filter(Boolean);
    if (parts.length >= 2) return parts;
    const words = String(atom).split(/\s+/).filter(Boolean);
    if (words.length >= 2) {
      const mid = Math.floor(words.length / 2);
      return [words.slice(0, mid).join(' '), words.slice(mid).join(' ')];
    }
    return [atom];
  }

  candidates(exclude = new Set()) {
    const rtt = this.node.rttByPeer();
    return this.node.peers()
      .filter((r) => !exclude.has(r.peerId))
      .map((r) => ({
        peerId: r.peerId,
        capacityScore: Math.max(1, idleScore(r.capacity || {})),
        load: r.load || 0,
        rtt: rtt.has(r.peerId) ? rtt.get(r.peerId) : null,
        models: (r.capacity && r.capacity.models) || [],
      }));
  }

  divide(atom, path, parent) {
    const exclude = new Set([...path, parent, this.node.peerId]);
    const cands = this.candidates(exclude);
    if (!cands.length) return [atom, []];
    let remaining = atom;
    const pieces = [];
    while (this.shouldDivide(remaining) && pieces.length < this.maxPieces) {
      const parts = this.split(remaining);
      if (parts.length < 2) break;
      const keep = this.chooseKeep(parts, remaining);
      const idx = parts.indexOf(keep);
      pieces.push(...parts.slice(0, idx), ...parts.slice(idx + 1));
      remaining = keep;
    }
    if (!pieces.length) return [remaining, []];
    return [remaining, this.scheduler.plan(pieces, cands)];
  }

  // ---------------------------------------------------------------- origin
  async complete(prompt) {
    const atoms = this.coordinator.decompose(prompt);
    if (!atoms.length) return '';
    const plan = this.scheduler.plan(atoms, this.candidates());
    if (!plan.length) return this.answerFn(prompt); // no peers -> answer locally

    const taskId = randomUUID().replace(/-/g, '');
    const cipher = TaskCipher.generate();
    const session = this.tasks.create(taskId, taskId, 'origin', cipher);
    session.expects = plan.length;
    const done = new Promise((resolve) => this._waiters.set(taskId, resolve));

    plan.forEach(([atom, peerId], index) => {
      session.addChild(peerId);
      const chunkId = `${taskId}${SEP}${String(index).padStart(4, '0')}`;
      session.setChildPiece(chunkId, atom);
      this.node.sendTo(peerId, make(MessageType.TASK_ASSIGN, this.node.peerId, {
        task_id: taskId, chunk_id: chunkId, parent: this.node.peerId,
        atom, key: cipher.exportKey(), path: [this.node.peerId], depth: 0,
      }));
    });

    await Promise.race([done, new Promise((r) => setTimeout(r, this.resultTimeout))]);
    const final = this._assemble(session.orderedResults());
    this._closeTask(taskId, session);
    return final;
  }

  _assemble(chunks) {
    const ordered = (chunks || []).map((c) => String(c).trim()).filter(Boolean);
    if (!ordered.length) return '';
    return ordered.join('\n');
  }

  _closeTask(taskId, session) {
    for (const child of session.children) {
      this.node.sendTo(child, make(MessageType.TASK_CLOSE, this.node.peerId, { task_id: taskId }));
    }
    this.tasks.close(taskId);
    this._waiters.delete(taskId);
  }

  // -------------------------------------------------------------- handlers
  async onMessage(msg) {
    const payload = msg.payload || {};
    if (msg.type === MessageType.TASK_ASSIGN) await this._onAssign(payload);
    else if (msg.type === MessageType.TASK_RESULT) this._onResult(payload);
    else if (msg.type === MessageType.TASK_CLOSE) this._onClose(payload);
  }

  _acceptResult(piece, text) {
    if (!this.verifyResults || !piece || !text) return true;
    let verdict = null;
    try {
      verdict = this.coordinator.verify(text, piece);
    } catch (err) {
      verdict = null;
    }
    if (verdict === false) {
      this.stats.rejected += 1;
      return false;
    }
    if (verdict === null || verdict === undefined) this.stats.unverified += 1;
    else this.stats.verified += 1;
    return true;
  }

  _sendResult(parent, taskId, chunkId, text, cipher) {
    this.stats.answers += 1;
    const envelope = cipher.encrypt(Buffer.from(String(text))).toString('hex');
    this.node.sendTo(parent, make(MessageType.TASK_RESULT, this.node.peerId, {
      task_id: taskId, chunk_id: chunkId, ciphertext: envelope,
    }));
  }

  async _onAssign(p) {
    const { task_id: taskId, chunk_id: chunkId, parent, atom = '', key, path = [], depth = 0 } = p;
    if (!(taskId && chunkId && parent && key)) return;
    this.stats.assigns += 1;
    this.stats.maxDepth = Math.max(this.stats.maxDepth, depth);
    const cipher = new TaskCipher(Buffer.from(key, 'hex'));
    const session = this.tasks.get(chunkId) || this.tasks.create(taskId, chunkId, 'delegate', cipher, parent);
    this.store.put(taskId, `atom:${chunkId}`, atom);

    if (!this.shouldDivide(atom) || depth >= this.maxDepth) {
      const answer = await Promise.resolve(this.answerFn(atom));
      this.store.put(taskId, `result:${chunkId}`, answer);
      this._sendResult(parent, taskId, chunkId, answer, cipher);
      return;
    }

    const [kept, assignments] = this.divide(atom, path, parent);
    if (!assignments.length) {
      const answer = await Promise.resolve(this.answerFn(atom));
      this.store.put(taskId, `result:${chunkId}`, answer);
      this._sendResult(parent, taskId, chunkId, answer, cipher);
      return;
    }

    this.stats.delegations += 1;
    session.ownResult = await Promise.resolve(this.answerFn(kept));
    this.store.put(taskId, `own:${chunkId}`, session.ownResult);
    session.expects += assignments.length;
    assignments.forEach(([piece, peerId], index) => {
      const childId = `${chunkId}${SEP}${String(index).padStart(2, '0')}`;
      session.addChild(peerId);
      session.setChildPiece(childId, piece);
      this.node.sendTo(peerId, make(MessageType.TASK_ASSIGN, this.node.peerId, {
        task_id: taskId, chunk_id: childId, parent: this.node.peerId,
        atom: piece, key, path: [...path, this.node.peerId], depth: depth + 1,
      }));
    });
  }

  _onResult(p) {
    const taskId = p.task_id;
    const chunkId = p.chunk_id;
    const parentId = chunkId.includes(SEP) ? chunkId.slice(0, chunkId.lastIndexOf(SEP)) : taskId;
    const session = this.tasks.get(parentId);
    if (!session) return;
    let text = '';
    try {
      text = session.cipher.decrypt(Buffer.from(p.ciphertext || '', 'hex')).toString('utf8');
    } catch (err) {
      text = '';
    }
    const accepted = this._acceptResult(session.childPieces.get(chunkId), text);
    session.addResult(chunkId, text, accepted);
    if (!session.complete()) return;
    if (session.role === 'origin') {
      const resolve = this._waiters.get(taskId);
      if (resolve) resolve();
    } else {
      this._sendResult(session.parent, session.taskId, session.assignedId, session.aggregate(), session.cipher);
    }
  }

  _onClose(p) {
    const taskId = p.task_id;
    if (!taskId) return;
    const children = [];
    const origin = this.tasks.get(taskId);
    if (origin) children.push(...origin.children);
    for (const session of this.tasks.sessions()) {
      if (session.taskId === taskId) children.push(...session.children);
    }
    for (const child of new Set(children)) {
      this.node.sendTo(child, make(MessageType.TASK_CLOSE, this.node.peerId, { task_id: taskId }));
    }
    this.tasks.close(taskId);
    this._waiters.delete(taskId);
  }
}

module.exports = { PoolEngine, defaultAnswer };

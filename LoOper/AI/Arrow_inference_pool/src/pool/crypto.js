'use strict';
/**
 * pool/crypto.js — Ed25519 identity keys + per-task AES-256-GCM, with crypto-shred.
 *
 * No external dependency: everything is `node:crypto`.
 *  - Identity : Ed25519 keypair; peer_id = sha256(public DER)[:40].
 *  - Tasks    : a fresh AES-256-GCM key per task; shred() zeroes the key so any
 *               ciphertext that lingers is computationally unrecoverable.
 */
const crypto = require('node:crypto');

function generateIdentity() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  return {
    publicKey,
    privateKey,
    publicDer: publicKey.export({ type: 'spki', format: 'der' }),
  };
}

function peerIdOf(publicDer) {
  return crypto.createHash('sha256').update(publicDer).digest('hex').slice(0, 40);
}

function sign(privateKey, message) {
  return crypto.sign(null, Buffer.from(message), privateKey);
}

function verify(publicDer, message, signature) {
  try {
    const key = crypto.createPublicKey({ key: Buffer.from(publicDer), format: 'der', type: 'spki' });
    return crypto.verify(null, Buffer.from(message), key, Buffer.from(signature));
  } catch (err) {
    return false;
  }
}

class TaskCipher {
  constructor(key) {
    this._key = Buffer.from(key);
    this._shredded = false;
  }

  static generate() {
    return new TaskCipher(crypto.randomBytes(32));
  }

  /** Encrypt -> iv(12) || tag(16) || ciphertext. */
  encrypt(plaintext) {
    this._requireLive();
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', this._key, iv);
    const body = Buffer.concat([cipher.update(Buffer.from(plaintext)), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), body]);
  }

  decrypt(envelope) {
    this._requireLive();
    const buf = Buffer.from(envelope);
    const iv = buf.subarray(0, 12);
    const tag = buf.subarray(12, 28);
    const body = buf.subarray(28);
    const decipher = crypto.createDecipheriv('aes-256-gcm', this._key, iv);
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(body), decipher.final()]);
  }

  /** Destroy the key material (crypto-shred). Idempotent, irreversible. */
  shred() {
    this._key.fill(0);
    this._shredded = true;
  }

  get shredded() {
    return this._shredded;
  }

  /** Raw key as hex, for sharing with a delegate (live key only). */
  exportKey() {
    this._requireLive();
    return this._key.toString('hex');
  }

  _requireLive() {
    if (this._shredded) throw new Error('task key has been shredded');
  }
}

module.exports = { generateIdentity, peerIdOf, sign, verify, TaskCipher };

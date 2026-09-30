'use strict';
/**
 * pool/backend.js — how a node answers atoms from its OWN local knowledge.
 *
 * A pool node is a compute contributor: when a peer delegates an atomic part to
 * it, it answers that part with its LOCAL model. Only the result travels back up
 * the pool; the node's knowledge never leaves.
 *
 *   EchoBackend    — deterministic, model-free default (tests / offline demos).
 *   OpenAIBackend  — calls any OpenAI-compatible model server (local llama.cpp,
 *                    Ollama, or a LoOper inference endpoint).
 */

class EchoBackend {
  async generate(atom) {
    return String(atom).trim();
  }
}

class OpenAIBackend {
  constructor({ baseUrl, model, apiKey = null, timeoutMs = 120000, system = '' } = {}) {
    this.baseUrl = String(baseUrl || '').replace(/\/$/, '');
    this.model = model || 'default';
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
    this.system = system;
  }

  async generate(atom) {
    const messages = [];
    if (this.system) messages.push({ role: 'system', content: this.system });
    messages.push({ role: 'user', content: String(atom) });
    const headers = { 'Content-Type': 'application/json' };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    const res = await fetch(`${this.baseUrl}/v1/chat/completions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ model: this.model, messages }),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    if (!res.ok) throw new Error(`backend ${res.status}`);
    const data = await res.json();
    return String((data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) || '');
  }
}

module.exports = { EchoBackend, OpenAIBackend };

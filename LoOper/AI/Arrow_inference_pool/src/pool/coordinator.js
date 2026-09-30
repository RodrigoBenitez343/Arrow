'use strict';
/**
 * pool/coordinator.js — the default division/verification decisions.
 *
 * Model-free heuristics that mirror the (reference) swarm design:
 *  - decompose : split a request into distinct single-facet directives
 *  - pick      : choose the part to keep
 *  - verify    : does a returned chunk address the part it was given
 *  - manageable: `null` -> caller falls back to its char budget
 *
 * Content-word Jaccard (stopwords dropped) is used so short directives are not
 * merged just because they share function words.
 */
const STOPWORDS = new Set([
  'a', 'an', 'the', 'of', 'to', 'and', 'or', 'is', 'are', 'be', 'in', 'on',
  'for', 'with', 'that', 'this', 'it', 'as', 'at', 'by', 'do', 'does', 'did',
  'then', 'so', 'if', 'we', 'you', 'they', 'he', 'she',
]);
const WORD = /[a-z0-9]+/g;
const SPLIT = /(?<=[.!?;])\s+|\s+then\s+|\s+and\s+|,|\n+/i;
const DEDUPE_THRESHOLD = 0.5;

function tokens(text) {
  return new Set((String(text).toLowerCase().match(WORD) || []).filter((w) => !STOPWORDS.has(w)));
}

function jaccard(a, b) {
  const ta = tokens(a);
  const tb = tokens(b);
  if (!ta.size && !tb.size) return 0;
  let inter = 0;
  for (const t of ta) if (tb.has(t)) inter += 1;
  const union = new Set([...ta, ...tb]).size;
  return union ? inter / union : 0;
}

function dedupe(items, threshold = DEDUPE_THRESHOLD) {
  const kept = [];
  for (const item of items) {
    const text = String(item);
    if (!kept.some((k) => jaccard(text, k) > threshold)) kept.push(text);
  }
  return kept;
}

class HeuristicCoordinator {
  decompose(goal, maxParts = 6) {
    const text = String(goal || '').trim();
    if (!text) return [];
    let parts = text.split(SPLIT).map((s) => s.trim()).filter((s) => s.length > 1);
    if (!parts.length) parts = [text];
    return dedupe(parts).slice(0, maxParts);
  }

  pick(options, premise) {
    const opts = (options || []).map(String).filter((s) => s.trim());
    if (!opts.length) return null;
    const want = tokens(premise);
    let best = opts[0];
    let bestScore = -1;
    for (const opt of opts) {
      let score = 0;
      for (const t of tokens(opt)) if (want.has(t)) score += 1;
      if (score > bestScore) {
        best = opt;
        bestScore = score;
      }
    }
    return best;
  }

  verify(premise, criterion) {
    if (!premise || !criterion) return null;
    const want = tokens(criterion);
    if (!want.size) return null;
    let got = 0;
    const have = tokens(premise);
    for (const t of want) if (have.has(t)) got += 1;
    return got >= Math.max(1, Math.floor(want.size / 2));
  }

  manageable() {
    return null; // defer to the caller's size budget
  }
}

module.exports = { HeuristicCoordinator, tokens, jaccard, dedupe };

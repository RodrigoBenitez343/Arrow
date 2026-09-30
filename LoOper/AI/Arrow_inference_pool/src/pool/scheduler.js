'use strict';
/**
 * pool/scheduler.js — spread atoms over peers by proximity × idle compute × fit.
 *
 * Only the control plane is consulted: each peer's advertised idle capacity/load
 * (from the gossiped table) plus its live RTT. Capacity is consumed as atoms are
 * assigned so a node is not overloaded (backpressure). Proximity only *ranks*;
 * correctness is decided later by verification.
 */

function proximity(rtt) {
  if (rtt === null || rtt === undefined) return 0.25;
  return 1 / (1 + Math.max(0, rtt));
}

class Scheduler {
  constructor({ wProximity = 1, wIdle = 1, wFit = 1 } = {}) {
    this.wProximity = wProximity;
    this.wIdle = wIdle;
    this.wFit = wFit;
  }

  score(candidate, atom) {
    const load = Math.min(1, Math.max(0, candidate.load || 0));
    const idle = Math.max(0, candidate.capacityScore || 0) * (1 - load);
    let fit = 0.5;
    if (candidate.models && candidate.models.length) {
      const text = String(atom).toLowerCase();
      fit = candidate.models.some((m) => text.includes(String(m).toLowerCase())) ? 1 : 0.5;
    }
    return this.wProximity * proximity(candidate.rtt) + this.wIdle * idle + this.wFit * fit;
  }

  /** Assign each atom to the best peer. Returns [[atom, peerId], ...]. */
  plan(atoms, candidates) {
    if (!atoms || !atoms.length || !candidates || !candidates.length) return [];
    const free = new Map(candidates.map((c) => [c.peerId, Math.max(0, c.capacityScore || 0)]));
    const load = new Map(candidates.map((c) => [c.peerId, Math.max(0, c.load || 0)]));
    const plan = [];
    for (const atom of atoms) {
      let best = null;
      let bestScore = -Infinity;
      for (const c of candidates) {
        if (candidates.length > 1 && (free.get(c.peerId) || 0) <= 0) continue;
        const s = this.score(
          { ...c, capacityScore: free.get(c.peerId), load: load.get(c.peerId) },
          atom
        );
        if (s > bestScore) {
          best = c.peerId;
          bestScore = s;
        }
      }
      if (best === null) return [];
      plan.push([atom, best]);
      free.set(best, (free.get(best) || 0) - 1);
      load.set(best, Math.min(1, (load.get(best) || 0) + 0.25));
    }
    return plan;
  }
}

module.exports = { Scheduler, proximity };

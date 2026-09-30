# Routing Evaluation & Benchmarking Plan (v1)

*Status: design — not yet implemented. Date: 2026-09-26. Applies to the LoOper agent-mode routing pipeline (intent planner → routing ladder → worker dispatch → execution → verification) and to any change touching it (worker naming rules, planner wording, propagation packet, action-graph projection, learned chains).*

---

## Contents

1. Why evaluate this way (principles)
2. What is being evaluated
3. Artifacts & folder layout
4. The golden suite — row schema
5. The eval ladder (stages 0a / 0b / 1 / 2)
6. What gets captured per run
7. Scoring & metrics
8. Benchmarking strategy (modes, A/B protocol, mining loop)
9. Test corpus v1 — the first rows
10. Implementation roadmap
11. Hygiene rules
- Appendix A — log markers to parse
- Appendix B — environment switches
- Appendix C — current baseline (2026-09-26)

---

## 1. Why evaluate this way (principles)

These principles come from measured failures in this codebase, not from theory:

1. **Grade paths, not prose.** A worker's result prose ("I have opened LinkedIn…") is never evidence — measured 2026-09-23: result claims poisoned the done-probe (p_done 0.89 over a no-op) until the probe was restricted to steps-only premises. The evidence for a run is: which worker was picked, which chains executed, what the observed state digest showed, and what the final output states.
2. **Three grades of ground truth per test:**
   - **Outcome** — did the request get answered/achieved (fact checklist or end-state)?
   - **Path (binding)** — which worker(s)/chain(s) must serve it, in order, with `any_of` alternatives where several routes are legitimately correct, and an explicit **forbidden** list.
   - **Projection (step)** — which derived action lines should match each planned directive (the `Plan projection (shadow)` block prints exactly this).
3. **Every incident becomes a row.** The first row is the "what is in the website i opened?" mis-route (2026-09-25): the plan's observation wording matched 1/3 wired action sets and step 1 fell to a blind choice that ran the linkedin brain. Suites that do not grow from real failures lie.
4. **Cheap stages first.** Most routing bugs (the plan-wording funnel, false refusals from description negation, vocabulary mismatches) are provable **without executing anything** — engine-free, seconds, CI-able. Live end-to-end runs are the last stage, never the first.
5. **Comparing runs across different worlds is invalid.** Every row pins its `state_fixture` and the runner asserts the observed digest matches it.

## 2. What is being evaluated

```
request ──► PLAN (LLM turn; directive wording)          ← funnel: wording decides everything downstream
        ──► ROUTE (ladder: learned → assembly → chains gate → brain pick)
        ──► DISPATCH (scope + invoke worker, propagation packet)
        ──► EXECUTE (chains/nodes; run_memory records every node)
        ──► VERIFY (per-step verdict; done-probe; handback)
        ──► ANSWER (synthesis / trace)
```

The observability substrate already exists (built 2026-09-25/26) and is the primary capture source:

- `Action graph (shadow)` — per activation, per wired worker: coverage (`rendered/nodes`), skipped node kinds, rendered entity lines, `-> call "…" (k child line(s))` imports.
- `Plan projection (shadow)` — per planned directive: `directive -> worker: line (shared: …) | UNMATCHED`, closed by `(projection: k/n directive(s) matched at least one action line)`.
- `[ORCH-REMAINING]` / `[ORCH-OUTCOME]` — relayed handback + structured child outcome (`stop_reason`, last ≤8 step records, observed state), recorded on the parent trace entry as `outcome`.
- `node_<id>_trace` — the JSON step log (`{n, brain, step, result, ok, outcome?}`).
- `data/run_memory.db` — `begin_run(chain_path, source, query)` → `record_node(...)` per executed node → `end_run`; run_id-scoped, so the executed path is machine-readable.

## 3. Artifacts & folder layout

```
LoOper/tests/evals/
  routing_eval_v1.jsonl        # the golden suite (one JSON row per test)
  runner.py                    # --stage 0a|0b|1|2 --filter <tag> --repeat N --label X
  mine_row.py                  # run.log path -> draft row (incident mining)
  results/
    20260926_baseline.json     # metric snapshot per label
    compare.py                 # delta report between two snapshots
  README.md                    # short usage note (optional)
LoOper/tests/test_eval_routing.py   # thin pytest wrapper running stage 0a rows (CI, engine-free)
```

## 4. The golden suite — row schema

```jsonc
{
  "id": "web-read-01",
  "tags": ["web", "read", "incident-2026-09-25"],
  "request": "what is in the website i opened?",
  "state_fixture": {
    "setup": "looper_browser_on:example_page",      // named recipe; deterministic page in the LoOper web engine
    "expect_observe_contains": ["Example Page"]     // sanity: the world is the pinned one
  },
  "gold": {
    "path": [                                       // ordered hops; each hop is a directive-shaped intent
      {"directive_names": ["screen", "displayed"],
       "worker_any_of": ["vision_system_chain", "web_basetools"],
       "chain_any_of": ["screen_check", "web_check"]},
      {"directive_names": ["text", "content", "visible", "page"],
       "worker_any_of": ["web_basetools", "vision_system_chain"],
       "chain_any_of": ["web_check"]},
      {"directive_names": ["report", "observe", "website"],
       "worker_any_of": ["web_basetools", "vision_system_chain"]}
    ],
    "forbidden": ["linkedin_system_chain", "go to linkedin", "messages", "my network"],
    "outcome": {"must_mention": ["Example Page", "<known page fact>"], "must_not_mention": []},
    "budget": {"max_steps": 6, "max_laya_forwards": 14}
  },
  "plan_mode": "live",          // live = real planner turn; pinned = use "pinned_plan" (isolate routing from planning)
  "pinned_plan": [],            // required when plan_mode == "pinned"
  "expected": "pass",           // pass | known_fail (known_fail rows never redden CI; their count is a tracked metric)
  "modes": ["0a", "0b", "1", "2"]
}
```

Notes:

- `worker_any_of` / `chain_any_of` accept several legitimate routes; the row fails only outside the set.
- `forbidden` is the safety list ("never again" paths). The release gate is **zero forbidden hits**.
- Rows are single-purpose. Row length grows by count, not by per-row complexity.

## 5. The eval ladder (stages 0a / 0b / 1 / 2)

| Stage | What runs | Determinism | Runtime / cadence | Catches |
|---|---|---|---|---|
| **0a Routing-only** | Authored brains/chains list + a pinned directive (from `pinned_plan` or the row's gold intent) → the real deterministic picker (`_orchestrator_laya_worker` naming rules), the chains gate, `graph_candidate_lines` + projection. Invocation stubbed to record. Engine touched only when the case needs `choice`/`noul`; the deterministic namer path needs none | High (replayable) | seconds; CI on every change | False refusals, vocabulary mismatches, forbidden picks, projection coverage — the whole incident class |
| **0b Planner-only** | Real planner turn per row (`request` + pinned state digest) → grade emitted directives against `gold.path[*].directive_names` (stem overlap ≥1 word per hop) + count stray/legacy actions | Sampled → repeat 3–5× | ~1 LLM call/row/repeat; pre-merge | The wording funnel itself, planner copy-poisoning, dropped/duplicated steps |
| **1 Dry-run integration** | Full `_execute_orchestrator_node` against the **real wired ORCHESTRATOR.json**, with `_orchestrator_invoke_brain` stubbed to record (the pattern the unit tests use). Plans sampled or pinned per row | Planner sampled; routing deterministic | minutes; pre-merge | Whole-ladder behavior on the real library: whole-job rung, assembly, chains gate, picks, handbacks, packet vars |
| **2 Live E2E** | Real execution against fixture pages/apps; path extracted from `run_memory.db`; decisions parsed from `[ORCH]` log lines | World-dependent | tens of minutes; weekly / pre-release | Outcome correctness, real resource waste, observed-state grounding |

Fixture rule for web rows (stage 2): serve a static page into the **LoOper web engine** (`looper_browser_on:<page>`); never depend on the user's own Chrome — that ambiguity was exactly the original incident and is not reproducible.

Runner interface (sketch):

```bash
python LoOper/tests/evals/runner.py --stage 0a --label baseline
python LoOper/tests/evals/runner.py --stage 0b --filter web --repeat 3 --label vocab_on
python LoOper/tests/evals/runner.py --stage 1  --filter propagation --label packet_v1
python LoOper/tests/evals/runner.py --compare results/20260926_baseline.json results/20261002_vocab_on.json
```

## 6. What gets captured per run

One **run record** per (row, repeat), assembled from the sources in §2:

| Field | Source |
|---|---|
| `directives[]` | `goal split into %d directive(s)` log line / stub capture |
| `picks[]` = `{worker, step_text, verdict}` | trace JSON (`node_<id>_trace`) |
| `projection[]` = directive → match/UNMATCHED + shared words | `Plan projection (shadow)` block |
| `graph_coverage{worker: rendered/nodes}` | `Action graph (shadow)` block |
| `dispatches[]` = chains executed, in order | `run_memory.db` events for the run_id (live) / stub capture (0a/1) |
| `handbacks`, `outcomes` | `[ORCH-REMAINING]` / `[ORCH-OUTCOME]` parses (`outcome` field on trace entries) |
| `state_digest_at_start` | `observed state:` log line — validates `state_fixture` |
| `stop_reason`, `steps`, `wall_time` | `finished: steps=%d stop_reason=%s` line |
| `resource counters` — Laya forwards, LLM calls | counted from engine log lines (`Laya done-probe`, `choice`, `verify verdict`, planner/scope turns) |
| `final_answer` | orchestrator `output` port text / output-node text |

## 7. Scoring & metrics

Per row, a glanceable verdict card:

```
[PASS] web-read-01  hops=3/3  first=ok  projection=3/3  forbidden=0  waste=0  outcome=2/2  steps=4/6
[FAIL] web-read-01  hops=2/3  diverged_at=hop2 (picked linkedin_system_chain)  forbidden=1
```

Definitions:

- **hop accuracy** = matched hops / gold hops, evaluated in order, each hop accepted if the chosen worker/chain is in `any_of`.
- **first-hop accuracy** — the single most diagnostic number for the funnel.
- **forbidden hits** — count; release gate = 0.
- **projection coverage** = matched directives / total directives (per row and per tag). Baseline reference: the incident row measured **1/3** on 2026-09-26.
- **outcome facts** — per-fact Laya `noul` on **steps-only premises** (never the run's own prose); a row passes outcome when every `must_mention` verifies true and no `must_not_mention` appears. Human adjudication on a small sample of `known_fail`/disputed rows.
- **honest-stop correctness** — rows tagged `unserved` must end `stop_reason in (no_worker, blocked, ask)`; a wrong run is a fail even if it "did something".
- **waste** — steps used vs. minimal path length; executed chains outside any gold path; Laya forwards vs. `max_laya_forwards` budget.
- **aggregates** — per tag (`web`, `desktop`, `observation-only`, `refusal`, `unserved`, `propagation`, `learning`) plus a snapshot file per label; `compare.py` prints row-by-row deltas.

## 8. Benchmarking strategy

### 8.1 Three modes, three cadences

| Mode | Stages | When | Purpose |
|---|---|---|---|
| **M1 — fast bench** | 0a (+ stage-0a pytest wrapper) | every change, CI (no engine, no GUI) | routing regressions: refusals, forbidden picks, projection coverage |
| **M2 — planner bench** | 0b + 1, `--repeat 3–5` | pre-merge | planner wording quality and full-ladder behavior on the real library |
| **M3 — release bench** | 2 (live subset with fixtures) | weekly / pre-release | outcome correctness and real resource waste |

### 8.2 A/B protocol (template for any routing change)

1. Run M1/M2 on the unchanged tree → `--label baseline` (repeat 3 for sampled stages).
2. Apply the change; run the same rows → `--label <change>`.
3. `compare.py` the two snapshots; read the row-level failures, not just aggregates (a fix that trades one forbidden hit for another is not a fix).

**Acceptance bar (default):**

- forbidden hits stay **0**;
- first-hop accuracy does not drop on any row that currently passes;
- projection coverage strictly improves on `web` / `observation-only` tags;
- all other tags unchanged;
- no outcome regressions on rows tagged `pass`.

### 8.3 Micro/macro consistency

Generate Gate-A samples for `AI/laya_bench.py` from the same suite rows (premise = request + pinned state; options = worker lines; gold = hop-1 worker). One source of truth keeps the Laya micro-bench and the pipeline bench aligned.

### 8.4 Incident mining loop

`mine_row.py <run.log>` extracts request, plan, picks, projection and dispatch lines and stamps a draft row with the **observed (wrong) path in `forbidden`**. Every future incident adds a row the same day. Rows expected red until a fix lands are `"expected": "known_fail"` — visible in reports, never noisy; the count of `known_fail` is itself a tracked metric that should trend to 0.

## 9. Test corpus v1 — the first rows

| # | id | Request | What it pins |
|---|---|---|---|
| 1 | `web-read-01` | "what is in the website i opened?" | The incident: read-path routing, forbidden navigation, outcome facts from a fixture page |
| 2 | `observe-only-01` | "check what's on the screen" | No navigation chain may execute (observation guard) |
| 3 | `refusal-01` | "open LinkedIn" | Vision (whose description refuses `open`) must not be picked; linkedin brain must |
| 4 | `refusal-02` | "check the screen" | Vision must be picked deterministically (the `screen_check` alias case) |
| 5 | `unserved-01` | "check the weather" | Honest stop (`no_worker`/`ask`), zero executions |
| 6 | `compound-01` | "open linkedin and click on my network" | Two-hop path; handback integrity if hop 2 fails |
| 7 | `learned-01` | job x, run twice | Second run hits the whole-job rung (`steps=1`, artifact in dispatches) |
| 8 | `propagation-01` | relayed nested brain | Goal resolved from `_chain_goal`; `Root goal:` / `PARENT PLAN:` lines present; `Parent-observed:` fallback on empty state |
| 9 | `propagation-02` | child emits `[ORCH-OUTCOME]` | Parent trace entry carries `outcome`; prose clean; cursor untouched |
| 10 | `budget-01` | a 4-hop request | `max_steps` and `max_laya_forwards` respected |

## 10. Implementation roadmap

1. **Slice 1 (day one):** `routing_eval_v1.jsonl` with rows 1–5; `runner.py` with **stage 0a only** (engine-free paths, reusing the unit-test harness pattern and the projection block); the incident mined as `web-read-01`; `results/20260926_baseline.json` recorded; `compare.py`. A runnable benchmark the same day.
2. **Slice 2:** stage 0b (planner grading, `--repeat`), stage-0a pytest wrapper wired into `LoOper/tests/`.
3. **Slice 3:** stage 1 dry-run integration on the real wired library; log/trace parsers per Appendix A.
4. **Slice 4:** stage 2 live runners + fixture page recipes; `mine_row.py` hardening.
5. Wire M1 into the change workflow: a routing change is not done until the snapshot comparison is attached.

## 11. Hygiene rules

- Never grade outcomes with the run's own result prose (probe-poisoning lesson); steps-only premises, observed state as the second witness.
- Every row pins `state_fixture` and asserts `state_digest_at_start` — invalid comparisons otherwise.
- Keep rows single-purpose; prune or fold duplicates monthly.
- `known_fail` rows are reported but never fail CI; the count trends to 0.
- Do not compare snapshots taken with different fixture pages / different wired libraries; the snapshot file records the library fingerprint (chain file hashes) for this reason.

---

## Appendix A — log markers to parse

| Marker (substring) | Meaning |
|---|---|
| `===== System chain execution =====` + `Chain:` + `Query:` | run start (system chain dispatch) |
| `[ORCH] Node … start: goal=… brains=… chains=… picker=…` | activation shape |
| `goal split into N directive(s): …` | the plan |
| `Worker pick: excluded …` / `Worker pick (deterministic): …` | naming / refusals (pre-engine) |
| `delegating N directive(s) of X's scope` | nested hand-off + delegated list |
| `invoking brain X … \| <scoped step>` | dispatch with the exact step |
| `verify verdict=True/False` | per-step verdict |
| `Action graph (shadow)` | coverage + rendered action lines per worker |
| `Plan projection (shadow)` | directive → match/UNMATCHED + shared words |
| `[ORCH-REMAINING]` / `[ORCH-OUTCOME]` | relayed handback (machine-readable) |
| `finished: steps=… stop_reason=… blocked=…` | the honest ending |

## Appendix B — environment switches

| Variable | Effect (relevant to evaluation) |
|---|---|
| `LOOPER_ORCH_STATE` | `off` disables the world digest — changes what the picker/scoper/verifier see; only run eval stages with a consistent setting |
| `LOOPER_ORCH_GRAPH` | `off` silences both shadow blocks — **never set it when capturing** (they are the primary signal) |
| `LOOPER_LEARN` | `off` disables freeze — use for repeatable runs that must not create artifacts (`learned-01` excepted) |
| `LOOPER_LAYA` / `LOOPER_LAYA_*` | engine availability/overrides — stages 0b/1 need it up for the full picker composition |
| `LOOPER_RUN_MEMORY` / `LOOPER_RUN_MEMORY_DB` | activity timeline on/off + DB path (stage 2 path extraction) |
| `LOOPER_GOAL_LEDGER` | goal ledger path (isolate per-test ledgers in temp dirs) |

## Appendix C — current baseline (2026-09-26)

- `Plan projection` on the incident plan: **1/3 directives matched** (screen → `screen_check` via its import prefix; the text/content and observe/website directions matched nothing).
- Worker-naming false refusal (machine notes read as negations) fixed 2026-09-25 — refusal now reads human prose only.
- Known coverage gaps measured on the live library: `linkedin_system_chain`, `vision_system_chain`, `web_basetools` render **0/N own nodes** (their only own node is a label-less input); their routing rests on descriptions and one-level import child lines (`jobs: click "Jobs"` renders correctly).
- Test suite: 80 modules / 1,903 tests collected (`python -m pytest LoOper/tests/ -q`).
- Which changes this plan gates next: planner vocabulary injection (O1) and, later, planner selection over graph paths (O4).

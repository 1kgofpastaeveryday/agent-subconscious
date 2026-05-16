# Adversarial Design Review - 2026-05-15 Round 2

## Review Configuration
- Project: <workspace>
- Focus: General
- Tier: mid
- Reviewers: Attack Surface, Production Stress, Assumption Challenger, Implementation Reality, Coherence Auditor
- Execution mode: Codex subagents
- Manifest: <workspace>\<local-review-artifact>

## Scoring Summary

| Reviewer | Correctness | Completeness | Implementability | Resilience | Weighted Avg | Binding? |
|---|---:|---:|---:|---:|---:|:---:|
| Attack Surface | 5 | 5 | 6 | 4 | 5.125 | yes |
| Production Stress | 4 | 3 | 5 | 2 | 3.625 | yes |
| Assumption Challenger | 6 | 5 | 5 | 5 | 5.375 | yes |
| Implementation Reality | 4 | 3 | 5 | 4 | 4.000 | yes |
| Coherence Auditor | 6 | 6 | 5 | 5 | 5.625 | yes |
| **Average** | **5.0** | **4.4** | **5.2** | **4.0** | **4.750** | yes |

PASS/FAIL: FAIL. Binding lanes scored Completeness <= 3 and Resilience <= 3.

## Cross-Lane Convergence

- Real installed hook readiness remains blocked without a non-fixture Codex host attestation/main-agent proof path.
- Real feedback verification remains blocked without a daemon-owned signing key lifecycle distinct from fixture keys.
- Durable lifecycle/state remains under-specified for production: generation/purge, session-aware idle, stale daemon cleanup, cursor expiry, and append-log recovery.
- Tool/browser investigation remains documentation-only and should not count toward MVP readiness.

## Terminal Classification

BLOCKED. The loop cannot safely continue to PASS without product/host decisions and broader architecture implementation outside the current reviewed spike:

- Define or obtain a real Codex host attestation/main-agent distinction signal, or formally keep installed hooks non-operational outside fixture mode.
- Decide the real signing-key ownership model and implement non-fixture key generation/storage/rotation.
- Decide whether current file-state is fixture storage or the real local control plane, then align generation, purge, session, daemon status, and cursor state contracts.
- Decide adapter ABI/test scope for read-only file/git/browser/network investigation.

## Fixes Applied Before Round 2

- Added bounded state-lock acquisition in `agent_subconscious/hook_probe.py`.
- Bounded daemon processing by item count and time in `agent_subconscious/daemon.py`.
- Broadened safe feedback validation to include documented `intent mismatch` and quoted evidence shapes while retaining one-line/no-instruction filtering.
- Rendered installed plugin hooks with the current absolute Python interpreter in `agent_subconscious/codex_plugin_setup.py`.
- Made config writes use unique temp files and fsync the temp file before replace.
- Split fixture readiness from login readiness in setup diagnostics and docs.
- Added tests for fixture readiness semantics, absolute interpreter rendering, and documented feedback body shapes.

## Verification

- `pytest -q`: failed in this environment because the repo root was not on `PYTHONPATH`.
- `$env:PYTHONPATH='.'; pytest -q`: 78 passed, 1 skipped.


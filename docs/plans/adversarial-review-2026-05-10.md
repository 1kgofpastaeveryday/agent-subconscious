# Adversarial Design Review - 2026-05-10

## Review Configuration

- Date: 2026-05-10
- Project: `<workspace>`
- Focus: current design readiness
- Tier: mid
- Live QA: not requested
- Execution mode: Codex subagents
- Reviewers:
  - Attack Surface
  - Production Stress
  - Assumption Challenger
  - Implementation Reality
  - Coherence Auditor
- Review targets:
  - `README.md`
  - `AGENTS.md`
  - `docs/design.md`
  - `docs/open-decisions.md`
  - `docs/integrations/knudg.md`
  - `docs/references/claude-subconscious.md`
  - `docs/plans/adversarial-review-2026-05-09.md`
  - `<local-review-artifact>`

## Result

FAIL.

The design direction is clear, but the current documents do not represent a completed or implementation-ready design. The most important issue is that the product docs present an MVP feedback loop, while the review artifacts say the MVP remains blocked by open decisions and missing safety contracts.

## Scoring Summary

| Reviewer | Correctness | Completeness | Implementability | Resilience | Weighted Avg |
|---|---:|---:|---:|---:|---:|
| Attack Surface | 4 | 2 | 3 | 2 | 3.00 |
| Production Stress | 5 | 3 | 4 | 3 | 4.00 |
| Assumption Challenger | 4 | 3 | 3 | 3 | 3.38 |
| Implementation Reality | 5 | 3 | 3 | 4 | 3.88 |
| Coherence Auditor | 4 | 3 | 4 | 4 | 3.75 |
| **Average** | **4.4** | **2.8** | **3.4** | **3.2** | **3.60** |

PASS/FAIL: **FAIL** because average Completeness is <= 3, and multiple reviewers scored Completeness, Implementability, or Resilience <= 3.

## Cross-Lane Convergence

Issues identified by 3+ reviewers:

| Issue | Reviewers | Tier | Confidence |
|---|---|---|---|
| MVP status contradicts open blockers and prior review artifacts | Attack Surface, Production Stress, Assumption Challenger, Implementation Reality, Coherence Auditor | Tier 1 | HIGH |
| Prompt feedback injection is not a safe, concrete contract | Attack Surface, Production Stress, Assumption Challenger, Implementation Reality, Coherence Auditor | Tier 1 | HIGH |
| Missing event, storage, delivery, retry, purge, and concurrency schemas | Production Stress, Assumption Challenger, Implementation Reality, Coherence Auditor | Tier 1/Tier 2 | HIGH |
| Sanitization and secret-handling requirements are not testable | Attack Surface, Assumption Challenger, Implementation Reality | Tier 1/Tier 2 | HIGH |
| Read-only investigation boundary is policy-only, not enforced | Attack Surface, Assumption Challenger, Implementation Reality | Tier 1/Tier 2 | HIGH |
| Backend credential model blocks live sidecar calls | Attack Surface, Production Stress, Assumption Challenger, Implementation Reality | Tier 2 | HIGH |

Issues identified by 2 reviewers:

| Issue | Reviewers | Tier | Confidence |
|---|---|---|---|
| Local daemon control plane lacks explicit auth/bind/CORS/CSRF/rate-limit contract | Attack Surface, Implementation Reality | Tier 1 | HIGH |
| Plain Markdown/JSON memory is risky for concurrent async state and poisoning | Attack Surface, Production Stress | Tier 1/Tier 2 | HIGH |
| Evaluation metrics lack thresholds and fixture-based verification | Implementation Reality, Coherence Auditor | Tier 2/Tier 3 | MEDIUM |
| Knudg bridge is correctly out of v0 but still needs trust-merge rules before future implementation | Attack Surface, Assumption Challenger | Tier 3 | MEDIUM |

## Tier 1 - Immediate Fix Required

### T1-001: Current status is contradictory

`README.md` says "MVP is a local auto-start feedback loop." `docs/design.md` still describes a rollout that begins with local daemon work. But `<local-review-artifact>` says `Final Status: BLOCKED`, and the prior review report says D-001, D-002, and active/prompt-injection work remain blockers.

Fix: add one authoritative current-status section to `README.md` and `docs/design.md`. State that implementation is blocked until event source, credential/backend, prompt injection/verifier, storage/state, and sanitizer contracts are accepted. If the prior review artifacts are stale, archive or supersede them explicitly.

### T1-002: Prompt injection is the core product path, but not yet safe

The design says feedback is advisory and not instruction hierarchy, but the intended delivery path injects it next to the next user prompt. Reviewers flagged missing signed envelopes, verifier contract, replay protection, source identity, expiry, prompt correlation, and a clear rendering wrapper that keeps feedback as untrusted advisory evidence.

Fix: define a `FeedbackItem` contract with stable id, workspace/session id, source, issued/expiry timestamps, confidence/risk, evidence refs, escaped body, signature/HMAC or equivalent verifier, and rejection rules for unsigned, malformed, expired, duplicate, or wrong-workspace feedback.

### T1-003: Delivery and background observation are not recoverable

The current flow says `fetch -> emit -> mark delivered` and `send compact update in background`, but it does not define durable outbox, retry state, acknowledgement semantics, idempotency, dead-letter handling, or stale-feedback rules.

Fix: add state machines for `ObservationEvent` and `FeedbackItem`. Minimum delivery states should cover pending, claimed, injected, acknowledged, expired, failed, and dead-lettered. Add prompt correlation ids and TTL/supersession behavior.

### T1-004: Control-plane and tool boundaries are policy-only

The docs require a local daemon and read-only/disposable investigation, but do not define loopback/named-pipe binding, capability tokens, CORS/Origin handling, rate limits, browser isolation, command allowlists, or proof that tools cannot mutate real workspace/authenticated state.

Fix: add a capability matrix and adapter contracts. For v0, expose only hardcoded read-only adapters, disposable browser profiles with no shared cookies/extensions, no generic shell write path, and an authenticated local control plane.

### T1-005: Sanitizer requirements cannot be tested

The design says to prevent obvious leaks and bound payloads, but does not define allowlisted fields, redaction classes, entropy checks, max sizes, logging policy, fixtures, or provider retention constraints.

Fix: add a mechanical sanitizer contract and fixture suite before any backend calls. This should include event-specific allowlists, secret regex and entropy detection, no auth headers/cookies, redacted diagnostics, payload limits, omitted-content markers, and zero-tolerance privacy leak tests.

## Tier 2 - Decide Before Implementation

### T2-001: D-001/D-002/D-003 must become accepted, blocked, or deferred

`docs/open-decisions.md` uses "Default:" language for core decisions, while `README.md` and `docs/design.md` consume those defaults as if they are architecture. That leaves implementers guessing whether host hooks, backend credentials, and prompt injection are chosen.

Fix: give each open decision a status field: `proposed`, `accepted`, `blocked`, or `deferred`. If Codex hooks are the v0 path, mark D-001 and D-003 accepted and document exact hook behavior.

### T2-002: Backend credential model blocks live agent calls

The design requires an LLM backend but leaves official API credentials, Codex OAuth, or provider adapters open. That is not buildable without creating security, spend, retention, and revocation risk.

Fix: for v0, choose explicit experiment API credentials only, or mark backend calls disabled. Define storage, scope, revocation, budget/rate limits, audit logging, and provider data retention assumptions.

### T2-003: Storage format is not implementation-ready

The design names `.codex/subconscious/memory.md`, `feedback.md`, `sessions/`, and `state.json`, but does not define versioned schemas, atomic writes, file locking, crash recovery, migration, purge generation, or path containment.

Fix: add `docs/contracts.md` with JSON schemas for `ObservationEvent`, `FeedbackItem`, `DeliveryCursor`, `DaemonState`, `SessionRegistration`, `SanitizationReport`, and `PurgeRun`. Decide whether the daemon is a single writer with atomic rename, or whether v0 needs a transactional store.

### T2-004: Alternate resource fallback is not a proven injection path

The design lists "a local resource provider that Codex automatically queries before turns" as an acceptable alternative, but host-selected resources are not inherently auto-injected.

Fix: demote resource-provider fallback to non-v0 unless a Codex-specific auto-query proof exists. Treat `UserPromptSubmit` hooks or a wrapper as the only candidate v0 injection path until proven otherwise.

### T2-005: Evaluation lacks pass/fail thresholds

The current pass criterion is qualitative: feedback should change the main agent's behavior usefully. That cannot verify readiness.

Fix: add a fixture-based eval suite with canned events/transcripts, expected feedback classes, max distracting-feedback rate, latency ceiling, zero privacy leaks, and daemon lifecycle tests.

## Tier 3 - Future Risk

- Add protocol and state schema versioning so stale daemons cannot serve incompatible clients.
- Add OS-specific lifecycle tests for Windows/macOS/Linux: duplicate start race, pid reuse, dead heartbeat, changed executable path, idle timeout, and no kill-by-port regression.
- Keep Knudg bridge excluded from v0. Any future bridge RFC must define outbound query schema, provenance rendering, stale-card handling, tenant-safe citation, and trust merge rules.
- Mark the Claude acknowledgement-instruction behavior as reference-only. It should not be copied into Codex v0 unless D-003 explicitly accepts host-level wrapper instructions.

## Statistics

- Total raw findings: 44
- Tier 1: 15 (HIGH: 15, MEDIUM: 0, LOW: 0)
- Tier 2: 19 (HIGH: 15, MEDIUM: 4, LOW: 0)
- Tier 3: 10 (HIGH: 0, MEDIUM: 10, LOW: 0)
- Cross-lane convergence, 3+ reviewers: 6
- Cross-lane convergence, 2 reviewers: 4
- Average weighted score: 3.60/10

## Sources Cited By Reviewers

- OpenAI Codex Hooks: https://developers.openai.com/codex/hooks
- OpenAI agent safety guidance: https://developers.openai.com/api/docs/guides/agent-builder-safety
- OWASP API Security Top 10 2023: https://owasp.org/API-Security/editions/2023/en/0x11-t10/
- OWASP LLM Top 10: https://owasp.org/www-project-top-10-for-large-language-model-applications
- OWASP CSRF Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- Azure Circuit Breaker pattern: https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker
- Azure reliability design patterns: https://learn.microsoft.com/en-us/azure/well-architected/reliability/design-patterns
- Google SRE, Addressing Cascading Failures: https://sre.google/sre-book/addressing-cascading-failures/
- AWS ADR process: https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- GOV.UK ADR Framework: https://www.gov.uk/government/publications/architectural-decision-record-framework/architectural-decision-record-framework
- Claude Code Hooks: https://code.claude.com/docs/en/hooks

## Raw Reviewer Output Index

Raw reviewer outputs were collected from the following subagent lanes and synthesized above:

- Attack Surface: local daemon auth/bind/CORS/CSRF boundary, feedback spoof/replay, memory path traversal, sanitizer gaps, browser safety, rate limits, credentials, purge/retention, Knudg outbound risk.
- Production Stress: delivery atomicity, durable outbox, purge races, concurrent Markdown/JSON writes, backend outage behavior, daemon health semantics, stale feedback, doc conflict, diagnostics, resource exhaustion, migration/rollback.
- Assumption Challenger: MVP marked runnable while blockers remain, advisory feedback entering stronger prompt context, read-only investigation not mechanically enforced, compact summaries lacking fidelity contract, memory poisoning/retention, credential model, daemon version skew, Knudg trust merge.
- Implementation Reality: MVP status contradiction, host injection contract, missing event/storage schemas, sanitizer tests, tool enforcement design, backend credentials, resource-provider fallback overstatement, concurrency model, lifecycle tests, evaluation thresholds.
- Coherence Auditor: review artifacts claim fixes absent from current product docs, open decisions consumed as binding architecture, D-003 naming inconsistency, acceptance criteria without traceability, Claude-specific instruction behavior leaking into Codex design.

## Next Fix Plan

1. Reconcile status first: update `README.md`, `docs/design.md`, and `docs/open-decisions.md` so they agree whether this is a spike or implementation-ready MVP.
2. Close D-001 and D-003 for Codex specifically, or explicitly mark active injection blocked.
3. Add contracts for events, feedback, delivery cursor, sanitizer report, daemon state, and purge runs.
4. Add control-plane, credential, and read-only tool enforcement requirements.
5. Add fixture-based acceptance tests/evals before implementation work.


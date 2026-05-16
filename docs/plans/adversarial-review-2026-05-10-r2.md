# Adversarial Review - 2026-05-10 Round 2

## Verdict

BLOCKED for MVP implementation readiness.

PASS for a spike-ready design contract set.

The remaining blocker is not a hidden design contradiction. MVP work is
intentionally gated on a passing Codex hook fixture that proves the selected
Codex product surface, hook events, selected `additionalContext` transport,
transcript/log visibility, timeout behavior, Windows path behavior, and
main-agent versus subagent distinction.

## Review Configuration

- Project: `<workspace>`
- Target files: `README.md`, `AGENTS.md`, `docs/design.md`,
  `docs/open-decisions.md`, `docs/contracts.md`,
  `docs/codex-hooks-v0.md`, `docs/integrations/knudg.md`,
  `docs/references/claude-subconscious.md`
- Review type: targeted adversarial re-review after fixes
- Stop condition: pass, hard blocker, or parked-only residuals

## Blocking Finding

| Finding | Severity | Status |
|---|---:|---|
| Codex hook behavior must be proven before MVP implementation: `SessionStart`, `UserPromptSubmit`, `Stop`, exact-once prompt-context delivery, fail-closed behavior, transcript/log visibility, Windows path handling, and main-agent/subagent separation. | Tier 1 | BLOCKED on fixture proof |

## Cleared Prior Tier 1 Findings

| Prior finding | Current result |
|---|---|
| MVP status contradicted prior review artifacts and open decisions. | Cleared. Docs now say design spike, MVP blocked until gates pass. |
| Prompt injection contract was unsafe and underspecified. | Cleared for spike. Feedback requires signed `FeedbackItem`, verifier rejection rules, prompt-turn eligibility, and hard-coded wrapper. |
| Event, storage, delivery, retry, purge, and concurrency contracts were missing. | Cleared for spike. `docs/contracts.md` defines durable records, state transitions, purge generation, queue admission, delivery cursors, and daemon lifecycle. |
| Read-only investigation relied on prompting instead of enforcement. | Cleared for spike. Tool boundaries are adapter-enforced; browser work is deferred until disposable-profile and network-safety proofs exist. |
| Backend credential model was open. | Cleared for spike. Backend review is disabled by default per workspace and requires explicit experiment credentials. |
| Local v0 blurred with Knudg shared-memory product ideas. | Cleared. Knudg remains a separate integration note. |
| Claude-specific hook assumptions leaked into Codex design. | Cleared. Claude behavior is reference-only; Codex has a separate fixture contract. |

## Parked Non-Blocking Findings

- Exact resource budget values for CPU, memory, log bytes, screenshot bytes,
  artifact bytes, and disk quota should be chosen before implementation.
- Status diagnostics can later enumerate an allowlist of returned fields.
- A future design may use asymmetric signing if hook-side verification should
  not mint feedback.

## Final Notes

The design should not be described as "done" for MVP. It is ready to implement
the Codex hook spike and fake-feedback fixture work. A live backend, browser
investigation, and full MVP loop remain gated by executable proofs.


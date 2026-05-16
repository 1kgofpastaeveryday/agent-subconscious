# Adversarial Design Review - 2026-05-09

## Review Configuration

- Project: <workspace>
- Tier: mid
- Focus: general design review
- Review targets:
  - README.md
  - docs/design.md
  - docs/open-decisions.md
  - docs/integrations/knudg.md
- Live QA: not requested
- Execution mode: Codex subagents

## Round 1 Result

FAIL.

Converged blockers:

- Sanitization was a goal, not a mechanically testable allowlist.
- Raw and sanitized local storage boundaries were ambiguous.
- Active notes could become an instruction-injection path.
- Processing, retry, purge, snooze, and projection state were not recoverable.
- Backend credentials, retention, retry, and outage handling were underspecified.
- SQLite encryption, purge, WAL/temp handling, and retention were not implementable.
- Event source selection was blocking but README implied MVP could start.

## Fixes Applied

- Split spike from MVP and marked MVP blocked until event source, backend, and encrypted storage decisions close.
- Added sanitizer acceptance gates, sensitive context inventory, HMAC identifiers, and diagnostic redaction rules.
- Replaced direct active-note Markdown consumption with verifier-only signed structured records.
- Added local control-plane security requirements and capability-scoped controls.
- Added processing state, backend dependency state, purge journal, TTL/retention defaults, and retry/circuit policy.
- Added SQLCipher-compatible v0 storage profile with rollback journal and no WAL.
- Added stable intervention idempotency based on workspace event, policy, content digest, and evidence refs.
- Tightened Knudg integration as non-v0 future RFC context.

## Round 2/3 Result

The follow-up reviews continued to find gaps until the last fix pass. The final
local consistency check found no remaining old conflict text such as direct
agent Markdown consumption or raw `event_json` storage. A full fresh subagent
PASS was not run after the final fix pass.

## Remaining Blockers

- D-001 event source remains intentionally open and blocks MVP.
- D-002 backend/credential allowlist remains intentionally open and blocks live backend calls.
- Active mode remains blocked until verifier contract and host compatibility proof are accepted.

## Parked Findings

- Knudg bridge details are parked as non-v0 future RFC material.
- Statistical evaluation details may need refinement after the spike generates realistic scenarios.


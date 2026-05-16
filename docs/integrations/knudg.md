# Knudg Integration Context

Status: context note, not v0 scope

This document preserves the "Agent Subconscious with Knudg" product context
without mixing it into the local-only v0 design.

V0 exclusion: this repository must not add Knudg client code, schema fields,
auth abstractions, Knudg-specific provenance fields, feature flags, bridge
interfaces, or runtime dependencies until a separate bridge RFC is accepted.
Local observation provenance used by Agent Subconscious for traceability is
still part of the local v0 design.

## Boundary

The local Agent Subconscious project remains useful without Knudg:

- observes active local work
- creates compact session updates
- asks a background reviewer for concise second opinions
- stores Subconscious-owned local memory
- injects advisory feedback into the next prompt context

The Knudg-enabled version is a separate product shape:

- retrieves relevant shared experience cards from Knudg
- uses private/team/public corpora according to Knudg authorization
- lets the local sidecar compare current work against known solved paths,
  failed paths, deprecations, and environment traps
- can submit candidate experiences back to Knudg only through Knudg's writer,
  consent, review, and revocation flows

## Future Bridge RFC Sketch

This section is non-v0, not accepted, and not an implementation interface. Do
not create code, schemas, auth abstractions, flags, or provenance placeholders
from this diagram in v0.

```text
local event source
  -> deterministic sanitizer
  -> local observation profile
  -> local subconscious reviewer
  -> optional Knudg search_similar call
  -> merged advisory interventions
  -> next-prompt feedback injection and local memory
```

This diagram shows one possible ordering only. Ordering, retrieval timing,
failure behavior, and merge rules remain undecided until a bridge RFC.

Knudg cards remain untrusted evidence. The local sidecar can use them as
additional context for a second opinion, but it cannot promote them to
instructions or bypass local sanitization.

## Possible Future Integration Modes

These modes are hypotheses for a future bridge RFC, not accepted v0
requirements.

### Read-Only Retrieval

The sidecar may call Knudg `search_similar` with a sanitized outbound query
profile. Results are displayed or summarized as advisory context only.

Hypothesis: if integration is ever approved, read-only retrieval is likely the
first mode to evaluate. This is not a v0 decision.

### Team-Aware Private Retrieval

For enterprise use, the sidecar may search a team-shared private corpus. This
requires Knudg tenant, namespace, principal, and token enforcement. The local
sidecar is not the authorization boundary.

### Candidate Submission

The sidecar may propose that a useful experience should become a Knudg
candidate. It may prepare a draft summary, but only Knudg's writer pipeline can
create, redact, review, approve, publish, or revoke cards.

### Risk Preflight

The sidecar may run a local risk preflight before sending a query to Knudg:

- likely secret exposure
- private path leakage
- customer or tenant identifier leakage
- excessive specificity
- unsafe high-risk command/package/repository advice

This preflight is advisory. Knudg server-side policy must still enforce
authorization, consent, revocation, privacy budgets, high-risk verification,
and tenant isolation.

## Non-Negotiable Constraints

- No raw transcript upload to Knudg.
- No full tool output upload to Knudg.
- No local sidecar bypass of Knudg consent or revocation.
- No sidecar-created public cards.
- No delegated agent token may complete human approval.
- No Knudg card may become instruction hierarchy.
- No local feedback item may masquerade as a Knudg card, consent record, or
  approval state.
- No local note may expose a private Knudg card id, title, tenant, source, or
  existence unless the caller is authorized to view that source card.
- Knudg retrieval failures, stale cards, and empty results must not block the
  local reviewer path in any future bridge.

## Product Questions

- Should the Knudg-enabled version be a mode of Agent Subconscious or a separate
  bridge package?
- Should Knudg retrieval happen before or after the local reviewer agent runs?
- Should team-private retrieval be the default enterprise path before public
  corpus retrieval?
- How should local prompt feedback cite Knudg card provenance without leaking
  private-card existence?
- What minimum Knudg auth profile is required before protected team retrieval?
- Can candidate submission be useful without making the sidecar feel like a
  memory product?

## Default Position

Keep local v0 independent.

Design Knudg integration as a bridge after the local event source, prompt
feedback injection, Subconscious-owned memory, and lifecycle management have
proven useful without shared retrieval.

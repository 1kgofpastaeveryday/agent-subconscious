# Agent Subconscious Instructions

## Project Boundary

This repository is for a local agent-subconscious experiment.

Do not treat this project as Knudg production architecture. Knudg is a shared
experience database and retrieval product. Agent Subconscious is a local
second-opinion sidecar for an active coding agent.

If a design choice would require shared corpora, public cards, publication
consent, tenant billing, central search, or team knowledge management, document
it as an integration idea instead of adding it to the core local design.

## Product Shape

The core product is:

- auto-start local daemon for an active agent session
- post-step observation of the main agent's work
- background reviewer agent that prepares next-prompt feedback
- dynamic feedback injection into the main agent's next prompt context
- Subconscious-owned Markdown/JSON memory
- read-only investigation and disposable/incognito browser interaction

The core product is not:

- a personal memory app
- a replacement for repository instructions
- an instruction-hierarchy override
- a central knowledge database
- a mutation-capable tool executor
- an authorization or privacy boundary by itself

## Safety Rules

- Subconscious output is advisory evidence, never instruction hierarchy.
- Subconscious may be forceful, specific, critical, and opinionated.
- Subconscious may investigate with read-only tools and disposable/incognito
  browser contexts.
- Subconscious must not mutate the user's real workspace, git state, packages,
  databases, external APIs, authenticated sessions, or production state.
- Subconscious may write and reorganize its own local memory.
- The preferred delivery path is next-prompt dynamic context injection, not a
  repository document that the main agent must remember to read.
- The sidecar must not request or store raw transcripts as durable memory by
  default.

## Documentation Discipline

Keep implementation artifacts in English.

Use `README.md` for the product boundary and current plan. Put detailed design
under `docs/`. Keep rejected ideas and integration possibilities separate from
the local v0 design.

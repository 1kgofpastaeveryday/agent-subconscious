# Adversarial Design Review - 2026-05-15

## Review Configuration
- Date: 2026-05-15
- Project: <workspace>
- Focus: General
- Tier: mid
- Reviewers: 5 total
  - Codex model(s): gpt-5.5 lanes: Attack Surface, Production Stress, Assumption Challenger, Implementation Reality, Coherence Auditor
  - External Claude: disabled
  - Execution mode: subagent lanes
  - Artifact directory: <workspace>\<local-review-artifact>
  - Live QA: not requested
- Rounds: 1
- Review targets: see <workspace>\<local-review-artifact>

## Scoring Summary

| Reviewer | Correctness | Completeness | Implementability | Resilience | Weighted Avg | Binding? |
|----------|------------:|-------------:|-----------------:|-----------:|-------------:|:--------:|
| Attack Surface | 5 | 5 | 5 | 4 | 4.875 | yes |
| Production Stress | 6 | 5 | 6 | 4 | 5.500 | yes |
| Assumption Challenger | 6 | 5 | 4 | 5 | 5.125 | yes |
| Implementation Reality | 4 | 3 | 5 | 4 | 4.000 | yes |
| Coherence Auditor | 5 | 5 | 4 | 6 | 4.875 | yes |
| **Average** | **5.2** | **4.6** | **4.8** | **4.6** | **4.875** | yes |

PASS/FAIL: FAIL. One binding lane scored an axis <= 3.

## Lane Execution Status

| Lane | Execution mode | Status | Raw output | Failure reason |
|------|----------------|--------|------------|----------------|
| Attack Surface | subagent | valid | <local-review-artifact> | n/a |
| Production Stress | subagent | valid | <local-review-artifact> | n/a |
| Assumption Challenger | subagent | valid | <local-review-artifact> | n/a |
| Implementation Reality | subagent | valid | <local-review-artifact> | n/a |
| Coherence Auditor | subagent | valid | <local-review-artifact> | n/a |

## Cross-Lane Convergence

Issues identified by 3+ reviewers:

| Issue | Reviewers | Tier | Confidence |
|-------|-----------|------|------------|
| Installed hook path cannot become operational without fixture/debug assumptions | Attack Surface, Assumption Challenger, Implementation Reality, Coherence Auditor | Tier 1/2 | HIGH |
| Daemon lifecycle/status contract diverges from implementation | Production Stress, Implementation Reality, Coherence Auditor | Tier 1/2 | HIGH |

Issues identified by 2 reviewers:

| Issue | Reviewers | Tier | Confidence |
|-------|-----------|------|------------|
| Hook command relies on ambient python/python3 resolution | Attack Surface, Implementation Reality | Tier 1/2 | HIGH/MEDIUM |
| Feedback/delivery semantics are stricter or less observable than docs imply | Assumption Challenger, Implementation Reality | Tier 2 | MEDIUM/HIGH |
| Tool/browser safety remains policy-only | Attack Surface, Implementation Reality | Tier 3 | MEDIUM/HIGH |

## Tier 1 - Immediate Fix Required
- Installed hook path cannot become operational without a real host attestation/readiness path.
- Hook command trusts ambient PATH for interpreter resolution.
- Prompt hooks can block behind an unbounded shared state lock.
- Queue processing is unbounded under the same hook lock.
- Durable daemon status conflicts with the normative daemon contract.

## Tier 2 - Decide Before Implementation
- Main-agent identity, host auth readiness, and delivery acknowledgement are undecided host-contract questions.
- Feedback verifier rejects documented useful feedback shapes.
- Daemon lifecycle implementation lacks sessions, lock ownership/timeout, protocol/generation gates, and cleanup.
- Plugin install/update is not crash-safe or concurrency-safe.

## Tier 3 - Future Risk
- Tool/browser read-only policy lacks adapter ABI/tests.
- Memory purge/backend opt-in workflows need operator flows.
- Prompt wrapper text has multiple canonical-looking variants.

## Raw Reviewer Outputs

### attack
## Tier 1 - Immediate Fix Required

**AS-1 / Hook command trusts ambient PATH for code execution**  
**Confidence:** HIGH  
**Doc(s):** `plugins/agent-subconscious/hooks/hooks.json` lines 10, 22, 34; `plugins/agent-subconscious/hooks/hooks.windows.json` lines 10, 22, 34  
**Issue:** The installed hook command runs `python3` / `python` by name. The trusted hook hash covers the command string, not the resolved interpreter path. A PATH hijack or environment drift can make every hook execute an attacker-controlled interpreter before validation runs.  
**Evidence:** Hook entries execute `subconscious_hook.py` through the plugin-root variable.
**Recommendation:** At install time, resolve and pin an absolute interpreter path, record its executable identity, and have doctor fail closed if the interpreter path or digest changes.  
**Sources:** [OWASP LLM01 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [OpenAI agent builder safety](https://developers.openai.com/api/docs/guides/agent-builder-safety)

**AS-2 / Main-agent trust root is either absent or bypassed by fixture controls**  
**Confidence:** HIGH  
**Doc(s):** `agent_subconscious/codex_hook_adapter.py` lines 47-50, 83-95, 121-135; `agent_subconscious/hook_probe.py` lines 695-700; `docs/codex-hooks-v0.md` lines 169-186  
**Issue:** The real adapter has no production main-agent attestation. It rejects real payloads unless `SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT=1`, then self-mints host attestations signed by the same fixture signing path that can fall back to a fixed test key.  
**Evidence:** `validate_real_codex_payload` requires the assume-main-agent env var; `host_attestation` hardcodes `agent_role: "main"`; `fixture_signing_key` returns `agent-subconscious-fixture-only-key` when the allow-fixture env var is set.  
**Recommendation:** Remove fixture trust from the installed runtime path. Require a host-provided, version-pinned attestation or a daemon-issued one-shot bootstrap secret with CSPRNG capability tokens.  
**Sources:** [OWASP LLM01 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [OpenAI prompt injection guidance](https://openai.com/safety/prompt-injections/)

## Tier 2 - Decide Before Implementation

**AS-3 / Authenticated control plane is specified but the spike normalizes direct file trust**  
**Confidence:** MEDIUM  
**Doc(s):** `docs/contracts.md` lines 239-299; `agent_subconscious/hook_probe.py` lines 506-535, 742-826; `plugins/agent-subconscious/bin/subconscious_hook.py` lines 91-123  
**Issue:** The contract requires per-session capability tokens, scoped permissions, nonce/idempotency, and authenticated local IPC or loopback constraints. The implementation currently uses shared state files and JSONL.  
**Recommendation:** Label the file protocol as fixture-only, then implement an IPC/RPC boundary with scoped CSPRNG capabilities before MVP.  
**Sources:** [OpenAI agent builder safety](https://developers.openai.com/api/docs/guides/agent-builder-safety), [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

**AS-4 / Windows state directory lacks owner-only permission enforcement**  
**Confidence:** MEDIUM  
**Doc(s):** `agent_subconscious/hook_probe.py` lines 214-245, 274-284; `docs/contracts.md` lines 263-265, 267-299  
**Issue:** On non-Windows, `prepare_state_dir` applies `0700` and owner checks; on Windows it only checks directory existence and reparse/symlink status.  
**Recommendation:** Add Windows ACL checks for owner, inherited broad write permissions, and reparse points on relevant parents.  
**Sources:** [OWASP LLM01 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

## Tier 3 - Future Risk

**AS-5 / Tool and browser safety remain policy-only, with no adapter implementation target**  
**Confidence:** MEDIUM  
**Doc(s):** `docs/design.md` lines 290-307; `docs/contracts.md` lines 332-351; `docs/open-decisions.md` lines 136-145  
**Issue:** The design says read-only adapters and disposable browser profiles are required, but there is no concrete adapter interface or enforcement test target.  
**Recommendation:** Define the adapter ABI before adding a real reviewer: allowed operation enums, canonical root checks, git subcommand allowlists, disposable browser profile construction, DNS/redirect private-range checks, and golden denial tests.  
**Sources:** [OpenAI agent builder safety](https://developers.openai.com/api/docs/guides/agent-builder-safety), [OpenAI prompt injection guidance](https://openai.com/safety/prompt-injections/)

## Scores

| Axis | Score | Rationale |
|---|---:|---|
| Correctness | 5 | Core security intent is coherent, but current trust establishment is fixture-only and unsafe if enabled. |
| Completeness | 5 | Major contracts exist, but production attestation, IPC auth, Windows ACLs, and adapter enforcement are not complete. |
| Implementability | 5 | Developers can build the spike, but would need security design decisions before a real MVP. |
| Resilience | 4 | Fails closed in many paths, but ambient PATH execution and fixture-key bypass are brittle attack surfaces. |

## Lane Verdict

FAIL: the current target has immediate hook execution and trust-root issues that must be fixed before any real auto-start or prompt-injection use.


### production-stress
## Tier 1 - Immediate Fix Required

- **[PS-1] Prompt hooks can block behind an unbounded state lock**  
  **Confidence:** HIGH  
  **Doc(s):** `agent_subconscious/hook_probe.py` `StateFileLock` / `locked_state`; `plugins/agent-subconscious/hooks/hooks.json`; `docs/design.md` Auto-Start And Process Lifecycle  
  **Issue:** `UserPromptSubmit`, `Stop`, daemon processing, and daemon startup all share a blocking file lock with no timeout, owner record, stale-lock handling, or degraded fallback while waiting.  
  **Recommendation:** Replace blocking lock acquisition in prompt-path hooks with bounded try-lock/retry behavior, stale-owner metadata, and immediate fail-closed output when the budget is exceeded.  
  **Sources:** [systemd.service Restart/Timeout/Watchdog semantics](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html), [Python subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html)

- **[PS-2] Queue processing is unbounded and runs under the same hook lock**  
  **Confidence:** HIGH  
  **Doc(s):** `agent_subconscious/daemon.py` `process_once`; `agent_subconscious/hook_probe.py` `read_observations_by_id`; `docs/contracts.md` ControlPlane  
  **Issue:** The daemon scans full JSONL state and processes every eligible observation while holding the shared lock. There is no per-iteration budget, queue-depth cap, chunking, or backpressure.  
  **Recommendation:** Add bounded leases and process at most N events or M milliseconds per daemon tick. Add queue depth and state-size limits with degraded readiness.  
  **Sources:** [systemd.service WatchdogSec/Restart](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html), [Python subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html)

## Tier 2 - Decide Before Implementation

- **[PS-3] Active sessions do not prevent daemon idle exit**  
  **Confidence:** HIGH  
  **Doc(s):** `agent_subconscious/daemon.py` `run_loop`; `agent_subconscious/hook_probe.py` capability bootstrap; `docs/design.md` Auto-Start And Process Lifecycle  
  **Issue:** The implementation has capability records but no session registry, no unregister path, and exits solely from activity timing. Quiet but active sessions will lose the daemon.  
  **Recommendation:** Implement `SessionRegistration` state with last-seen and unregister/expiry semantics, and base idle shutdown on no live sessions plus no work.  
  **Sources:** [systemd.service Restart settings](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html)

- **[PS-4] Corrupt append-only state can disable processing instead of quarantining**  
  **Confidence:** MEDIUM  
  **Doc(s):** `agent_subconscious/hook_probe.py` `read_jsonl`; `agent_subconscious/daemon.py` `run_loop`; `tests/test_plugin_package.py`; `docs/contracts.md`  
  **Issue:** Strict JSONL reads raise on malformed lines, and the daemon catches the exception only as a loop error. There is no quarantine, truncation of a partial last record, dead-letter, or repair path.  
  **Recommendation:** Add per-file recovery: validate on startup, quarantine malformed suffixes or records, preserve redacted diagnostics, and continue processing unaffected records.  
  **Sources:** [Python os.replace atomic replacement docs](https://docs.python.org/3/library/os.html#os.replace), [atomicwrites durability notes](https://python-atomicwrites.readthedocs.io/)

- **[PS-5] Plugin install/update is not crash-safe or concurrency-safe**  
  **Confidence:** MEDIUM  
  **Doc(s):** `agent_subconscious/codex_plugin_setup.py` `copy_plugin_to_cache`, `atomic_write_text`  
  **Issue:** Plugin installation renames the current cache to `.previous`, renames the temp tree into place, then deletes the backup without locking or rollback.  
  **Recommendation:** Add an install lock, unique temp directories, rollback on failure, and fsync file plus parent directory where supported.  
  **Sources:** [Python os.replace docs](https://docs.python.org/3/library/os.html#os.replace)

## Tier 3 - Future Risk

- **[PS-6] Daemon observability is mostly hidden from the user path**  
  **Confidence:** MEDIUM  
  **Doc(s):** `plugins/agent-subconscious/bin/subconscious_hook.py`; `agent_subconscious/daemon.py`  
  **Issue:** The daemon is spawned with stdout/stderr redirected to `DEVNULL`, while loop failures emit diagnostics to stderr and only the last error code reaches status.  
  **Recommendation:** Write bounded redacted diagnostics to a rotating state-owned log or status ring buffer, expose them through doctor/status, and keep hook stderr for immediate startup failures only.  
  **Sources:** [systemd.service failure/restart documentation](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html)

## Scores

| Axis | Score | Rationale |
|---|---:|---|
| Correctness | 6 | Core fail-closed paths exist, but lifecycle and recovery semantics diverge from the documented contract. |
| Completeness | 5 | Major operational decisions are documented, but bounded lock waits, queue budgets, session liveness, and repair flows are not implemented. |
| Implementability | 6 | The spike is buildable, but implementers still need to invent several production-critical mechanisms. |
| Resilience | 4 | Current behavior is fragile under lock contention, large queues, malformed state, crashes during install, and hidden daemon failures. |

## Lane Verdict

FAIL: the design and spike are not production-resilient until lock budgets, backpressure, session lifecycle, state repair, and crash-safe setup are fixed.


### assumption-challenger
## Tier 1 - Immediate Fix Required

## Tier 2 - Decide Before Implementation

**AC-1/Main-agent identity is still an environment assumption**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` First Launch Setup; `docs/open-decisions.md` D-002; `docs/codex-hooks-v0.md`; `agent_subconscious/codex_hook_adapter.py`; `plugins/agent-subconscious/hooks/*.json`  
**Issue:** The design requires proof that events come from the main agent, not subagents/reviewer lanes, but the implementation path is an opt-in environment/debug assumption.  
**Recommendation:** Define the actual Codex host signal or attestation contract that distinguishes main-agent sessions, and make plugin readiness fail closed until that signal is fixture-proven.  
**Sources:** [ISO/IEC/IEEE 29148](https://www.iso.org/standard/72089.html), [ADR guidance](https://adr.github.io/)

**AC-2/Host auth readiness is modeled but cannot become ready**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` First Launch Setup; `docs/open-decisions.md` D-002; `docs/contracts.md` HostSetupState; `agent_subconscious/codex_plugin_setup.py`  
**Issue:** The docs require distinguishing plugin login/setup states, but the code hard-codes `auth_ready = False`, so `host_surface_ready` cannot become true.  
**Recommendation:** Add a decision record for the real login-readiness source and update `HostSetupState` modes so unknown/unobservable is not reported as definitive `login_required`.  
**Sources:** [ADR guidance](https://adr.github.io/), [ISO/IEC/IEEE 29148](https://www.iso.org/standard/72089.html)

**AC-3/Delivery success semantics depend on an unproven host behavior**  
**Confidence:** MEDIUM  
**Doc(s):** `docs/contracts.md` DeliveryCursor; `docs/codex-hooks-v0.md` UserPromptSubmit; `agent_subconscious/hook_probe.py`  
**Issue:** The design says feedback is marked emitted only after successful `additionalContext` emission, but the code can only observe stdout write/flush from the hook process.  
**Recommendation:** Define the observable condition for successful selected `additionalContext` emission. If no host acknowledgement exists, rename `emitted` to `hook_output_written` or keep `claimed` until fixture proof.  
**Sources:** [ISO/IEC/IEEE 29148](https://www.iso.org/standard/72089.html), [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

**AC-4/MVP success depends on evaluation thresholds that are not decided**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` Evaluation; `README.md` MVP  
**Issue:** The design says MVP passes based on useful feedback, distracting feedback, and behavior change, but the thresholds are deferred.  
**Recommendation:** Add concrete evaluation gates before MVP-A/B: sample size, useful-feedback rate, distracting-feedback ceiling, latency ceiling, and required failure-case fixtures.  
**Sources:** [Microsoft Human-AI Interaction Guidelines](https://www.microsoft.com/en-us/research/articles/guidelines-for-human-ai-interaction-eighteen-best-practices-for-human-centered-ai-design/), [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

## Tier 3 - Future Risk

**AC-5/User control over memory and purge lacks an operator workflow**  
**Confidence:** MEDIUM  
**Doc(s):** `docs/design.md` Memory; `docs/contracts.md` PurgeRun; `README.md` MVP  
**Issue:** The docs state users can inspect/edit memory and purge local state, but they do not define the user-facing command, confirmation model, or conflict behavior.  
**Recommendation:** Define `subconscious status`, `subconscious memory path`, and `subconscious purge` workflows, including daemon locking, confirmation, and recovery from partial purge.  
**Sources:** [Microsoft Human-AI Interaction Guidelines](https://www.microsoft.com/en-us/research/articles/guidelines-for-human-ai-interaction-eighteen-best-practices-for-human-centered-ai-design/), [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

**AC-6/Backend opt-in is a record schema, not a consent workflow**  
**Confidence:** MEDIUM  
**Doc(s):** `docs/design.md` Backend And Auth; `docs/contracts.md` BackendWorkspaceConfig  
**Issue:** The design requires explicit workspace opt-in and outbound field inventory before live backend calls, but it does not define how a user reviews, approves, changes, or revokes that inventory.  
**Recommendation:** Add a backend opt-in workflow with displayed field inventory, provider/model policy, local-only fallback, revocation, and audit record.  
**Sources:** [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework), [Microsoft Human-AI Interaction Guidelines](https://www.microsoft.com/en-us/research/articles/guidelines-for-human-ai-interaction-eighteen-best-practices-for-human-centered-ai-design/)

## Scores

| Axis | Score | Rationale |
|---|---:|---|
| Correctness | 6 | Core boundaries are mostly consistent, but readiness and delivery states encode assumptions that are not actually proven. |
| Completeness | 5 | Major workflows remain undecided: main-agent proof, host auth readiness, evaluation gates, backend opt-in, and purge UX. |
| Implementability | 4 | A developer can build the spike, but productionizing the accepted v0 path requires guessing at host signals and user workflows. |
| Resilience | 5 | Failure-closed intent is strong, but several recovery and acknowledgement semantics depend on unavailable or undefined host behavior. |

## Lane Verdict

FAIL because multiple accepted v0 decisions still rely on unproven host/auth/delivery assumptions that would block implementation beyond the fixture spike.


### implementation-reality
## Tier 1 - Immediate Fix Required

**IR-1 / Installed Codex hook path cannot become operational**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` First Launch Setup / Prompt Injection; `docs/codex-hooks-v0.md` Supported Modes; `agent_subconscious/codex_plugin_setup.py`; `agent_subconscious/codex_hook_adapter.py`; `plugins/agent-subconscious/hooks/hooks.windows.json`; `tests/test_plugin_package.py`  
**Issue:** The non-debug installed hook path has no documented or implemented route to readiness. `doctor()` hardcodes `auth_ready = False`, the adapter rejects real Codex payloads unless `SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT=1`, and the shipped hook commands do not pass debug flags or set that environment.  
**Recommendation:** Add a real supported-mode proof path before treating the hook spike as installable: foreground doctor must verify actual Codex hook support/auth state, the installed hook must receive a non-fixture host attestation or fail closed with a clear unsupported state, and tests must cover the installed hook command without debug flags/env.  
**Sources:** [Python subprocess docs](https://docs.python.org/3/library/subprocess.html)

## Tier 2 - Decide Before Implementation

**IR-2 / Feedback verifier rejects documented useful feedback shapes**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` Feedback Semantics; `agent_subconscious/hook_probe.py`; `tests/test_daemon.py`  
**Issue:** The verifier is much stricter than the contract. The docs allow categories such as `intent mismatch` and quoted evidence/recommendation data, but `safe_feedback_body()` only allows a small hardcoded prefix list and a lower-case word-only sentence pattern.  
**Recommendation:** Replace the toy body grammar with a structured `FeedbackItem.body` schema or renderer fields, then test every documented allowed category plus adversarial rejected bodies.  
**Sources:** [Python os.replace docs](https://docs.python.org/3/library/os.html#os.replace)

**IR-3 / Daemon lifecycle implementation does not match the v0 lifecycle contract**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` Auto-Start And Process Lifecycle; `docs/open-decisions.md` D-004; `agent_subconscious/daemon.py`; `tests/test_daemon.py`  
**Issue:** The docs require startup lock owner/timeout, session registration/unregistration, queue-progress readiness, protocol versioning, generation identity, idle exit only when no sessions remain, and identity-based cleanup. The implementation has a status snapshot and heartbeat, but no session registry, no cleanup path, no protocol/generation compatibility gate, and idle timeout is based on activity rather than registered sessions.  
**Recommendation:** Either label the daemon as fixture-only and exclude lifecycle claims, or implement the contract fields and add tests for every D-004 proof item.  
**Sources:** [Python msvcrt locking docs](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking), [Python os.replace docs](https://docs.python.org/3/library/os.html#os.replace)

**IR-4 / Hook commands use ambiguous interpreter resolution on the installed path**  
**Confidence:** MEDIUM  
**Doc(s):** `plugins/agent-subconscious/hooks/hooks.json`; `plugins/agent-subconscious/hooks/hooks.windows.json`; `plugins/agent-subconscious/bin/subconscious_hook.py`; `tests/test_plugin_package.py`  
**Issue:** The installed hook commands rely on `python3` or `python` being resolved correctly by Codex's hook environment. Tests use `sys.executable`, but the shipped hook files do not.  
**Recommendation:** During install, render hook commands with the verified interpreter path or a supported launcher path, and add tests that the installed hook command can execute from a Codex-like environment with a minimal `PATH`.  
**Sources:** [Python subprocess docs](https://docs.python.org/3/library/subprocess.html)

## Tier 3 - Future Risk

**IR-5 / Browser and read-only tool policy is documented but has no implementation seam or tests**  
**Confidence:** HIGH  
**Doc(s):** `docs/design.md` Tool Permissions; `docs/contracts.md` BrowserPolicy; tests  
**Issue:** The design promises adapter-enforced read-only file/git/browser/network behavior, disposable browser profiles, DNS/redirect checks, and denial of click/type outside disposable fixtures. None of the target implementation files define those adapters, and the test suite has no browser/network/tool-boundary tests.  
**Recommendation:** Keep these claims blocked from MVP acceptance until adapter interfaces and denial fixtures exist.  
**Sources:** [Python subprocess docs](https://docs.python.org/3/library/subprocess.html)

## Scores

| Axis | Score | Rationale |
|---|---:|---|
| Correctness | 4 | Core installed hook readiness contradicts the implementation path; verifier rejects documented feedback examples. |
| Completeness | 3 | Setup/auth, lifecycle, adapters, and real host fixture proof remain missing or fixture-only. |
| Implementability | 5 | Thin spike code is understandable, but developers must still decide the real auth/attestation, lifecycle, and adapter contracts. |
| Resilience | 4 | Some fail-closed and atomic-write behavior exists, but lifecycle cleanup, session handling, and tool-boundary resilience are not implemented. |

## Lane Verdict

FAIL: the current artifacts are a useful fixture spike, but the installed hook path and several promised v0 contracts cannot work as documented.


### coherence-auditor
## Tier 1 - Immediate Fix Required

ID: F1 / Durable daemon state contract conflicts with implemented state file  
Confidence: HIGH  
Doc(s): `docs/contracts.md`, `agent_subconscious/daemon.py`, `tests/test_daemon.py`, `tests/test_plugin_package.py`  
Issue: `docs/contracts.md` makes durable record fields normative and defines `DaemonState`, but the implemented `daemon_status.json` uses a different schema and lifecycle vocabulary. Tests now lock in the non-contract schema.  
Recommendation: Either rename this as an explicitly non-contract spike status file and document it separately, or align the implementation/tests to `DaemonState`.  
Sources: [Google Cloud ADR guidance](https://docs.cloud.google.com/architecture/architecture-decision-records), [ISO/IEC/IEEE 12207 via NASA](https://swehb.nasa.gov/download/attachments/16450347/12207-2008.pdf?api=v2)

## Tier 2 - Decide Before Implementation

ID: F2 / Installed hook package has no coherent path from real Codex hook to enabled observation  
Confidence: HIGH  
Doc(s): `docs/codex-hooks-v0.md`, `docs/design.md`, `agent_subconscious/codex_hook_adapter.py`, `plugins/agent-subconscious/hooks/*.json`, `tests/test_plugin_package.py`  
Issue: The docs require main-agent/subagent distinction proof before observation/injection, but the installed hooks call `with-daemon` without debug flags or any documented real attestation source.  
Recommendation: Decide whether this package is fixture-only or a real thin spike. If fixture-only, label install/doctor/hook docs that way. If real, specify the accepted host-provided attestation.  
Sources: [Microsoft ADR guidance](https://learn.microsoft.com/en-ie/azure/well-architected/architect-role/architecture-decision-record), [Google Cloud ADR guidance](https://docs.cloud.google.com/architecture/architecture-decision-records)

ID: F3 / Setup readiness model defines states the implementation cannot reach  
Confidence: MEDIUM  
Doc(s): `docs/contracts.md`, `docs/design.md`, `agent_subconscious/codex_plugin_setup.py`, `tests/test_codex_plugin_setup.py`  
Issue: `HostSetupState` and design docs describe detecting Codex plugin login as ready or required, but `doctor()` hardcodes `auth_ready = False`, so `ready`/`host_surface_ready` cannot be reached.  
Recommendation: Split fixture readiness from login readiness, or document that v0 has no implemented login-ready detector yet and therefore cannot claim `ready`.  
Sources: [Microsoft ADR guidance](https://learn.microsoft.com/en-ie/azure/well-architected/architect-role/architecture-decision-record), [ISO/IEC/IEEE 12207 via NASA](https://swehb.nasa.gov/download/attachments/16450347/12207-2008.pdf?api=v2)

ID: F4 / Thin hook spike wording conflicts with hook bundle auto-starting a daemon  
Confidence: MEDIUM  
Doc(s): `user-said.md`, `README.md`, `docs/design.md`, `plugins/agent-subconscious/bin/subconscious_hook.py`, `tests/test_plugin_package.py`  
Issue: User intent says not to jump straight to daemon/backend/browser/memory before hook surface is understood, but the plugin hook bundle uses `with-daemon` for all three hook events and tests assert Stop starts the daemon path.  
Recommendation: Clarify whether this daemon is a disposable fixture daemon inside the hook spike, or move daemon auto-start out of the default plugin hooks until the hook fixture proof is accepted.  
Sources: [Google Cloud ADR guidance](https://docs.cloud.google.com/architecture/architecture-decision-records), [ISO/IEC/IEEE 12207 via NASA](https://swehb.nasa.gov/download/attachments/16450347/12207-2008.pdf?api=v2)

## Tier 3 - Future Risk

ID: F5 / Prompt wrapper text has multiple canonical-looking variants  
Confidence: MEDIUM  
Doc(s): `docs/design.md`, `docs/contracts.md`, `docs/codex-hooks-v0.md`, `agent_subconscious/hook_probe.py`, `tests/test_hook_probe.py`  
Issue: The security-sensitive advisory wrapper is described with one phrase in docs and implemented with another.  
Recommendation: Pick one canonical rendered wrapper in `contracts.md` and make design, hook contract, implementation, and tests reference it exactly.  
Sources: [Microsoft ADR guidance](https://learn.microsoft.com/en-ie/azure/well-architected/architect-role/architecture-decision-record), [Google Cloud ADR guidance](https://docs.cloud.google.com/architecture/architecture-decision-records)

## Scores

| Axis | Score | Rationale |
|---|---:|---|
| Correctness | 5 | Core product boundaries are consistent, but durable daemon state and hook readiness semantics conflict with normative contracts. |
| Completeness | 5 | Major safety and lifecycle contracts exist, but several implemented spike paths lack a clear accepted source of truth. |
| Implementability | 4 | The installed hook path appears intentionally fail-closed without a documented route to real enablement. |
| Resilience | 6 | Fail-closed behavior is strong, but schema drift and ambiguous readiness states will make recovery and diagnostics brittle. |

## Lane Verdict

FAIL: the design/code set has unresolved source-of-truth conflicts around daemon state, real hook enablement, and setup readiness that should be resolved before treating the spike as coherent.


## Statistics
- Total findings: 27
- Tier 1: 7
- Tier 2: 14
- Tier 3: 6
- Cross-lane convergence (3+): 2
- Cross-lane convergence (2): 3
- Average weighted score: 4.875/10

